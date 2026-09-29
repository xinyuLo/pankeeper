<script setup lang="ts">
/* =====================================================================
 * 网盘连接 —— 原型 _shell.html data-view="accounts" 的 Vue3 还原。
 * 三张网盘卡：只展示连接状态，绝不回填凭据明文（设计红线）。
 * 状态读写走 accountStore（内存 mock），动作走 api/modules/accounts.ts。
 * ===================================================================== */
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import LazyDirTree from '@/components/LazyDirTree.vue'
import { accountStore, ACCOUNT_STATUS_VIEW, type AccountRow } from '@/api/mock/accounts'
import { MAIN_ORDER, DRIVE_META } from '@/api/mock/meta'
import { ddStore } from '@/api/mock/dd'
import { checkAccount, clearAccount, getSummary, listAccounts, saveCredential, setDriveNotify, type AccountSummary } from '@/api/modules/accounts'
import { listDdItems, saveDdItem, setDefaultDir } from '@/api/modules/dd'
import type { DdItem, MainDriveType } from '@/types/model'

/** 卡片列表：多账号平铺，按平台固定顺序 + 同平台按 id 排 */
const rows = computed<AccountRow[]>(() => {
  const rank: Record<string, number> = {}
  MAIN_ORDER.forEach((t, i) => (rank[t] = i))
  return [...accountStore.accounts].sort(
    (a, b) => (rank[a.type] ?? 99) - (rank[b.type] ?? 99) || a.id - b.id,
  )
})

/** 状态 → tag 类名/文案/圆点（connected/expired/unset 三态映射） */
function view(status: string) {
  return ACCOUNT_STATUS_VIEW[status]
}

/* ===== 失效通知开关（网盘粒度，Server 酱）。
 * 探活发现该网盘凭据失效时，只有这里是开着的才会推送；
 * 总闸在「系统设置 → 推送通知」（enabled + 凭据时机开关）。 ===== */
const notifySaving = ref<MainDriveType | null>(null)
async function onToggleNotify(a: AccountRow, v: boolean) {
  if (notifySaving.value) return
  notifySaving.value = a.id
  try {
    await setDriveNotify(a.id, v)
    a.notify = v
    message.success(`${a.alias || DRIVE_META[a.type].full}：${v ? '已开启' : '已关闭'}失效通知`)
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(detail || '保存失败')
  } finally {
    notifySaving.value = null
  }
}

/* ===== 新增账号：选平台 + 别名 + 粘贴 Cookie，保存即验证 ===== */
const addOpen = ref(false)
const addSaving = ref(false)
const addType = ref<MainDriveType>('baidu')
const addAlias = ref('')
const addCookies = ref('')
const ADD_PLATFORMS = [
  { value: 'baidu', label: '百度网盘' },
  { value: 'quark', label: '夸克网盘' },
  { value: '115', label: '115 网盘' },
]

async function onAddSave() {
  if (!addCookies.value.trim()) {
    message.warning('请先粘贴 Cookie')
    return
  }
  addSaving.value = true
  try {
    const { nickname } = await addAccount(addType.value, addCookies.value.trim(), addAlias.value.trim())
    addOpen.value = false
    addCookies.value = ''
    addAlias.value = ''
    message.success(`账号已添加并验证通过（${nickname}）`)
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(detail || '添加失败')
  } finally {
    addSaving.value = false
  }
}

/* ===== 进页面：先吃缓存立即可见，再后台实时刷新 ===== */
onMounted(async () => {
  prefillSummaries()
  await listAccounts().catch(() => {})
  prefillSummaries() // 拉到后端缓存后再填一次（刷新后会员信息不再「闪一下就没了」）
  for (const a of accountStore.accounts) {
    if (a.status === 'connected') loadSummary(a.id)
  }
  await listDdItems().catch(() => {}) // 默认目标目录读写「转存配置」，进页对齐一次
})

/* ===== 默认目标目录：读写「转存配置」页的 is_default 条目（唯一真源，不是独立字段） ===== */
const bdOpen = ref(false)
const bdType = ref<MainDriveType>('quark')
const bdPath = ref('')
const bdSaving = ref(false)
const bdTitle = computed(() => `默认目标目录 · ${DRIVE_META[bdType.value]?.full || bdType.value}`)

/** 该网盘当前的默认条目 */
function defaultDirOf(type: MainDriveType): DdItem | null {
  return ddStore.items.find((x) => x.type === type && x.is_default) || null
}

function openBaseDir(a: AccountRow) {
  bdType.value = a.type
  bdPath.value = defaultDirOf(a.type)?.path || '/'
  bdOpen.value = true
}

/** 树里点选目录：只记选择（高亮），点弹窗「保存到此处」才落库 */
function onTreePick(path: string) {
  bdPath.value = path
}

