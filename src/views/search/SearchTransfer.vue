<script setup lang="ts">
/* 搜索转存主页 —— 原型 _shell.html 搜索段 + parts/search-ui.js 的 Vue 移植。
 * 页面结构：搜索框 → 筛选条卡（频道勾选 + PanSou 来源 + 网盘胶囊 tab）→ 统计卡
 * → 结果表（快速转存/转存/跳转）→ PkPager；底部组装快速转存、转存两个弹窗（互斥）。
 * 检索动效：顶部不确定进度条 → 扫源计数 → 骨架屏 → 结果替换 + 数字滚动。
 * 转存动作一律入队即走（队列引擎在弹窗内调用），本页只负责打开弹窗并保持互斥。 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import PkPager from '@/components/PkPager.vue'
import QuickTransferModal from './QuickTransferModal.vue'
import TransferModal, { type TransferTarget } from './TransferModal.vue'
import { useIsMobile } from '@/composables/useIsMobile'
import { getInitialResults, getPanSouAddr, getSearchChannels, getSearchResults, type SearchChannel } from '@/api/modules/search'
import { getSettings, saveSearchSrc } from '@/api/modules/settings'
import { listDdItems } from '@/api/modules/dd'
import { DRIVE_META } from '@/api/mock/meta'
import type { DriveType, SearchResultItem } from '@/types/model'

/* 手机（<768px）渲染卡片列表代替结果表——393px 宽塞不下 5 列表格 */
const isMobile = useIsMobile()

/** tab 固定顺序 = DRIVE_META 的键序（全部/百度/夸克/115/123/阿里/迅雷/UC，与原型一致） */
const DRIVE_ORDER = Object.keys(DRIVE_META) as DriveType[]

/* ===== 静态文案 ===== */
const T_NO_CRED = '请先到「网盘连接」页配置该网盘凭据'
const T_NO_DD = '请先到「转存配置」页给该网盘添加一个路径'

/* ===== 基础数据 ===== */
const kw = ref('')
const channels = ref<SearchChannel[]>([])
const addr = ref('')
const results = ref<SearchResultItem[]>([])
/** 各网盘是否配过转存目录（快速转存按钮的前置条件，账号级配置在「转存配置」页） */
const ddTypes = ref(new Set<DriveType>())

onMounted(async () => {
  // 首屏走缓存结果（无检索动效），等价原型打开页面时 window.results 已在
  const [chs, a, rows, dds] = await Promise.all([
    getSearchChannels(),
    getPanSouAddr(),
    getInitialResults(),
    listDdItems(),
  ])
  channels.value = chs
  addr.value = a
  results.value = rows
  ddTypes.value = new Set<DriveType>(dds.map((x) => x.type))
  renderStats()
})

/* ===== 频道设置弹窗：白名单存后端 settings.search.channels，空 = 用全部 ===== */
const chCfgOpen = ref(false)
const chFilter = ref('')
const chDraft = ref<string[]>([])

const selectedChannels = computed(() => channels.value.filter((c) => c.on).map((c) => c.name))

function openChannelCfg() {
  chDraft.value = [...selectedChannels.value]
  chFilter.value = ''
  chCfgOpen.value = true
}

const chFiltered = computed(() => {
  const kwTrim = chFilter.value.trim().toLowerCase()
  return channels.value.filter((c) => !kwTrim || c.name.toLowerCase().includes(kwTrim))
})

function chSetAll(on: boolean) {
  for (const c of channels.value) c.on = on
  chDraft.value = channels.value.filter((c) => c.on).map((c) => c.name)
}

/** 草稿与列表勾选双向：勾选变化时同步 draft */
function chToggle(name: string, on: boolean) {
  const set = new Set(chDraft.value)
  if (on) set.add(name)
  else set.delete(name)
  chDraft.value = [...set]
}

async function chSave() {
  const draft = new Set(chDraft.value)
  for (const c of channels.value) c.on = draft.has(c.name)
  try {
    const all = await getSettings()
    all.search.channels = [...draft]
    await saveSearchSrc(all.search)
    chCfgOpen.value = false
    message.success(draft.size ? `已保存：搜索时使用 ${draft.size} 个频道` : '已保存：使用全部频道')
  } catch {
    message.error('保存频道设置失败')
  }
}

