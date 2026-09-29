<script setup lang="ts">
/* =====================================================================
 * 网盘连接 —— 原型 _shell.html data-view="accounts" 的 Vue3 还原。
 * 三张网盘卡：只展示连接状态，绝不回填凭据明文（设计红线）。
 * 状态读写走 accountStore（内存 mock），动作走 api/modules/accounts.ts。
 * ===================================================================== */
import { computed, ref } from 'vue'
import { message } from 'ant-design-vue'
import { accountStore, ACCOUNT_STATUS_VIEW, type AccountRow } from '@/api/mock/accounts'
import { MAIN_ORDER, DRIVE_META } from '@/api/mock/meta'
import { checkAccount, clearAccount, getSummary, saveBaseDir, saveCredential, type AccountSummary } from '@/api/modules/accounts'
import type { MainDriveType } from '@/types/model'

/** 卡片按 baidu/quark/115 固定顺序（MAIN_ORDER）铺开，store 变了视图自动跟 */
const rows = computed<AccountRow[]>(() => MAIN_ORDER.map((t) => accountStore.accounts[t]))

/** 状态 → tag 类名/文案/圆点（connected/expired/unset 三态映射） */
function view(status: string) {
  return ACCOUNT_STATUS_VIEW[status]
}

/* ===== 默认目标目录：卡片直接填写；保存后后端自动预热该目录的目录树缓存 ===== */
const bdOpen = ref(false)
const bdType = ref<MainDriveType>('quark')
const bdPath = ref('')
const bdSaving = ref(false)
const bdTitle = computed(() => `默认目标目录 · ${DRIVE_META[bdType.value]?.full || bdType.value}`)

function openBaseDir(a: AccountRow) {
  bdType.value = a.type
  bdPath.value = a.base && a.base !== '—' ? a.base : ''
  bdOpen.value = true
}

async function onBaseDirSave() {
  bdSaving.value = true
  try {
    const res = await saveBaseDir(bdType.value, bdPath.value.trim())
    const row = accountStore.accounts[bdType.value]
    if (row) row.base = bdPath.value.trim() || '—'
    bdOpen.value = false
    if (res.primed) message.success(res.message + '（首次打开转存弹窗秒开）')
    else message.info(res.message)
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(detail || '保存失败')
  } finally {
    bdSaving.value = false
  }
}

/* ===== 卡片动作 ===== */
const checking = ref<MainDriveType | null>(null)

async function onCheck(a: AccountRow) {
  checking.value = a.type
  try {
    const res = await checkAccount(a.type)
    // 检测结果回写：last_check 已在 api 里更新，status 以返回为准（真实后端可能探成过期）
    accountStore.accounts[a.type].status = res.status
    if (res.kind === 'success') {
      message.success(res.message)
      await loadSummary(a.type)
    } else if (res.kind === 'warning') message.warning(res.message)
    else message.error(res.message)
  } finally {
    checking.value = null
  }
}

/* ===== 容量 + 会员摘要：connected 时拉取，检测连通成功后刷新 ===== */
const summaries = ref<Record<string, AccountSummary | null>>({})

async function loadSummary(type: MainDriveType) {
  try {
    summaries.value[type] = await getSummary(type)
  } catch {
    summaries.value[type] = null
  }
}

function summaryOf(type: MainDriveType): AccountSummary | null {
  return summaries.value[type] || null
}

