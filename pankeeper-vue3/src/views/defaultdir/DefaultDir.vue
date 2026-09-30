<script setup lang="ts">
/* =====================================================================
 * 转存配置（默认目录）—— 原型 parts/page-default-dir.html 的 Vue3 还原。
 * 「快速转存」的下拉项就来自这里，按 网盘 tab × 账号 两级隔离。
 * dd- 前缀私有样式在本文件 <style scoped>；数据直接读写 ddStore（内存态），
 * 增删改走 api/modules/dd.ts（mockDelay 包一层，后端就绪后只换实现）。
 * ===================================================================== */
import { computed, onMounted, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { ddStore, ddFind } from '@/api/mock/dd'
import { DD_MEDIA, MAIN_ORDER, DRIVE_META } from '@/api/mock/meta'
import { DD_TREES } from '@/api/mock/tree'
import { accountStore } from '@/api/mock/accounts'
import { getFilesList } from '@/api/modules/files'
import { USE_MOCK } from '@/api/http'
import { saveDdItem, deleteDdItem, setDefaultDir, listDdItems, listQmsPaths, listStrmPaths } from '@/api/modules/dd'
import type { DdItem, DdQmsPath, DdStrmPath, MainDriveType, TreeNode } from '@/types/model'

/* ===== 列表态 ===== */
const active = ref<MainDriveType>('baidu')

/** 当前 tab 的行，按 sort 升序（数字越小在快速转存下拉里越靠前） */
const rows = computed<DdItem[]>(() =>
  ddStore.items.filter((x) => x.type === active.value).sort((a, b) => (a.sort || 0) - (b.sort || 0)),
)

function countOf(t: MainDriveType): number {
  return ddStore.items.filter((x) => x.type === t).length
}

/** 账号 id → 展示名（真实账号：别名/昵称；查不到时兜底显示原始值） */
function accLabel(acc: string): string {
  const a = accountStore.accounts.find((x) => String(x.id) === String(acc))
  return a ? a.alias || a.nickname || `${DRIVE_META[a.type].name}#${a.id}` : acc || '—'
}

function switchTab(t: MainDriveType) {
  active.value = t
}

/* ===== 行内操作 ===== */
async function setDefault(it: DdItem) {
  // api 内部已保证同网盘 + 同账号下唯一
  await setDefaultDir(it.id)
  message.success(`已把「${it.name}」设为默认目录`)
}

async function doDelete(it: DdItem) {
  await deleteDdItem(it.id)
  message.success(`已删除「${it.name}」`)
}

/* ===== 新增 / 编辑弹窗 ===== */
const modalOpen = ref(false)
const editingId = ref<number | null>(null) // null = 新增
const fName = ref('')
const fType = ref<MainDriveType>('baidu')
const fAcc = ref('')
const fPath = ref('')
const fSort = ref(1)
const fQmsOn = ref(false)
const fQmsId = ref<number | undefined>(undefined)
const fStrmId = ref(0) // 0 = 不生成 STRM（select 没法用 null 当选项值，用 0 哨兵）
const treeOpen = ref(false)

const typeOptions = MAIN_ORDER.map((k) => ({ value: k, label: DRIVE_META[k].full }))
/** 所属账号：真实账号列表（网盘连接页配的），不是 mock 的假号 */
const accOptions = computed(() =>
  accountStore.accounts
    .filter((a) => a.type === fType.value)
    .map((a) => ({ value: String(a.id), label: a.alias || a.nickname || `${DRIVE_META[a.type].name}#${a.id}` })),
)
/** QMS 刮削目录 / STRM 同步路径：打开弹窗时从 QMS 拉真实列表 */
const qmsPaths = ref<DdQmsPath[]>([])
const strmPaths = ref<DdStrmPath[]>([])
// QMS 刮削目录下拉：#id · 类型 · 路径
const qmsOptions = computed(() =>
  qmsPaths.value.map((p) => ({ value: p.id, label: `#${p.id} · ${DD_MEDIA[p.media_type as 'tv'] || p.media_type} · ${p.source_path}` })),
)
// STRM 下拉：首项「不生成」对应哨兵 0
const strmOptions = computed(() => [
  { value: 0, label: '不生成 STRM' },
  ...strmPaths.value.map((p) => ({ value: p.id, label: `#${p.id} · ${p.remote_path}` })),
])

// 开着 QMS 却没选目录时，自动选中第一项（原型 ddRenderQms 同款兜底）
watch(fQmsOn, (on) => {
  if (on && fQmsId.value == null && qmsPaths.value.length) fQmsId.value = qmsPaths.value[0].id
})

async function loadQmsStrmPaths() {
  // QMS 未启用/连不上时静默置空：下拉显示"暂无可选"，不挡住表单其他项
  qmsPaths.value = await listQmsPaths().catch(() => [])
  strmPaths.value = await listStrmPaths().catch(() => [])
}

function openEditor(id: number | null) {
  editingId.value = id
  const it = id != null ? ddFind(id) : null
  fType.value = it ? it.type : active.value
  const firstAcc = accountStore.accounts.find((a) => a.type === fType.value)
  fAcc.value = it ? it.account : String(firstAcc?.id ?? '')
  if (!it && !firstAcc) message.warning(`该网盘还没有已连接的账号，请先到「网盘连接」配置`)
  fPath.value = it ? it.path : ''
  fName.value = it ? it.name : ''
  // 新增时排序默认排到该网盘现有最大值 + 1（原型如此）
  fSort.value = it
    ? it.sort
    : ddStore.items.filter((x) => x.type === fType.value).reduce((m, x) => Math.max(m, x.sort || 0), 0) + 1
  fQmsOn.value = it ? !!it.qms_on : false
  fQmsId.value = it?.qms_id ?? undefined
  fStrmId.value = it?.strm_id ?? 0
  expanded.value = new Set()
  // ⚠️ 必须重置树展开态与按钮文案，否则第二次打开显示「收起目录」、点一下反而把树收起来（原型踩过）
  treeOpen.value = false
  modalOpen.value = true
  loadQmsStrmPaths()
}

/** 切换网盘：账号跟随切到该网盘第一个，已选路径/树展开态作废（跨网盘路径无意义） */
function onTypeChange() {
  const firstAcc = accountStore.accounts.find((a) => a.type === fType.value)
  fAcc.value = String(firstAcc?.id ?? '')
  fPath.value = ''
  expanded.value = new Set()
  remoteRows.value = []
  fidByPath.value = {}
}

/* ===== 目录树（dd-tnode 结构，扁平化渲染：缩进 = 深度 × 18px，视觉与原型嵌套版一致） ===== */
interface FlatNode {
  path: string
  name: string
  depth: number
  hasKids: boolean
}
const expanded = ref(new Set<string>())

/* ===== 真实模式：远程目录（按需懒加载，fid 记在 path→fid 映射里） ===== */
const remoteRows = ref<FlatNode[]>([])
const remoteLoading = ref(false)
const fidByPath = ref<Record<string, string>>({})

async function loadRemoteRoot(force = false) {
  if (remoteLoading.value) return
  remoteLoading.value = true
  try {
    const items = await getFilesList(fType.value, '0', '/', force)
    const rows: FlatNode[] = []
    for (const it of items) {
      const p = '/' + it.name
      if (it.is_dir) fidByPath.value[p] = it.fid
      rows.push({ path: p, name: it.name, depth: 0, hasKids: it.is_dir })
    }
    remoteRows.value = rows
  } catch {
    remoteRows.value = []
  } finally {
    remoteLoading.value = false
  }
}

async function loadRemoteKids(parent: string) {
  const fid = fidByPath.value[parent]
  if (!fid) return
  try {
    const items = await getFilesList(fType.value, fid)
    const depth = parent.split('/').filter(Boolean).length
    const rows: FlatNode[] = []
    for (const it of items) {
      const p = parent === '/' ? '/' + it.name : parent + '/' + it.name
      if (it.is_dir) fidByPath.value[p] = it.fid
      rows.push({ path: p, name: it.name, depth: depth, hasKids: it.is_dir })
    }
    // 插到父节点之后（保持深度序）
    const list = remoteRows.value
    const idx = list.findIndex((r) => r.path === parent)
    list.splice(idx + 1, 0, ...rows)
  } catch {
    /* 加载失败：收起即可重试 */
  }
}

const flatTree = computed<FlatNode[]>(() => {
  if (!USE_MOCK) return remoteRows.value
  const out: FlatNode[] = []
  const walk = (nodes: TreeNode[], parent: string, depth: number) => {
    for (const nd of nodes) {
      // 原型拼路径规则：根层直接 /xxx，往下逐级接
      const full = parent === '/' ? '/' + nd.name : parent + '/' + nd.name
      const hasKids = !!(nd.kids && nd.kids.length)
      out.push({ path: full, name: nd.name, depth, hasKids })
      if (hasKids && expanded.value.has(full)) walk(nd.kids!, full, depth + 1)
    }
  }
  walk(DD_TREES[fType.value] || [], '/', 0)
  return out
})

async function toggleNode(p: string) {
  if (!USE_MOCK) {
    const s = new Set(expanded.value)
    if (s.has(p)) {
      s.delete(p)
      expanded.value = s
      return
    }
    s.add(p)
    expanded.value = s
    // 该节点子层还没拉过：拉一次（有 fid 才是真实目录）
    if (fidByPath.value[p] && !remoteRows.value.some((r) => r.path.startsWith(p + '/'))) {
      await loadRemoteKids(p)
    }
    return
  }
  const s = new Set(expanded.value)
  if (s.has(p)) s.delete(p)
  else s.add(p)
  expanded.value = s
}

function pickPath(p: string) {
  fPath.value = p
}

function toggleTree() {
  treeOpen.value = !treeOpen.value
  if (treeOpen.value && !USE_MOCK && remoteRows.value.length === 0) loadRemoteRoot()
}

/* ===== 进页面：拉真实转存配置 + QMS/STRM 路径列表（此前页面渲染的一直是 mock 假数据） ===== */
onMounted(async () => {
  await listDdItems().catch(() => {})
  loadQmsStrmPaths()
})

/* ===== 保存（校验全部不关弹窗，重名只在同网盘 + 同账号内拦截） ===== */
async function confirmEditor() {
  const name = fName.value.trim()
  if (!name) {
    message.error('请填写名称')
    return
  }
  if (!fPath.value) {
    message.error('请选择网盘路径')
    return
  }
  if (!fSort.value || fSort.value < 1) {
    message.error('排序请填正整数')
    return
  }
  if (!fAcc.value) {
    message.error('请选择所属账号')
    return
  }
  const dup = ddStore.items.some(
    (x) => x.type === fType.value && x.account === fAcc.value && x.name === name && x.id !== editingId.value,
  )
  if (dup) {
    message.error(`该账号下已有同名目录「${name}」`)
    return
  }
  if (fQmsOn.value && fQmsId.value == null) {
    message.error('联动 QMS 需选择整理目录')
    return
  }
  const qmsFields = {
    qms_on: fQmsOn.value,
    qms_id: fQmsOn.value && fQmsId.value != null ? fQmsId.value : null,
    strm_id: fQmsOn.value && fStrmId.value ? fStrmId.value : null, // 0 哨兵 → 不生成
  }
  // 目标账号下（排除自己）已有多少条 —— 新增时第一条自动成为该账号默认
  const beforeCount = ddStore.items.filter(
    (x) => x.type === fType.value && x.account === fAcc.value && x.id !== editingId.value,
  ).length

  if (editingId.value != null) {
    const it = ddFind(editingId.value)
    if (!it) return
    // 编辑换了账号且目标账号还是空的 → 顶上默认，保证「每账号唯一默认」不被搬家破坏
    const moved = it.type !== fType.value || it.account !== fAcc.value
    await saveDdItem({
      ...it,
      name,
      sort: fSort.value,
      path: fPath.value,
      account: fAcc.value,
      type: fType.value,
      is_default: moved ? beforeCount === 0 : it.is_default,
      ...qmsFields,
    })
    message.success(`已保存「${name}」`)
  } else {
    await saveDdItem({
      id: null, // 新建：后端自增分配 id，不再用 0 当"新建"魔法值
      type: fType.value,
      account: fAcc.value,
      sort: fSort.value,
      name,
      path: fPath.value,
      is_default: beforeCount === 0,
      ...qmsFields,
    })
    message.success(beforeCount === 0 ? `已新增「${name}」，并设为该账号默认` : `已新增「${name}」`)
  }
  // 编辑里换了网盘 → 跟着切到对应 tab（原型如此）
  active.value = fType.value
  modalOpen.value = false
}
</script>

<template>
  <div class="dd-wrap">
    <div class="dd-card">
      <!-- 卡头：标题跟随当前 tab，右侧新增 -->
      <div class="dd-cardhd">
        <div class="dd-headtt">
          <h2>{{ DRIVE_META[active].full }} · 转存配置</h2>
          <div class="dd-headdesc">
            给每个网盘配一套自己的「别名 → 路径」。配好后搜索结果里的「快速转存」
            可直接下拉选择，不必每次翻目录树。<br />
            配置按<b>账号</b>隔离 —— 同一网盘的不同账号互不影响。
          </div>
        </div>
        <div class="dd-headact">
          <a-button type="primary" @click="openEditor(null)">+ 新增目录</a-button>
        </div>
      </div>

      <!-- 胶囊 tab：贴在卡头下方、表头之上，与卡片同宽 -->
      <div class="dd-tabsbar">
        <div class="tabs">
          <div v-for="k in MAIN_ORDER" :key="k" :class="{ on: active === k }" @click="switchTab(k)">
            {{ DRIVE_META[k].full }}
            <span v-if="countOf(k)" class="dd-tabnum">{{ countOf(k) }}</span>
          </div>
        </div>
      </div>

      <!-- 表格：排序 / 名称+路径同格 / 所属账号 / 默认 / 操作（手机包横滑容器保列宽） -->
      <div v-if="rows.length" class="pk-hscroll">
        <table class="dd-table">
          <thead>
            <tr>
              <th class="dd-th" style="width: 52px">排序</th>
              <th class="dd-th">名称 / 网盘路径</th>
              <th class="dd-th" style="width: 170px">所属账号</th>
              <th class="dd-th" style="width: 110px">默认</th>
              <th class="dd-th" style="width: 150px; text-align: right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="it in rows" :key="it.id" class="dd-row">
              <td class="dd-td"><span class="dd-sort">{{ it.sort }}</span></td>
              <td class="dd-td">
                <span class="dd-name">{{ it.name }}</span>
                <span class="dd-path" :title="it.path">{{ it.path }}</span>
              </td>
              <td class="dd-td"><span class="dd-acc">{{ accLabel(it.account) }}</span></td>
              <td class="dd-td">
                <span v-if="it.is_default" class="dd-default-tag">默认</span>
                <button v-else class="dd-setdefault" type="button" @click="setDefault(it)">设为默认</button>
              </td>
              <td class="dd-td">
                <div class="dd-ops">
                  <button class="dd-op" type="button" @click="openEditor(it.id)">编辑</button>
                  <a-popconfirm
                    :title="`确定删除「${it.name}」？删除后「快速转存」将不再显示它。`"
                    ok-text="删除"
                    cancel-text="取消"
                    @confirm="doDelete(it)"
                  >
                    <button class="dd-op dd-op-del" type="button">删除</button>
                  </a-popconfirm>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="dd-empty">
        <b>还没有配置转存目录</b>
        给 {{ DRIVE_META[active].full }} 添加一个别名（如「电视剧」）并绑定路径，<br />
        搜索结果里的「快速转存」就能一键选它。
      </div>
    </div>
  </div>

  <!-- 新增 / 编辑弹窗：限高 + 表体滚动（内容长时保存按钮不被顶出屏） -->
  <a-modal
    v-model:open="modalOpen"
    :title="editingId != null ? '编辑目录' : '新增目录'"
    :width="520"
    centered
    wrap-class-name="dd-modal-wrap"
  >
    <div class="dd-field">
      <label class="dd-label">名称<i>*</i></label>
      <a-input v-model:value="fName" :maxlength="20" placeholder="你自己起的别名，如「电视剧」「电影」" />
      <div class="dd-tip">这个名字会出现在搜索结果「快速转存」的下拉框里。</div>
    </div>

    <div class="dd-row2">
      <div class="dd-field">
        <label class="dd-label">网盘<i>*</i></label>
        <a-select v-model:value="fType" :options="typeOptions" class="dd-sel" @change="onTypeChange" />
      </div>
      <div class="dd-field">
        <label class="dd-label">所属账号<i>*</i></label>
        <a-select
          v-model:value="fAcc"
          :options="accOptions"
          class="dd-sel"
          placeholder="该网盘暂无账号，请先到「网盘连接」添加"
        />
      </div>
    </div>

    <div class="dd-field">
      <label class="dd-label">网盘路径<i>*</i></label>
      <div class="dd-pick">
        <a-input :value="fPath" readonly placeholder="尚未选择，点右侧「浏览」从目录树里选" />
        <a-button type="primary" ghost @click="toggleTree">{{ treeOpen ? '收起目录' : '浏览' }}</a-button>
      </div>
      <div v-if="treeOpen" class="dd-tree">
        <div
          v-for="row in flatTree"
          :key="row.path"
          class="dd-tnode"
          :class="{ on: fPath === row.path }"
          :style="{ paddingLeft: 8 + row.depth * 18 + 'px' }"
          @click="pickPath(row.path)"
        >
          <span class="dd-tico" @click.stop="row.hasKids && toggleNode(row.path)">
            {{ row.hasKids ? (expanded.has(row.path) ? '▾' : '▸') : '·' }}
          </span>
          <span>{{ row.name }}</span>
        </div>
      </div>
    </div>

    <div class="dd-field">
      <label class="dd-label">排序</label>
      <a-input-number v-model:value="fSort" :min="1" :precision="0" class="dd-num" />
      <div class="dd-tip">数字越小越靠前，「快速转存」的下拉框按这个顺序排。</div>
    </div>

    <div class="dd-field">
      <div class="dd-qmsline">
        <a-switch v-model:checked="fQmsOn" />
        <span class="dd-qmslabel">联动 QMS</span>
      </div>
      <div class="dd-tip">开启后，转到此目录的资源会自动送 QMS 刮削整理。</div>
      <div v-if="fQmsOn" class="dd-qmsbody">
        <label class="dd-label">QMS 整理目录<i>*</i></label>
        <a-select
          v-model:value="fQmsId"
          :options="qmsOptions"
          class="dd-sel"
          :placeholder="qmsOptions.length ? '请选择 QMS 整理目录' : 'QMS 暂无刮削目录，请先到 qmediasync 添加'"
        />
        <div class="dd-tip">自动转存完成后 15 秒触发 QMS 整理。</div>
        <label class="dd-label dd-mt12">STRM 生成（可选）</label>
        <a-select v-model:value="fStrmId" :options="strmOptions" class="dd-sel" />
        <div class="dd-tip">QMS 整理完成后 15 秒触发 STRM 生成，不需要就选「不生成」。</div>
      </div>
    </div>

    <template #footer>
      <a-button @click="modalOpen = false">取消</a-button>
      <a-button type="primary" @click="confirmEditor">保存</a-button>
    </template>
  </a-modal>
</template>

<style scoped>
/* ===== 页面主体（dd- 前缀，对齐原型 parts/page-default-dir.html） ===== */
.dd-wrap {
  padding: 0;
}

/* 整页收进一张卡：标题/说明/新增按钮做卡头，tab 做卡内分段栏，表格接在下面 */
.dd-card {
  background: var(--card);
  border-radius: var(--r);
  box-shadow: var(--shadow);
  padding: 0;
  overflow: hidden;
}

.dd-cardhd {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  padding: 18px 22px 16px;
  border-bottom: 1px solid var(--split);
}
.dd-headtt {
  margin-bottom: 6px;
}
.dd-headtt h2 {
  font-size: 15.5px;
  font-weight: 600;
  margin: 0 0 5px;
  line-height: 1.35;
}
.dd-headdesc {
  font-size: 12.5px;
  color: var(--text3);
  line-height: 1.7;
  max-width: 620px;
}
.dd-headdesc b {
  color: var(--text2);
  font-weight: 500;
}
.dd-headact {
  flex: 0 0 auto;
  padding-top: 2px;
}

/* tab 栏：与卡片同宽（不再是孤零零一小条） */
.dd-tabsbar {
  padding: 10px 14px 0;
  border-bottom: 1px solid var(--split);
  background: var(--surface-2);
}
.dd-tabsbar .tabs {
  margin-bottom: 0;
}

/* tab 里的数量徽章，与搜索页 .pktab .pknum 同一套长相 */
.dd-tabnum {
  margin-left: 6px;
  font-size: 12px;
  line-height: 1;
  padding: 2px 7px;
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.06);
  color: var(--text2);
  font-variant-numeric: tabular-nums;
}
.tabs div.on .dd-tabnum {
  background: rgba(22, 119, 255, 0.12);
  color: var(--primary);
}