/* ===== 网盘 tab ===== */
type TabKey = 'all' | DriveType
const active = ref<TabKey>('all')

const countsBy = computed<Record<string, number>>(() => {
  const c: Record<string, number> = { all: results.value.length }
  for (const t of DRIVE_ORDER) c[t] = 0
  for (const r of results.value) c[r.t] = (c[r.t] || 0) + 1
  return c
})

const tabs = computed(() => [
  { key: 'all' as TabKey, name: '全部', count: countsBy.value.all, color: '' },
  ...DRIVE_ORDER.map((t) => ({ key: t as TabKey, name: DRIVE_META[t].name, count: countsBy.value[t] || 0, color: DRIVE_META[t].color })),
])

function setTab(k: TabKey) {
  if (k === active.value) return
  active.value = k
  page.value = 1 // 换 tab 回第 1 页：否则「第 5 页」切到只有 2 条的 tab 会白屏
  renderStats() // 统计卡跟随当前 tab（单网盘时只出一张卡）
}

/* ===== 筛选 / 分页（假分页：数据全在前端，切片渲染） ===== */
const filtered = computed(() => (active.value === 'all' ? results.value : results.value.filter((r) => r.t === active.value)))
const page = ref(1)
const size = ref(8)
const paged = computed(() => filtered.value.slice((page.value - 1) * size.value, page.value * size.value))

// 结果变少时把页码夹回有效范围（删减/换 tab 后不白屏）
watch(
  () => filtered.value.length,
  (n) => {
    const max = Math.max(1, Math.ceil(n / size.value))
    if (page.value > max) page.value = max
  },
)

/* ===== 检索动效 ===== */
const busy = ref(false)
const barOn = ref(false) // 顶部不确定进度条：滚动中
const barDone = ref(false) // 收束态：拉满再淡出
const scanCount = ref(1) // 「已扫 N 个源」（7 = 七个网盘源）
const elapsed = ref<string | null>(null) // 上一次检索耗时（首屏未知 → 不渲染耗时卡）
const rowEpoch = ref(0) // 结果 tbody 的 key：检索完成后整组重挂载，重放逐行入场动画
let scanTimer: number | undefined
const kwRef = ref()

async function doSearch() {
  if (busy.value) return
  if (!kw.value.trim()) {
    message.warning('请输入搜索关键词')
    return
  }
  busy.value = true
  barOn.value = true
  barDone.value = false
  scanCount.value = 1
  elapsed.value = null
  page.value = 1 // 新一次搜索必须回第 1 页
  kwRef.value?.blur?.()

  // 扫源计数动画：模拟 PanSou 逐源返回（210ms 一跳，封顶 7 个源）
  scanTimer = window.setInterval(() => {
    scanCount.value = Math.min(7, scanCount.value + 1)
  }, 210)

  const t0 = Date.now()
  try {
    results.value = await getSearchResults(kw.value)
    elapsed.value = ((Date.now() - t0) / 1000).toFixed(1) + 's'
    rowEpoch.value++
  } finally {
    window.clearInterval(scanTimer)
    busy.value = false
    renderStats()
    // 收束：进度条拉满 260ms 后淡出（对齐原型 pkBar(false)）
    barOn.value = false
    barDone.value = true
    window.setTimeout(() => (barDone.value = false), 300)
    if (elapsed.value) message.success(`命中 ${results.value.length} 条结果 · 耗时 ${elapsed.value}`)
  }
}

/* ===== 统计卡（跟随当前 tab + 数字滚动） ===== */
interface StatCard {
  key: string
  label: string
  value: number | string
  /** 左侧竖色条用网盘色；命中总数/耗时没有归属 → 退回中性灰 */
  accent?: string
}
const statCards = ref<StatCard[]>([])
const statShown = ref<Record<string, number | string>>({})
const statEpoch = ref(0)
let rollRaf = 0

function renderStats() {
  const list: StatCard[] = []
  if (active.value === 'all') {
    list.push({ key: 'all', label: '命中资源', value: results.value.length })
    for (const t of DRIVE_ORDER) {
      const n = countsBy.value[t] || 0
      if (n > 0) list.push({ key: t, label: DRIVE_META[t].full, value: n, accent: DRIVE_META[t].color })
    }
  } else {
    // 只选一个网盘：只出一张卡（原型 statline.single 行为）
    list.push({
      key: active.value,
      label: `${DRIVE_META[active.value].full}命中`,
      value: countsBy.value[active.value] || 0,
      accent: DRIVE_META[active.value].color,
    })
  }
  // 耗时卡只在检索过后出现（首屏没有可展示的值）
  if (elapsed.value) list.push({ key: 'elapsed', label: '耗时', value: elapsed.value })
  statCards.value = list
  statEpoch.value++ // 换 key 让出场动画重放
  rollStats(list)
}