async function onConfirmBaseDir() {
  if (!bdPath.value) {
    message.warning('请先在树里选择一个目录')
    return
  }
  bdSaving.value = true
  try {
    const item = defaultDirOf(bdType.value)
    if (item) {
      await saveDdItem({ ...item, path: bdPath.value }) // 更新默认条，保留 QMS/STRM 关联
    } else {
      const others = ddStore.items.filter((x) => x.type === bdType.value)
      if (others.length === 0) {
        // 一条都没有：新建即默认（后端对首条自动 is_default）
        await saveDdItem({ id: 0, type: bdType.value, account: 'main', sort: 1, name: '默认目录', path: bdPath.value, is_default: true, qms_on: false })
      } else {
        // 边缘：有条目但无默认 —— 更新第一条并设为默认
        const first = others[0]
        await saveDdItem({ ...first, path: bdPath.value })
        await setDefaultDir(first.id)
      }
    }
    bdOpen.value = false
    message.success('默认目标目录已保存')
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(detail || '保存失败')
  } finally {
    bdSaving.value = false
  }
}

/* ===== 卡片动作 ===== */
const checking = ref<number | null>(null)

async function onCheck(a: AccountRow) {
  checking.value = a.id
  try {
    const res = await checkAccount(a.id)
    // 检测结果回写：last_check 已在 api 里更新，status 以返回为准（真实后端可能探成过期）
    const row = accountStore.accounts.find((x) => x.id === a.id)
    if (row) row.status = res.status
    if (res.kind === 'success') {
      message.success(res.message)
      await loadSummary(a.id)
    } else if (res.kind === 'warning') message.warning(res.message)
    else message.error(res.message)
  } finally {
    checking.value = null
  }
}

/* ===== 容量 + 会员摘要：按账号拉取，检测连通成功后刷新 ===== */
const summaries = ref<Record<number, AccountSummary | null>>({})

/** 用 store 里后端缓存的摘要预填：刷新页面立刻能显示会员/容量，不必干等实时请求。 */
function prefillSummaries() {
  for (const a of accountStore.accounts) {
    if (a.summary) summaries.value[a.id] = a.summary
  }
}

async function loadSummary(accId: number) {
  try {
    const data = await getSummary(accId)
    summaries.value[accId] = data
    // 回写 store：本次会话内切页/回退不再闪空
    const row = accountStore.accounts.find((x) => x.id === accId)
    if (row) row.summary = data
  } catch {
    summaries.value[accId] = null
  }
}

function summaryOf(accId: number): AccountSummary | null {
  return summaries.value[accId] || null
}

function capOf(accId: number) {
  const cap = summaryOf(accId)?.capacity
  if (!cap || !cap.total) return null
  const pct = Math.min(100, Math.round((cap.used / cap.total) * 100))
  return {
    pct,
    used: (cap.used / 1024 ** 3).toFixed(cap.used > 10 * 1024 ** 3 ? 0 : 1),
    total: (cap.total / 1024 ** 3).toFixed(cap.total > 10 * 1024 ** 3 ? 0 : 1),
  }
}

function capClass(pct: number): string {
  return pct >= 95 ? 'full' : pct >= 80 ? 'warn' : ''
}

function gb(v: number): string {
  return (v / 1024 ** 3).toFixed(v > 10 * 1024 ** 3 ? 0 : 1)
}

/* 凭据表单：从浏览器 F12 复制整串 Cookie 粘贴；后端保存即验证，永远不回填明文 */
const credOpen = ref(false)
const credAcc = ref<AccountRow | null>(null)
const credTitle = computed(() => `配置凭据 · ${credAcc.value ? credAcc.value.alias || DRIVE_META[credAcc.value.type]?.full || credAcc.value.type : ''}`)
const credCookies = ref('')
const credSaving = ref(false)

function onConfig(a: AccountRow) {
  credAcc.value = a
  credCookies.value = ''
  credOpen.value = true
}

async function onCredSave() {
  if (!credCookies.value.trim()) {
    message.warning('请先粘贴 Cookie')
    return
  }
  if (!credAcc.value) return
  credSaving.value = true
  try {
    const { nickname } = await saveCredential(credAcc.value.id, credCookies.value.trim())
    credOpen.value = false
    message.success(`凭据已保存并验证通过（${nickname}）`)
    await loadSummary(credAcc.value.id)
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(detail || '凭据验证失败')
  } finally {
    credSaving.value = false
  }
}

async function onClear(a: AccountRow) {
  await clearAccount(a.id)
  message.success(`已清空「${a.alias || DRIVE_META[a.type].full}」凭据，状态置为未配置`)
}
</script>