.dd-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
}
.dd-th {
  text-align: left;
  font-size: 12.5px;
  font-weight: 500;
  color: var(--text3);
  background: var(--surface-2);
  padding: 10px 14px;
  border-bottom: 1px solid var(--split);
  white-space: nowrap;
}
.dd-td {
  padding: 13px 14px;
  border-bottom: 1px solid var(--split);
  font-size: 13.5px;
  color: var(--text);
  vertical-align: middle;
  overflow: hidden;
}
.dd-row:hover {
  background: var(--surface-3);
}
.dd-row:last-child .dd-td {
  border-bottom: none;
}

/* 排序：安静的数字（药丸给单位数太小题大做） */
.dd-sort {
  font-size: 12.5px;
  color: var(--text3);
  font-variant-numeric: tabular-nums;
}

/* 名称与路径合并成一格：名称当主体（大字），路径当次级信息（小字 + 省略号） */
.dd-name {
  font-weight: 500;
  display: block;
  line-height: 1.5;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dd-path {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text3);
  display: block;
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dd-acc {
  font-size: 12.5px;
  color: var(--text3);
}
.dd-ops {
  display: flex;
  gap: 4px;
  justify-content: flex-end;
}
.dd-op {
  height: 28px;
  padding: 0 10px;
  font-size: 13px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--card);
  cursor: pointer;
  color: var(--text);
  transition: all 0.15s;
}
.dd-op:hover {
  border-color: var(--primary);
  color: var(--primary);
}
.dd-op-del:hover {
  border-color: var(--error);
  color: var(--error);
}
.dd-empty {
  padding: 56px 20px;
  text-align: center;
  color: var(--text3);
}
.dd-empty b {
  display: block;
  font-size: 15px;
  color: var(--text2);
  margin-bottom: 6px;
  font-weight: 500;
}
.dd-default-tag {
  font-size: 12px;
  padding: 1px 7px;
  border-radius: 5px;
  background: rgba(22, 119, 255, 0.1);
  color: var(--primary);
  border: 1px solid rgba(22, 119, 255, 0.3);
  white-space: nowrap;
}
.dd-setdefault {
  font-size: 12.5px;
  color: var(--text3);
  background: none;
  border: 1px dashed var(--border);
  border-radius: 6px;
  padding: 2px 8px;
  cursor: pointer;
}
.dd-setdefault:hover {
  color: var(--primary);
  border-color: var(--primary);
  border-style: solid;
}