/** 数字从 0 滚到目标（easeOutCubic，620ms）；耗时是字符串直接给值 */
function rollStats(cards: StatCard[]) {
  cancelAnimationFrame(rollRaf)
  const nums = cards.filter((c): c is StatCard & { value: number } => typeof c.value === 'number')
  for (const c of cards) statShown.value[c.key] = typeof c.value === 'number' ? 0 : c.value
  if (!nums.length) return
  const dur = 620
  const t0 = performance.now()
  const step = (ts: number) => {
    const p = Math.min(1, (ts - t0) / dur)
    const e = 1 - Math.pow(1 - p, 3)
    for (const c of nums) statShown.value[c.key] = Math.round(c.value * e)
    if (p < 1) rollRaf = requestAnimationFrame(step)
    else for (const c of nums) statShown.value[c.key] = c.value
  }
  rollRaf = requestAnimationFrame(step)
}

/* ===== 行内操作三按钮 ===== */
function hasDD(t: DriveType) {
  return ddTypes.value.has(t)
}
/** 快速转存：要凭据 + 该网盘配过转存目录，两者缺一即禁用（title 指出去哪配） */
function quickDisabled(r: SearchResultItem) {
  return !r.ok || !hasDD(r.t)
}
function quickTitle(r: SearchResultItem) {
  return !r.ok ? T_NO_CRED : T_NO_DD
}

/* ===== 两个弹窗：互斥（同一时刻只开一个），Esc 由 antd Modal 自带 ===== */
const qsOpen = ref(false)
const qsType = ref<DriveType | null>(null)
const qsName = ref('')
const qsUrl = ref('')
const qsCode = ref('')
const tmOpen = ref(false)
const tmTarget = ref<TransferTarget | null>(null)

function openQuick(r: SearchResultItem) {
  if (!r.ok || !hasDD(r.t)) return
  tmOpen.value = false
  qsType.value = r.t
  qsName.value = r.n
  qsUrl.value = r.url || ''
  qsCode.value = r.share_code || ''
  qsOpen.value = true
}
function openTransfer(r: SearchResultItem) {
  if (!r.ok) return
  qsOpen.value = false
  tmTarget.value = { type: r.t, name: r.n, size: r.s, url: r.url || '', share_code: r.share_code || '' }
  tmOpen.value = true
}
/** 跳转：真实系统新开分享链接；mock 没有链接，给个反馈（原型同款 toast） */
function onJump(r: SearchResultItem) {
  message.info(`已在新标签打开原分享：${r.n}`)
}

onUnmounted(() => {
  window.clearInterval(scanTimer)
  cancelAnimationFrame(rollRaf)
})
</script>

