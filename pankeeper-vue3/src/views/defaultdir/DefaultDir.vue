<script setup lang="ts">
/* =====================================================================
 * 转存配置（默认目录）—— 原型 parts/page-default-dir.html 的 Vue3 还原。
 * 「快速转存」的下拉项就来自这里，按 网盘 tab × 账号 两级隔离。
 * dd- 前缀私有样式在本文件 <style scoped>；数据直接读写 ddStore（内存态），
 * 增删改走 api/modules/dd.ts（mockDelay 包一层，后端就绪后只换实现）。
 * ===================================================================== */
import { computed, onMounted, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import LazyDirTree from '@/components/LazyDirTree.vue'
import PkPager from '@/components/PkPager.vue'
import { useIsMobile } from '@/composables/useIsMobile'
import { ddStore, ddFind } from '@/api/mock/dd'
import { DD_MEDIA, MAIN_ORDER, DRIVE_META } from '@/api/mock/meta'
import { accountStore } from '@/api/mock/accounts'
import { getRootDirs } from '@/api/modules/accounts'
import { saveDdItem, deleteDdItem, setDefaultDir, listDdItems, listQmsPaths } from '@/api/modules/dd'
import { getSettings } from '@/api/modules/settings'
import type { DdItem, DdQmsPath, MainDriveType } from '@/types/model'

/* ===== 列表态 ===== */
const active = ref<MainDriveType>('baidu')
/* 手机（<768px）表格换卡片列表：横滑表格的「操作」列在窄屏上永远滑不到头 */
const isMobile = useIsMobile()

/** 当前 tab 的行，按 sort 升序（数字越小在快速转存下拉里越靠前） */
const rows = computed<DdItem[]>(() =>
  ddStore.items.filter((x) => x.type === active.value).sort((a, b) => (a.sort || 0) - (b.sort || 0)),
)

function countOf(t: MainDriveType): number {
  return ddStore.items.filter((x) => x.type === t).length
}

/* ===== 分页（内存切片；切网盘 tab 回第 1 页） ===== */
const page = ref(1)
const size = ref(20)
const pagedRows = computed(() => rows.value.slice((page.value - 1) * size.value, page.value * size.value))
watch(() => active.value, () => (page.value = 1))
watch(() => rows.value.length, () => {
  const max = Math.max(1, Math.ceil(rows.value.length / size.value))
  if (page.value > max) page.value = max
})

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
/** 目录类型（电影/电视节目，必选）：快速转存电影目录+多文件时展示文件多选；
 * 电视节目目录照旧全转（多集合理）。存量老目录按名称推断初值 */
const fMediaType = ref<'movie' | 'tv'>('movie')
const MEDIA_TYPE_OPTS = [
  { value: 'movie', label: '电影' },
  { value: 'tv', label: '电视节目' },
]
/** 按目录名推断类型（存量目录没标过时用） */
function inferMediaType(name: string): 'movie' | 'tv' {
  const n = (name || '').toLowerCase()
  return n.includes('电视') || n.includes('剧') ? 'tv' : 'movie'
}
const fType = ref<MainDriveType>('baidu')
const fAcc = ref('')
const fPath = ref('')
const fSort = ref(1)
const fQmsOn = ref(false)
const fQmsId = ref<number | undefined>(undefined)
// LitePan 联动（media.backend=litepan 时替代 QMS 表单）：开关 + 事件名（输入框，等
// LitePan 出接口后换下拉——2026-10-06 用户定稿）
const fLpOn = ref(false)
const fLpEvent = ref('')
/** 当前联动后端：qms 显示 QMS 表单，litepan 显示 LitePan 表单（进页面拉一次） */
const mediaBackend = ref<'qms' | 'litepan'>('qms')

const typeOptions = MAIN_ORDER.map((k) => ({ value: k, label: DRIVE_META[k].full }))
/** 所属账号：真实账号列表（网盘连接页配的），不是 mock 的假号 */
const accOptions = computed(() =>
  accountStore.accounts
    .filter((a) => a.type === fType.value)
    .map((a) => ({ value: String(a.id), label: a.alias || a.nickname || `${DRIVE_META[a.type].name}#${a.id}` })),
)
/** QMS 刮削目录 / STRM 同步路径：打开弹窗时从 QMS 拉真实列表 */
const qmsPaths = ref<DdQmsPath[]>([])
// QMS 刮削目录下拉：#id · 类型 · 路径
const qmsOptions = computed(() =>
  qmsPaths.value.map((p) => ({ value: p.id, label: `#${p.id} · ${DD_MEDIA[p.media_type as 'tv'] || p.media_type} · ${p.source_path}` })),
)
async function loadQmsStrmPaths() {
  // QMS 未启用/连不上时静默置空：下拉显示"暂无可选"，不挡住表单其他项。
  // STRM 不再在此配置（2026-10-04 定稿：跟随 QMS 自动配对，整理目标根=同步路径）
  qmsPaths.value = await listQmsPaths().catch(() => [])
}

/* ===== 目录选择弹窗（与网盘连接页/任务弹窗统一）：LazyDirTree 真实目录，只显示文件夹 ===== */
const bdOpen = ref(false)
const bdPath = ref('')
const bdTree = ref<InstanceType<typeof LazyDirTree> | null>(null)
const bdRefreshing = ref(false)
const bdType = computed<MainDriveType>(() => fType.value)
const bdAccId = computed<number | null>(() => {
  const n = Number(fAcc.value)
  return Number.isFinite(n) && n > 0 ? n : null
})
/** LazyDirTree 的 key：类型/账号变了整树重建 */
const bdKey = computed(() => `${bdType.value}/${bdAccId.value ?? 'def'}`)
/** 根路径锁定：网盘连接页配置的「默认根目录」（root_cfg），目录弹窗只展示它的子目录 */
const rootDirs = ref<Record<string, string>>({})
const bdRootPath = computed(() => rootDirs.value[bdType.value] || '')

function onBdPick(path: string) {
  bdPath.value = path
}
async function onBdRefresh() {
  bdRefreshing.value = true
  try {
    await bdTree.value?.reload()
  } finally {
    bdRefreshing.value = false
  }
}
function onPickOk() {
  if (!bdPath.value) {
    message.warning('请先在树里选择一个目录')
    return
  }
  fPath.value = bdPath.value
  bdOpen.value = false
}

/** 打开目录树：编辑=已填路径自动展开选中；新增=不预选（树根仍是默认根目录） */
function onBrowse() {
  bdPath.value = fPath.value
  bdOpen.value = true
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
  fMediaType.value = it ? (it.media_type === 'tv' || it.media_type === 'movie' ? it.media_type : inferMediaType(it.name)) : 'movie'
  // 新增时排序默认排到该网盘现有最大值 + 1（原型如此）
  fSort.value = it
    ? it.sort
    : ddStore.items.filter((x) => x.type === fType.value).reduce((m, x) => Math.max(m, x.sort || 0), 0) + 1
  fQmsOn.value = it ? !!it.qms_on : false
  fQmsId.value = it?.qms_id ?? undefined
  fLpOn.value = it ? !!it.lp_on : false
  fLpEvent.value = it?.lp_event || ''
  bdPath.value = fPath.value
  modalOpen.value = true
  loadQmsStrmPaths()
}

/** 切换网盘：账号跟随切到该网盘第一个，已选路径/树展开态作废（跨网盘路径无意义） */
function onTypeChange() {
  const firstAcc = accountStore.accounts.find((a) => a.type === fType.value)
  fAcc.value = String(firstAcc?.id ?? '')
  fPath.value = ''
}

/* ===== 目录树（dd-tnode 结构，扁平化渲染：缩进 = 深度 × 18px，视觉与原型嵌套版一致） ===== */
interface FlatNode {
  path: string
  name: string
  depth: number
  hasKids: boolean
}
const expanded = ref(new Set<string>())

/* ===== 进页面：拉真实转存配置 + QMS/STRM 路径列表（此前页面渲染的一直是 mock 假数据） ===== */
onMounted(async () => {
  await listDdItems().catch(() => {})
  rootDirs.value = await getRootDirs().catch(() => ({}))
  loadQmsStrmPaths()
  // 联动后端决定弹窗里显示 QMS 还是 LitePan 表单（qms_on/lp_on 两套字段各自保存互不覆盖）
  getSettings().then((d) => (mediaBackend.value = d.media?.backend || 'qms')).catch(() => {})
})

/* ===== 保存（校验全部不关弹窗，重名只在同网盘 + 同账号内拦截） ===== */
async function confirmEditor() {
  const name = fName.value.trim()
  if (!name) {
    message.error('请填写名称')
    return
  }
  if (!fMediaType.value) {
    message.error('请选择目录类型（电影 / 电视节目）')
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
  if (fLpOn.value && !fLpEvent.value.trim()) {
    message.error('LitePan 联动需填写事件名（与 LitePan 自动化规则里配的一致）')
    return
  }
  const qmsFields = {
    media_type: fMediaType.value,
    qms_on: fQmsOn.value,
    qms_id: fQmsOn.value && fQmsId.value != null ? fQmsId.value : null,
    strm_id: null, // 此表单只管 QMS；STRM 目录在任务弹窗里配（DdItemDraft 要求字段存在）
    // LitePan 联动字段与 QMS 独立保存（切后端时另一套不丢配置）
    lp_on: fLpOn.value,
    lp_event: fLpOn.value ? fLpEvent.value.trim() : fLpEvent.value.trim(),
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

      <!-- 表格：排序 / 名称+路径同格 / 所属账号 / 默认 / 操作（PC；手机换下方卡片列表） -->
      <div v-if="rows.length && !isMobile" class="pk-hscroll">
        <table class="dd-table">
          <thead>
            <tr>
              <th class="dd-th" style="width: 60%">名称 / 网盘路径</th>
              <th class="dd-th" style="width: 16%">所属账号</th>
              <th class="dd-th" style="width: 12%">默认</th>
              <th class="dd-th" style="width: 12%; text-align: right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="it in pagedRows" :key="it.id" class="dd-row">
              <td class="dd-td">
                <div class="dd-namecell">
                  <span class="dd-sort">{{ it.sort }}</span>
                  <span class="dd-namewrap">
                    <span class="dd-name">{{ it.name }}</span>
                    <span class="dd-path" :title="it.path">{{ it.path }}</span>
                  </span>
                </div>
              </td>
              <td class="dd-td"><span class="dd-acc">{{ accLabel(it.account) }}</span></td>
              <td class="dd-td">
                <span v-if="it.is_default" class="dd-default-tag">默认</span>
                <button v-else class="dd-setdefault" type="button" @click="setDefault(it)">设为默认</button>
              </td>
              <td class="dd-td">
                <div class="dd-ops">
                  <a-button type="link" size="small" class="dd-linkop" @click="openEditor(it.id)">编辑</a-button>
                  <span class="dd-opdiv">丨</span>
                  <a-popconfirm
                    :title="`确定删除「${it.name}」？删除后「快速转存」将不再显示它。`"
                    ok-text="删除"
                    cancel-text="取消"
                    @confirm="doDelete(it)"
                  >
                    <a-button type="link" danger size="small" class="dd-linkop">删除</a-button>
                  </a-popconfirm>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <!-- 手机卡片列表：一行 = 排序 + 名称 + 默认标记，次行路径，末行账号 + 操作 -->
      <div v-else-if="rows.length" class="dd-cards">
        <div v-for="it in pagedRows" :key="it.id" class="dd-mcard">
          <div class="dd-mtop">
            <span class="dd-sort">{{ it.sort }}</span>
            <span class="dd-mname">{{ it.name }}</span>
            <span v-if="it.is_default" class="dd-default-tag">默认</span>
            <button v-else class="dd-setdefault" type="button" @click="setDefault(it)">设为默认</button>
          </div>
          <div class="dd-mpath" :title="it.path">{{ it.path }}</div>
          <div class="dd-mfoot">
            <span class="dd-acc">{{ accLabel(it.account) }}</span>
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
          </div>
        </div>
      </div>
      <div v-else class="dd-empty">
        <b>还没有配置转存目录</b>
        给 {{ DRIVE_META[active].full }} 添加一个别名（如「电视剧」）并绑定路径，<br />
        搜索结果里的「快速转存」就能一键选它。
      </div>
      <PkPager v-model:current="page" v-model:pageSize="size" :total="rows.length" />
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
      <div style="display: flex; gap: 10px; align-items: center">
        <a-input v-model:value="fName" :maxlength="20" style="flex: 0 0 60%" />
        <a-select v-model:value="fMediaType" :options="MEDIA_TYPE_OPTS" style="flex: 1" />
      </div>
      <div class="dd-tip">名字出现在搜索结果「快速转存」的下拉框里；类型决定快速转存的行为——电影目录检测到多文件时可以勾选只转正片，电视节目目录照旧全部转存。</div>
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
        <a-input :value="fPath" readonly />
        <a-button type="primary" ghost @click="onBrowse">浏览</a-button>
      </div>
    </div>

    <div class="dd-field">
      <label class="dd-label">排序</label>
      <a-input-number v-model:value="fSort" :min="1" :precision="0" class="dd-num" />
      <div class="dd-tip">数字越小越靠前，「快速转存」的下拉框按这个顺序排。</div>
    </div>

    <!-- 联动区块按「系统设置 → 联动后端」切换：qms = QMS 表单，litepan = LitePan 表单。
         两套字段（qms_on/lp_on）各自独立保存，切后端不丢另一套的配置。 -->
    <div v-if="mediaBackend === 'qms'" class="dd-field">
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
        <div class="dd-tip">自动转存完成后触发 QMS 整理。</div>
        <div class="dd-tip dd-mt12">QMS 整理成功后会自动按整理结果生成 STRM，失败不生成（无需配置）。</div>
      </div>
    </div>
    <div v-else class="dd-field">
      <div class="dd-qmsline">
        <a-switch v-model:checked="fLpOn" />
        <span class="dd-qmslabel">联动 LitePan</span>
      </div>
      <div class="dd-tip">开启后，转到此目录的资源完成时会推 Webhook 给 LitePan（事件按下面配的）。</div>
      <div v-if="fLpOn" class="dd-qmsbody">
        <label class="dd-label">LitePan 事件名<i>*</i></label>
        <a-input v-model:value="fLpEvent" class="dd-sel" placeholder="如 transfer.movie.done" />
        <div class="dd-tip">须与 LitePan 自动化规则里配的事件名完全一致；不同目录可配不同事件（电影/电视剧各推各的规则）。</div>
        <div class="dd-tip dd-mt12">LitePan 收到后按匹配的规则自动整理；刮削/STRM 状态由 LitePan 自理，PanKeeper 只推自识别结果。</div>
      </div>
    </div>

    <template #footer>
      <a-button @click="modalOpen = false">取消</a-button>
      <a-button type="primary" @click="confirmEditor">保存</a-button>
    </template>
  </a-modal>
    <!-- 目录选择弹窗：与网盘连接页/任务弹窗同款（LazyDirTree，只显示文件夹，带刷新） -->
    <a-modal
      :open="bdOpen"
      :width="480"
      title="选择网盘目录"
      :destroy-on-close="true"
      ok-text="选定此处"
      @ok="onPickOk"
      @update:open="(v: boolean) => (bdOpen = v)"
    >
      <div class="bd-titlebar">
        <span>{{ DRIVE_META[bdType]?.full || '' }}</span>
        <a-button size="small" :loading="bdRefreshing" @click="onBdRefresh">刷新</a-button>
      </div>
      <p class="small" style="color: var(--text3); margin-bottom: 10px">
        点文件夹名选中目标目录，点左侧箭头展开子目录。
      </p>
      <!-- 打开即沿已配置路径（默认目录）逐层展开并选中；根锁定：只显示默认目录的子目录 -->
      <LazyDirTree ref="bdTree" :key="bdKey" :type="bdType" :acc-id="bdAccId" :root-path="bdRootPath" :initial-path="bdPath" @select="onBdPick" />
      <p class="bd-picked">已选目录：<b>{{ bdPath || '/' }}</b></p>
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

/* 表格走全局基础样式（th 13px/600/text2、td padding 14/20），这里只收窄横向内边距 + 固定布局
   —— 与转存历史/记录页的表头口径一致，别再自己压字号/字重/颜色（转存配置页曾漏改被用户点名） */
.dd-table { table-layout: fixed; }
.dd-table th,
.dd-table td { padding-left: 12px; padding-right: 12px; }
.dd-th { white-space: nowrap; }
.dd-td { overflow: hidden; }

/* 排序：安静的数字（药丸给单位数太小题大做）。并入名称格与移动端卡片同形态——
   独立排序列会被 fixed 布局按比例撑宽（48px 实测撑到 101px），数字和名称隔一大截空白 */
.dd-sort {
  flex: 0 0 auto;
  min-width: 16px;
  text-align: right;
  font-size: 12.5px;
  color: var(--text3);
  font-variant-numeric: tabular-nums;
}
.dd-namecell { display: flex; align-items: baseline; gap: 10px; min-width: 0; }
.dd-namewrap { flex: 1 1 auto; min-width: 0; }

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
/* 桌面表格操作列：编辑丨删除 文字链接（与转存记录页同款），红色删除走 antd danger */
.dd-linkop {
  padding: 0;
}
.dd-opdiv {
  color: var(--text4);
  font-size: 12px;
  margin: 0 2px;
  user-select: none;
  align-self: center;
}
.dd-op {
  height: 28px;
  padding: 0 10px;
  font-size: 13px;
  border: 1px solid color-mix(in srgb, var(--primary) 35%, var(--border));
  border-radius: 6px;
  background: color-mix(in srgb, var(--primary) 8%, var(--card));
  cursor: pointer;
  color: var(--primary);
  transition: all 0.15s;
}
.dd-op:hover {
  border-color: var(--primary);
  background: color-mix(in srgb, var(--primary) 16%, var(--card));
  color: var(--primary);
}
.dd-op-del {
  border-color: color-mix(in srgb, var(--error) 35%, var(--border));
  background: color-mix(in srgb, var(--error) 8%, var(--card));
  color: var(--error);
}
.dd-op-del:hover {
  border-color: var(--error);
  background: color-mix(in srgb, var(--error) 16%, var(--card));
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

  /* ---- 手机卡片列表（表格的替代渲染，仅 <768px 存在） ---- */
  .dd-mcard {
    padding: 12px 14px;
    border-bottom: 1px solid var(--split);
    -webkit-tap-highlight-color: transparent;
  }
  .dd-mcard:last-child { border-bottom: none; }
  .dd-mcard:active { background: var(--surface-3); }
  .dd-mtop {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
  }
  .dd-mname {
    flex: 1;
    min-width: 0;
    font-weight: 500;
    font-size: 13.5px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .dd-mpath {
    font-family: var(--font-mono);
    font-size: 11.5px;
    color: var(--text3);
    margin: 4px 0 0 22px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .dd-mfoot {
    display: flex;
    align-items: center;
    margin: 8px 0 0 22px;
  }
  .dd-mfoot .dd-acc {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .dd-mfoot .dd-ops { flex: none; }
  /* 触屏目标放大一点：编辑/删除 32px 高 */
  .dd-mfoot .dd-op { height: 32px; padding: 0 14px; }
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
/* 目录弹窗标题栏 + 已选回显（与网盘连接页同款） */
.bd-titlebar { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding-right: 34px; }
.bd-picked { margin: 12px 0 0; padding: 8px 10px; border-radius: 8px; background: var(--surface-2); font-size: 12.5px; color: var(--text3); }
.bd-picked b { color: var(--primary); font-weight: 500; }
</style>