function capOf(type: MainDriveType) {
  const cap = summaryOf(type)?.capacity
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
const credType = ref<MainDriveType>('quark')
const credTitle = computed(() => `配置凭据 · ${DRIVE_META[credType.value]?.full || credType.value}`)
const credCookies = ref('')
const credSaving = ref(false)

function onConfig(a: AccountRow) {
  credType.value = a.type
  credCookies.value = ''
  credOpen.value = true
}

async function onCredSave() {
  if (!credCookies.value.trim()) {
    message.warning('请先粘贴 Cookie')
    return
  }
  credSaving.value = true
  try {
    const { nickname } = await saveCredential(credType.value, credCookies.value.trim())
    const row = accountStore.accounts[credType.value]
    if (row) {
      row.status = 'connected'
      row.last_check = new Date().toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
    }
    credOpen.value = false
    message.success(`凭据已保存并验证通过（${nickname}）`)
    await loadSummary(credType.value)
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(detail || '凭据验证失败')
  } finally {
    credSaving.value = false
  }
}

async function onClear(a: AccountRow) {
  await clearAccount(a.type)
  message.success(`已清空「${DRIVE_META[a.type].full}」凭据，状态置为未配置`)
}
</script>

<template>
  <div>
    <!-- 网盘卡片网格：未配置的卡整体半透明（.off） -->
    <div class="accgrid">
      <div v-for="a in rows" :key="a.type" class="acc" :class="{ off: a.status === 'unset' }" :style="{ '--acc': a.color }">
        <div class="acchead">
          <div class="accname">
            <span class="chip" :style="{ background: a.color }">{{ a.short }}</span>
            {{ DRIVE_META[a.type].full }}
          </div>
          <span class="tag acc-tag" :class="view(a.status).cls">
            <span class="dot" :class="view(a.status).dot"></span>{{ view(a.status).label }}
          </span>
          <span
            v-if="a.status === 'connected' && summaryOf(a.type)?.vip && summaryOf(a.type)!.vip!.name !== '普通用户'"
            class="vip-tag"
            :title="summaryOf(a.type)!.vip!.expires ? `会员到期：${summaryOf(a.type)!.vip!.expires}` : ''"
          >✦ {{ summaryOf(a.type)!.vip!.name }}<template v-if="summaryOf(a.type)!.vip!.expires"> · {{ summaryOf(a.type)!.vip!.expires }}</template></span>
        </div>
        <div class="kv"><span>凭据类型</span><b>{{ a.cred_kind }}</b></div>
        <div class="kv"><span>上次检测</span><b>{{ a.last_check }}</b></div>
        <div class="kv">
          <span>默认目标目录</span>
          <b style="display: inline-flex; align-items: center; gap: 6px">
            {{ a.base || '—' }}
            <a-button type="link" size="small" style="padding: 0 2px; height: auto" @click="openBaseDir(a)">编辑</a-button>
          </b>
        </div>
        <div v-if="a.status === 'connected'" class="capblock">
          <template v-if="capOf(a.type)">
            <div class="capbar"><i :class="capClass(capOf(a.type)!.pct)" :style="{ width: capOf(a.type)!.pct + '%' }"></i></div>
            <div class="capmeta">
              <span>已用 {{ capOf(a.type)!.used }} GB / 共 {{ capOf(a.type)!.total }} GB</span>
              <span>{{ capOf(a.type)!.pct }}%</span>
            </div>
          </template>
          <div v-else-if="summaries[a.type] !== undefined" class="small" style="color: var(--text3)">
            {{ summaries[a.type] === null ? '容量信息获取失败' : '暂无容量信息' }}
          </div>
        </div>
        <div class="accbtns">
          <a-button type="primary" size="small" @click="onConfig(a)">配置凭据</a-button>
          <a-button size="small" :loading="checking === a.type" @click="onCheck(a)">检测连通</a-button>
          <a-popconfirm
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

    <!-- 默认目标目录编辑：保存后自动预热目录树缓存 -->
    <a-modal
      v-model:open="bdOpen"
      :title="bdTitle"
      :confirm-loading="bdSaving"
      ok-text="保存"
      @ok="onBaseDirSave"
    >
      <p class="small" style="color: var(--text3); margin-bottom: 10px">
        该网盘的默认保存位置，例如 <code>/影视</code>。保存后会<b>自动加载该目录的目录树缓存</b>——
        首次打开转存弹窗即秒开，不用现场等网盘接口。
      </p>
      <a-input v-model:value="bdPath" placeholder="/影视" allow-clear @press-enter="onBaseDirSave" />
    </a-modal>
  </div>
</template>

<style scoped>
/* 卡片网格/卡片本体的视觉在 pk.css 共享段（.accgrid/.acc/.acchead/...），这里只补页面私有微调 */
/* 品牌色顶条：与首页驾驶舱网盘卡同款分层（百度蓝/夸克青/115 紫） */
.acc {
  position: relative;
  overflow: hidden;
}
.acc::before {
  content: "";
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: var(--acc, var(--border));
  opacity: 0.9;
}
/* 状态 tag 挤在卡头右侧，去掉共享 .tag 的右边距避免顶着卡片边 */
.acchead .tag {
  margin-right: 0;
}
</style>