<template>
  <div>
    <!-- 顶部不确定进度条：检索时挂到视口顶部（原型 pk-topbar，left 跟侧栏宽对齐） -->
    <div class="pk-topbar" :class="{ on: barOn, done: barDone }"><i /></div>

    <!-- 搜索框：Enter 与按钮同一条路径 -->
    <div class="searchwrap">
      <a-input ref="kwRef" v-model:value="kw" class="kw-input" placeholder="输入片名 / 关键词，空格分隔" @press-enter="doSearch" />
      <a-button type="primary" class="btn-search" :loading="busy" @click="doSearch">搜 索</a-button>
    </div>

    <!-- 筛选条 + 网盘 tab 合在一张卡里：两者都是收窄结果范围的控制项，分两块白卡会显得零碎 -->
    <div class="card st-flush st-mb">
      <div class="filterbar">
        <!-- 频道摘要胶囊条：整条可点击 = 打开频道设置（与上方 tab 同款交互） -->
        <div class="ch-strip" title="点击管理搜索频道" @click="openChannelCfg">
          <span class="muted">搜索源频道</span>
          <template v-if="selectedChannels.length === 0">
            <span class="ch-chip ch-all">全部频道 · {{ channels.length }}</span>
          </template>
          <template v-else>
            <span v-for="c in selectedChannels.slice(0, 3)" :key="c" class="ch-chip">{{ c }}</span>
            <span v-if="selectedChannels.length > 3" class="ch-chip ch-more">+{{ selectedChannels.length - 3 }}</span>
          </template>
          <span class="ch-edit">✎ 管理</span>
        </div>
        <span class="st-flex1"></span>
        <span class="small muted">来源 PanSou · {{ addr }}</span>
      </div>

      <!-- 横向网盘 tab：点击筛选 + 回第 1 页；右侧常驻扫描状态 -->
      <div class="pktabs">
        <div
          v-for="t in tabs"
          :key="t.key"
          class="pktab"
          :class="{ on: active === t.key, dim: t.key !== 'all' && !t.count }"
          @click="setTab(t.key)"
        >
          <i v-if="t.count" class="pkdot" :style="{ background: t.color }"></i>
          {{ t.name }}<span class="pknum">{{ t.count }}</span>
        </div>
        <span class="pk-tab-scan">
          <template v-if="busy"><span class="pkspin"></span>正在检索… 已扫 {{ scanCount }} 个源</template>
          <template v-else><span class="pkdot-live"></span>已检索 {{ countsBy.all }} 条</template>
        </span>
      </div>
    </div>

    <!-- 统计卡：跟随当前 tab 动态渲染；换 key 重放出场动画；数字滚动 -->
    <div v-if="statCards.length" :key="statEpoch" class="statline enter" :class="{ single: active !== 'all' }">
      <div
        v-for="it in statCards"
        :key="it.key"
        class="stat"
        :class="{ wide: active !== 'all' }"
        :style="it.accent ? { '--pk-accent': it.accent } : undefined"
      >
        <b>{{ statShown[it.key] ?? it.value }}</b>
        <span>{{ it.label }}</span>
      </div>
    </div>

    <!-- 结果表 + 分页同一张白卡（padding:0 的卡里表尾不夹灰缝）；手机端换卡片列表 -->
    <div class="card st-flush st-res" :class="{ enter: rowEpoch > 0 }">
      <table v-if="!isMobile">
        <thead>
          <tr>
            <th style="width: 50%">资源名称</th>
            <th>来源</th>
            <th>大小</th>
            <th>分享时间</th>
            <th style="width: 210px">操作</th>
          </tr>
        </thead>

        <!-- 骨架屏：检索中给 7 行占位（宽度错落更像真内容） -->
        <tbody v-if="busy" key="skel">
          <tr v-for="(w, i) in ['72%', '54%', '66%', '48%', '60%', '44%', '70%']" :key="i" class="skel-tr">
            <td><span class="skel" :style="{ width: w }"></span></td>
            <td><span class="skel" style="width: 56px"></span></td>
            <td><span class="skel" style="width: 44px"></span></td>
            <td><span class="skel" style="width: 52px"></span></td>
            <td><span class="skel" style="width: 74px; height: 26px; border-radius: 6px"></span></td>
          </tr>
        </tbody>

        <tbody v-else :key="'rows' + rowEpoch">
          <tr v-for="(r, i) in paged" :key="r.t + '-' + r.n" :style="{ animationDelay: 0.03 * i + 's' }">
            <td>
              <div class="st-namecell">
                <span class="srcbar" :style="{ background: DRIVE_META[r.t].color }"></span>
                <span class="resname" :title="r.n">{{ r.n }}</span>
                <span v-if="r.hot" class="tag t-123 st-hot">极速</span>
              </div>
            </td>
            <td><span class="tag" :class="DRIVE_META[r.t].tag">{{ DRIVE_META[r.t].full }}</span></td>
            <td class="small muted">{{ r.s }}</td>
            <td class="small muted">{{ r.d }}</td>
            <td>
              <!-- 三按钮前置条件各不相同：快速转存要凭据+转存配置；转存只要凭据；跳转常驻 -->
              <div class="rowbtns">
                <button class="btn btn-quick" :disabled="quickDisabled(r)" :title="quickTitle(r)" @click="openQuick(r)">快速转存</button>
                <button class="btn btn-trans" :disabled="!r.ok" :title="T_NO_CRED" @click="openTransfer(r)">转存</button>
                <span class="rb-sep"></span>
                <button class="btn btn-jump" @click="onJump(r)">跳转</button>
              </div>
            </td>
          </tr>
          <tr v-if="!paged.length">
            <td colspan="5">
              <div class="pk-empty">
                <b>该网盘暂无命中结果</b>换个网盘 tab 或调整关键词试试
              </div>
            </td>
          </tr>
        </tbody>
      </table>

      <!-- 手机端：单条结果一张卡（名称两行 + 来源/大小/时间 + 三按钮），动作与表格版同一批 handler -->
      <div v-else class="st-cards" :key="'mrows' + rowEpoch">
        <template v-if="busy">
          <div v-for="(w, i) in [7, 6, 8, 5, 7, 6, 8]" :key="i" class="st-card-item">
            <div class="st-card-name"><span class="skel" :style="{ width: w * 10 + '%' }"></span></div>
            <div class="st-card-meta"><span class="skel" style="width: 52px"></span><span class="skel" style="width: 44px"></span></div>
          </div>
        </template>
        <template v-else>
          <div
            v-for="(r, i) in paged"
            :key="r.t + '-' + r.n"
            class="st-card-item"
            :style="{ animationDelay: 0.03 * i + 's' }"
          >
            <div class="st-card-name">
              <span class="srcbar" :style="{ background: DRIVE_META[r.t].color }"></span>
              <span class="st-card-title">{{ r.n }}</span>
              <span v-if="r.hot" class="tag t-123 st-hot">极速</span>
            </div>
            <div class="st-card-meta">
              <span class="tag" :class="DRIVE_META[r.t].tag">{{ DRIVE_META[r.t].full }}</span>
              <span class="small muted">{{ r.s }}</span>
              <span class="small muted st-card-date">{{ r.d }}</span>
            </div>
            <div class="rowbtns st-card-ops">
              <button class="btn btn-quick" :disabled="quickDisabled(r)" :title="quickTitle(r)" @click="openQuick(r)">快速转存</button>
              <button class="btn btn-trans" :disabled="!r.ok" :title="T_NO_CRED" @click="openTransfer(r)">转存</button>
              <span class="rb-sep"></span>
              <button class="btn btn-jump" @click="onJump(r)">跳转</button>
            </div>
          </div>
          <div v-if="!paged.length" class="pk-empty">
            <b>该网盘暂无命中结果</b>换个网盘 tab 或调整关键词试试
          </div>
        </template>
      </div>
      <PkPager v-model:current="page" v-model:pageSize="size" :total="filtered.length" />
    </div>



    <!-- 快速转存弹窗（qsMask）：凭转存配置直入队列 -->
    <QuickTransferModal v-model:open="qsOpen" :type="qsType" :share-name="qsName" :share-url="qsUrl" :share-code="qsCode" />
    <!-- 转存弹窗（transferMask）：分享树勾选 + 目标目录树 -->
    <TransferModal v-model:open="tmOpen" :target="tmTarget" />
  </div>
  <!-- 频道设置弹窗：白名单为空 = 使用全部频道 -->
  <a-modal v-model:open="chCfgOpen" title="搜索频道设置" :width="520" @ok="chSave">
    <div style="display: flex; gap: 8px; margin-bottom: 10px">
      <a-input v-model:value="chFilter" placeholder="按频道名过滤" allow-clear />
      <a-button @click="chSetAll(true)">全选</a-button>
      <a-button @click="chSetAll(false)">清空</a-button>
    </div>
    <div style="max-height: 320px; overflow: auto; border: 1px solid var(--split); border-radius: 8px; padding: 8px 12px">
      <a-checkbox
        v-for="c in chFiltered"
        :key="c.name"
        :checked="chDraft.includes(c.name)"
        style="display: flex; padding: 4px 0"
        @change="(e: any) => chToggle(c.name, e.target.checked)"
      >{{ c.name }}</a-checkbox>
      <div v-if="chFiltered.length === 0" class="small muted" style="text-align: center; padding: 16px 0">
        没有匹配的频道
      </div>
    </div>
    <p class="small" style="color: var(--text3); margin: 10px 0 0">
      不勾任何频道 = 每次搜索使用 PanSou 的全部频道（{{ channels.length }} 个）。白名单保存在后端设置里。
    </p>
  </a-modal>
</template>