<template>
  <div>
    <!-- 网盘卡片网格：未配置的卡整体半透明（.off） -->
    <div style="display: flex; justify-content: flex-end; margin-bottom: 14px">
      <a-button type="primary" @click="addOpen = true">＋ 新增网盘</a-button>
    </div>
    <div class="accgrid">
      <div v-for="a in rows" :key="a.type" class="acc" :class="{ off: a.status === 'unset' }" :style="{ '--acc': a.color }">
        <div class="acchead">
          <div class="accname">
            <span class="chip" :style="{ background: a.color }">{{ a.short }}</span>
            {{ a.alias || DRIVE_META[a.type].full }}
            <span class="tag acc-tag" :class="view(a.status).cls">
              <span class="dot" :class="view(a.status).dot"></span>{{ view(a.status).label }}
            </span>
          </div>
        </div>
        <div class="kv">
          <span>会员</span>
          <b v-if="a.status === 'connected' && summaryOf(a.id)?.vip && summaryOf(a.id)!.vip!.name !== '普通用户'"
             :title="summaryOf(a.id)!.vip!.expires ? `会员到期：${summaryOf(a.id)!.vip!.expires}` : ''">
            <span class="vip-tag">✦ {{ summaryOf(a.id)!.vip!.name }}<template v-if="summaryOf(a.id)!.vip!.expires"> · {{ summaryOf(a.id)!.vip!.expires }}</template></span>
          </b>
          <b v-else style="color: var(--text3)">—</b>
        </div>
        <div class="kv"><span>凭据类型</span><b>{{ a.cred_kind }}</b></div>
        <div class="kv"><span>上次检测</span><b>{{ a.last_check }}</b></div>
        <div class="kv">
          <span>默认目标目录</span>
          <b style="display: inline-flex; align-items: center; gap: 6px">
            {{ defaultDirOf(a.type)?.path || '—' }}
            <a-tooltip :title="a.status !== 'connected' ? '请先配置凭据并连通' : '从网盘目录中选择默认保存位置'">
              <a-button
                type="link"
                size="small"
                style="padding: 0 2px; height: auto"
                :disabled="a.status !== 'connected'"
                @click="openBaseDir(a)"
              >{{ defaultDirOf(a.type) ? '修改' : '配置' }}</a-button>
            </a-tooltip>
          </b>
        </div>
        <div class="kv">
          <span>失效通知</span>
          <b>
            <a-switch
              :checked="a.notify"
              :loading="notifySaving === a.id"
              size="small"
              @change="(v: any) => onToggleNotify(a, !!v)"
            />
          </b>
        </div>
        <!-- 未配置/凭据失效：容量条占位（三张卡高度一致，不缺一块） -->
        <div v-if="a.status !== 'connected'" class="capblock">
          <div class="capbar"><i style="width: 0%"></i></div>
          <div class="capmeta">
            <span style="color: var(--text3)">配置凭据后显示容量</span>
            <span>—</span>
          </div>
        </div>
        <div v-else class="capblock">
          <template v-if="capOf(a.id)">
            <div class="capbar"><i :class="capClass(capOf(a.id)!.pct)" :style="{ width: capOf(a.id)!.pct + '%' }"></i></div>
            <div class="capmeta">
              <span>已用 {{ capOf(a.id)!.used }} GB / 共 {{ capOf(a.id)!.total }} GB</span>
              <span>{{ capOf(a.id)!.pct }}%</span>
            </div>
          </template>
          <div v-else-if="summaries[a.id] !== undefined" class="small" style="color: var(--text3)">
            {{ summaries[a.id] === null ? '容量信息获取失败' : '暂无容量信息' }}
          </div>
        </div>
        <div class="accbtns">
          <a-button type="primary" size="small" @click="onConfig(a)">配置凭据</a-button>
          <a-button size="small" :loading="checking === a.id" @click="onCheck(a)">检测连通</a-button>
          <a-popconfirm
            v-if="a.status !== 'unset'"
            title="确定清空该网盘的凭据？清空后状态变为未配置，需重新绑定。"
            ok-text="清空"
            cancel-text="取消"
            @confirm="onClear(a)"
          >
            <a-button size="small" danger>清空</a-button>
          </a-popconfirm>
        </div>
      </div>
    </div>



    <!-- 新增网盘：选择要接入的网盘（未适配的置灰） -->
    <!-- 新增账号：选平台 + 别名 + 粘贴 Cookie，后端保存即验证 -->
    <a-modal v-model:open="addOpen" :width="440" title="新增账号" :footer="null">
      <div class="formrow">
        <label>平台</label>
        <div class="ctl">
          <a-select v-model:value="addType" :options="ADD_PLATFORMS" style="width: 200px" />
        </div>
      </div>
      <div class="formrow">
        <label>账号别名</label>
        <div class="ctl">
          <a-input v-model:value="addAlias" style="width: 260px" placeholder="可选，如：百度-大号" />
        </div>
      </div>
      <div class="formrow">
        <label>Cookie</label>
        <a-textarea
          v-model:value="addCookies"
          :rows="5"
          placeholder="从网盘网页版 F12 → 网络 → 任一请求的请求头里复制整串 Cookie 粘贴到下面。"
        />
      </div>
      <div style="display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px">
        <a-button @click="addOpen = false">取消</a-button>
        <a-button type="primary" :loading="addSaving" @click="onAddSave">保存并验证</a-button>
      </div>
    </a-modal>

    <!-- 凭据配置弹窗：粘贴整串 Cookie，后端保存即验证 -->
    <a-modal
      v-model:open="credOpen"
      :title="credTitle"
      :confirm-loading="credSaving"
      ok-text="保存并验证"
      @ok="onCredSave"
    >
      <p class="small" style="color: var(--text3); margin-bottom: 10px">
        到对应网盘网页版按 F12 → 网络 → 任一请求的请求头里复制整串 Cookie 粘贴到下面。
        凭据加密存储，任何接口都不会回填明文。
      </p>
      <a-textarea
        v-model:value="credCookies"
        :rows="5"
        placeholder="粘贴整串 Cookie，例如：UID=...; CID=...; SEID=...; __pus=...; __puus=..."
      />
    </a-modal>

    <!-- 默认目标目录：读写「转存配置」的默认条目（key 绑 type，切换网盘强制重建） -->
    <a-modal
      :open="bdOpen"
      :width="480"
      :title="bdTitle"
      ok-text="保存到此处"
      :confirm-loading="bdSaving"
      :destroy-on-close="true"
      @ok="onConfirmBaseDir"
      @update:open="(v: boolean) => (bdOpen = v)"
    >
      <p class="small" style="color: var(--text3); margin-bottom: 10px">
        点文件夹名选中目标目录，点左侧箭头展开子目录；保存写入「转存配置」的默认条目。
      </p>
      <LazyDirTree :key="bdType" :type="bdType" @select="onTreePick" />
      <p class="bd-picked">已选目录：<b>{{ bdPath || '/' }}</b></p>
    </a-modal>

  </div>