/* ===== 弹窗内字段 ===== */
.dd-field {
  margin-bottom: 16px;
}
.dd-field:last-child {
  margin-bottom: 0;
}
.dd-label {
  display: block;
  font-size: 13px;
  color: var(--text2);
  margin-bottom: 6px;
}
.dd-label i {
  color: var(--error);
  font-style: normal;
  margin-left: 2px;
}
.dd-tip {
  font-size: 12px;
  color: var(--text3);
  margin-top: 5px;
  line-height: 1.6;
}
.dd-row2 {
  display: flex;
  gap: 12px;
}
.dd-row2 > * {
  flex: 1;
}
.dd-pick {
  display: flex;
  gap: 8px;
}
.dd-pick > *:first-child {
  flex: 1;
}
.dd-num {
  width: 100px;
}
.dd-sel {
  width: 100%;
}
/* 联动 QMS：开关行 + 展开区 */
.dd-qmsline {
  display: flex;
  align-items: center;
  gap: 8px;
}
.dd-qmslabel {
  font-size: 13px;
  color: var(--text2);
}
.dd-qmsbody {
  margin-top: 12px;
}
.dd-mt12 {
  margin-top: 12px;
}

/* 目录树（弹窗内，max-height 内滚） */
.dd-tree {
  max-height: 220px;
  overflow: auto;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 6px 4px;
  margin-top: 8px;
}
.dd-tnode {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 8px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  user-select: none;
}
.dd-tnode:hover {
  background: var(--hover);
}
.dd-tnode.on {
  background: rgba(22, 119, 255, 0.1);
  color: var(--primary);
}
.dd-tico {
  width: 14px;
  text-align: center;
  font-size: 11px;
  color: var(--text3);
  flex: none;
}

