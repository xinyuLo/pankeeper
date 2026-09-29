<script setup lang="ts">
/* =====================================================================
 * 网盘连接 —— 原型 _shell.html data-view="accounts" 的 Vue3 还原。
 * 三张网盘卡：只展示连接状态，绝不回填凭据明文（设计红线）。
 * 状态读写走 accountStore（内存 mock），动作走 api/modules/accounts.ts。
 * ===================================================================== */
import { computed, ref } from 'vue'
import { message } from 'ant-design-vue'
import { LoadingOutlined } from '@ant-design/icons-vue'
import LazyDirTree from '@/components/LazyDirTree.vue'
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

/* ===== 新增网盘：列出可扩展的网盘（三家已默认在页；其余适配器开发中） ===== */
const addOpen = ref(false)
const extendable = [
  { type: '123', name: '123 云盘', color: '#fa8c16' },
  { type: 'ali', name: '阿里云盘', color: '#ff6a00' },
  { type: 'xunlei', name: '迅雷网盘', color: '#2db7f5' },
  { type: 'uc', name: 'UC 网盘', color: '#597ef7' },
]

/* ===== 默认目标目录：先配凭据 → 自动缓存文件夹 → 从目录树选位置 ===== */
const priming = ref<MainDriveType | null>(null)
const bdPath = ref('')
const bdOpen = ref(false)
const bdType = ref<MainDriveType>('quark')
const bdTitle = computed(() => `默认目标目录 · ${DRIVE_META[bdType.value]?.full || bdType.value}`)

function openBaseDir(a: AccountRow) {
  bdType.value = a.type
  bdPath.value = a.base && a.base !== '—' ? a.base : '/'
  bdOpen.value = true
}

async function onTreePick(path: string) {
  try {
    const res = await saveBaseDir(bdType.value, path)
    const row = accountStore.accounts[bdType.value]
    if (row) row.base = path
    bdOpen.value = false
    message.success(res.message)
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(detail || '保存失败')
  }
}

/** 配置凭据成功后：自动缓存文件夹（loading 期间卡片上直接可见） */
async function primeFolders(type: MainDriveType) {
  priming.value = type
  try {
    const res = await saveBaseDir(type, accountStore.accounts[type]?.base || '')
  } catch {
    /* 预热失败不阻塞：首次打开转存弹窗会再加载 */
  } finally {
    priming.value = null
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
      await primeFolders(a.type)
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
    await primeFolders(credType.value)
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
    <div style="display: flex; justify-content: flex-end; margin-bottom: 14px">
      <a-button type="primary" @click="addOpen = true">＋ 新增网盘</a-button>
    </div>
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
          <b v-if="priming === a.type" style="color: var(--primary)"><LoadingOutlined /> 正在缓存文件夹…</b>
          <b v-else style="display: inline-flex; align-items: center; gap: 6px">
            {{ a.base && a.base !== '—' ? a.base : '/' }}
            <a-tooltip :title="a.status !== 'connected' ? '请先配置凭据并连通' : '从网盘目录中选择默认保存位置'">
              <a-button
                type="link"
                size="small"
                style="padding: 0 2px; height: auto"
                :disabled="a.status !== 'connected'"
                @click="openBaseDir(a)"
              >配置</a-button>
            </a-tooltip>
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



    <!-- 新增网盘：选择要接入的网盘（未适配的置灰） -->
    <a-modal v-model:open="addOpen" :width="380" title="新增网盘" :footer="null">
      <div class="add-grid">
        <div v-for="d in extendable" :key="d.type" class="add-tile add-item-off">
          <span class="chip" :style="{ background: d.color }">{{ d.name.slice(0, 2) }}</span>
          <div class="add-tile-name">{{ d.name }}</div>
          <a-tag style="margin-right: 0">即将支持</a-tag>
        </div>
      </div>
      <p class="small" style="color: var(--text3); margin: 10px 0 0">
        百度 / 夸克 / 115 已默认在页面上方，配置凭据即可使用。
      </p>
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

    <!-- 默认目标目录：直接展示网盘文件夹树，点了就存 -->
    <a-modal :open="bdOpen" :width="480" :title="bdTitle" :footer="null" @update:open="(v: boolean) => (bdOpen = v)">
      <p class="small" style="color: var(--text3); margin-bottom: 10px">
        从网盘目录中选择默认保存位置，选中即保存（已自动加载目录缓存）。
      </p>
      <LazyDirTree :type="bdType" @select="onTreePick" />
    </a-modal>
  </div>
</template>

<style scoped>
/* 卡片网格/卡片本体的视觉在 pk.css 共享段（.accgrid/.acc/.acchead/...），这里只补页面私有微调 */
/* 品牌色顶条：与首页网盘卡同款分层（百度蓝/夸克青/115 紫） */
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