</template>

<style scoped>
/* 卡片网格/卡片本体的视觉在 pk.css 共享段（.accgrid/.acc/.acchead/...），这里只补页面私有微调 */
/* 品牌色顶条：与首页网盘卡同款分层（百度蓝/夸克青/115 紫） */
/* 目录选择弹窗底部的「已选目录」回显 */
.bd-picked {
  margin: 12px 0 0;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--surface-2);
  font-size: 12.5px;
  color: var(--text3);
}
.bd-picked b { color: var(--primary); font-weight: 500; }

.acc {
  position: relative;
  overflow: hidden;
  /* 首页网盘卡同款：hover 轻浮起 + 边框和阴影染上品牌色（颜色经 --acc 传入） */
  transition: transform 0.18s, box-shadow 0.18s, border-color 0.18s;
}
.acc:hover {
  border-color: var(--acc);
  box-shadow:
    var(--shadow-lg),
    0 0 22px color-mix(in srgb, var(--acc) 16%, transparent);
}
.acc::before {
  content: "";
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  /* 首页网盘卡同款品牌色光晕条：渐变收边 + 品牌色微发光（颜色经 --acc 传入） */
  background: linear-gradient(90deg, transparent, var(--acc) 35%, var(--acc) 65%, transparent);
  box-shadow: 0 0 10px var(--acc);
}
/* 首页同款扫描线：品牌色自上而下缓缓掠过（console 质感） */
.acc::after {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  height: 40px;
  top: -46px;
  background: linear-gradient(to bottom, transparent, color-mix(in srgb, var(--acc) 7%, transparent), transparent);
  animation: accScan 5.5s linear infinite;
  pointer-events: none;
}
@keyframes accScan {
  0% { top: -46px; }
  70%, 100% { top: 105%; }
}
/* 未配置的卡：整体半透明 + 扫描线停住（与首页 .is-off 同语义） */
.acc.off { opacity: 0.72; }
.acc.off::after { animation-play-state: paused; opacity: 0; }
@media (prefers-reduced-motion: reduce) {
  .acc::after { animation: none; }
}
/* 状态 tag 挤在卡头右侧，去掉共享 .tag 的右边距避免顶着卡片边 */
.acchead .tag {
  margin-right: 0;
}
/* 状态标签已挪进 .accname（紧跟网盘名）：收回从标题继承来的字重，保持标签原观感 */
.acchead .accname .acc-tag {
  font-size: 12px;
  font-weight: 500;
}
</style>