/* ===== 暗色覆盖：原型已有的搬过来，新增的浅色硬编码单独补 ===== */
html[data-theme='dark'] .dd-tabsbar {
  background: var(--surface-2);
}
html[data-theme='dark'] .dd-tabnum {
  background: rgba(255, 255, 255, 0.1);
  color: var(--text2);
}
html[data-theme='dark'] .tabs div.on .dd-tabnum {
  background: rgba(64, 150, 255, 0.22);
  color: #91caff;
}
html[data-theme='dark'] .dd-default-tag {
  background: rgba(64, 150, 255, 0.18);
  border-color: rgba(64, 150, 255, 0.4);
}
/* 树选中高亮是硬编码浅蓝底，深色下几乎看不见（原型 qs-pv-new 踩过的同一个坑） */
html[data-theme='dark'] .dd-tnode.on {
  background: rgba(64, 150, 255, 0.2);
  color: #91caff;
}

/* ===== 移动端（<768px）：卡头上下堆叠、弹窗双列改单列；PC 一条不动 ===== */
@media (max-width: 767px) {
  .dd-cardhd {
    flex-direction: column;
    gap: 12px;
    padding: 14px 14px 12px;
  }
  /* 新增目录按钮占满整行（本页主操作） */
  .dd-headact { width: 100%; }
  .dd-headact :deep(.ant-btn) { width: 100%; }
  .dd-tabsbar { padding: 8px 10px 0; }

  /* 弹窗里「网盘 + 所属账号」双列改单列 */
  .dd-row2 { display: block; }
  .dd-row2 > * + * { margin-top: 12px; }
}
</style>

<style>
/* 弹窗限高：antd Modal 挂在 body 下，scoped 够不到 → 用 dd-modal-wrap 隔离，不污染别的页面。
   表体自身滚动，标题和底部按钮始终可见（原型 dd-modal 的 flex 方案）。 */
.dd-modal-wrap .ant-modal-content {
  display: flex;
  flex-direction: column;
  max-height: calc(100vh - 48px);
  overflow: hidden;
  padding: 0;
}
.dd-modal-wrap .ant-modal-header {
  flex: none;
  padding: 16px 20px;
  border-bottom: 1px solid var(--split);
  margin-bottom: 0;
}
.dd-modal-wrap .ant-modal-title {
  font-size: 15px;
  font-weight: 600;
}
.dd-modal-wrap .ant-modal-close {
  top: 15px;
  inset-inline-end: 16px;
}
.dd-modal-wrap .ant-modal-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 18px 20px;
}
.dd-modal-wrap .ant-modal-footer {
  flex: none;
  margin-top: 0;
  padding: 14px 20px;
  border-top: 1px solid var(--split);
  background: var(--surface-3);
}

/* 手机：限高跟着 dvh 走 + 底部贴边（全局移动层在 pk.css，这里只补本弹窗的 flex 高度） */
@media (max-width: 767px) {
  .dd-modal-wrap .ant-modal-content { max-height: calc(100dvh - 24px); }
  .dd-modal-wrap .ant-modal-body { padding: 14px 16px; }
  .dd-modal-wrap .ant-modal-footer { padding: 12px 16px; }
}
</style>
