<script setup lang="ts">
/* 搜索转存主页 —— 原型 _shell.html 搜索段 + parts/search-ui.js 的 Vue 移植。
 * 页面结构：搜索框 → 筛选条卡（频道勾选 + PanSou 来源 + 网盘胶囊 tab）→ 统计卡
 * → 结果表（快速转存/转存/跳转）→ PkPager；底部组装快速转存、转存两个弹窗（互斥）。
 * 检索动效：顶部不确定进度条 → 扫源计数 → 骨架屏 → 结果替换 + 数字滚动。
 * 转存动作一律入队即走（队列引擎在弹窗内调用），本页只负责打开弹窗并保持互斥。 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { CopyOutlined, FolderOpenOutlined } from '@ant-design/icons-vue'
import PkPager from '@/components/PkPager.vue'
import QuickTransferModal from './QuickTransferModal.vue'
import TransferModal, { type TransferTarget } from './TransferModal.vue'
import ShareFilesModal from '@/views/auto/ShareFilesModal.vue'
import { useIsMobile } from '@/composables/useIsMobile'
import { checkShareLink, getInitialResults, getPanSouAddr, getSearchChannels, getSearchResults, getSearchShareFiles, type SearchChannel, getEngineHealth, getEngineHealthCached } from '@/api/modules/search'
import { getSettings, saveSearchSrc } from '@/api/modules/settings'
import { listDdItems } from '@/api/modules/dd'
import { ddStore } from '@/api/mock/dd'
import { DRIVE_META } from '@/api/mock/meta'
import { loadSearchCache, saveSearchCache } from '@/views/search/searchCache'
import type { DriveType, SearchResultItem } from '@/types/model'

/* 手机（<768px）渲染卡片列表代替结果表——393px 宽塞不下 5 列表格 */
const isMobile = useIsMobile()

/** tab 固定顺序（全部/百度/夸克/115/磁力/123/阿里/迅雷/UC）
 * tab 顺序：主力转存盘在前（百度→夸克→115→123→阿里），其余靠后；
 * 磁力不是网盘转存不了，按用户 2026-10-06 要求排 115 后面 */
const DRIVE_ORDER: DriveType[] = ['baidu', 'quark', '115', 'magnet', '123', 'ali', 'xunlei', 'uc']

/* ===== 静态文案 ===== */
const T_NO_CRED = '请先到「网盘连接」页配置该网盘凭据'
const T_NO_DD = '请先到「转存配置」页给该网盘添加一个路径'

/* ===== 基础数据 ===== */
const router = useRouter()

const kw = ref('')
const channels = ref<SearchChannel[]>([])
/** PanSou 服务地址（后端下发，不暴露给用户）；空 = 未配置，整个搜索功能不可用。
 *  addrReady 之前不算"未配置"——否则进页一瞬间会闪"未配置/离线"引导。 */
const addr = ref('')
const addrReady = ref(false)
const pansouMissing = computed(() => addrReady.value && !addr.value.trim())
/** 首屏接口是否已落定：落定前空态卡（含去配置引导）一律不渲染，防进页闪屏 */
const booted = ref(false)

/** 未配置 PanSou 时的引导：带路径说清楚去哪儿填 */
function goPansouCfg() {
  router.push({ name: 'settings' })
}
/** 引擎状态胶囊（右上角）：先吃后端缓存立即定调，再用实时探测静默覆盖。
 *  缓存由每日探活 + 设置页"测试 PanSou"刷新。不暴露地址，IP 属隐私 */
const engineOk = ref<boolean | null>(null)
const results = ref<SearchResultItem[]>([])
/** 各网盘是否配过转存目录（快速转存按钮的前置条件，账号级配置在「转存配置」页）。
 *  直接从 reactive 的 ddStore 派生 —— 在「转存配置」页增删改后这里即时同步，不必刷新页面。 */
const ddTypes = computed(() => new Set<DriveType>(ddStore.items.map((x) => x.type)))

onMounted(async () => {
  // 首屏走缓存结果（无检索动效），等价原型打开页面时 window.results 已在
  // listDdItems() 只为把后端配置灌进 ddStore（ddTypes 是它的 computed，不在此赋值）
  // ⚠️ 每个请求单独兜底——任何一个被代理偶发抖动打死都不能拖垮整个首屏
  //（曾经整组 Promise.all 一断，engineOk 卡「检测中」、结果区空白）
  const [chs, a, rows, , cached] = await Promise.all([
    getSearchChannels().catch(() => []),
    getPanSouAddr().catch(() => ''),
    getInitialResults().catch(() => []),
    listDdItems().catch(() => undefined),
    getEngineHealthCached().catch(() => ({ ok: null as boolean | null, checked_at: '' })),
  ])
  channels.value = chs
  addr.value = a
  addrReady.value = true
  // 缓存状态先渲染（不再闪"离线"），随后实时探测静默覆盖。
  // 但单次探测失败/超时（8s 窗口里的偶发抖动）不打翻缓存里的「在线」——
  // 真下线时每日探活会刷新缓存、检索本身也会报错，别让胶囊闪一下离线
  engineOk.value = cached.ok
  getEngineHealth()
    .then((h) => {
      if (h.ok || engineOk.value !== true) engineOk.value = h.ok
    })
    .catch(() => {
      // 探测请求本身挂了（代理偶发抖动）：别卡在「检测中」——
      // 缓存有基调（true）就保持，没有就按离线收场
      if (engineOk.value === null) engineOk.value = false
    })
  results.value = rows
  // 有跨页保留的上次搜索：整体恢复（关键词/结果/tab/耗时），不动首屏缓存
  const cachedSearch = loadSearchCache()
  if (cachedSearch) {
    kw.value = cachedSearch.kw
    active.value = cachedSearch.active
    elapsed.value = cachedSearch.elapsed
    results.value = cachedSearch.results
  }
  renderStats()
  // 首屏数据（地址/缓存结果）落定后才允许渲染空态卡——否则进页一瞬间会闪
  // 「暂无搜索结果 / 去配置引导」，数据到了又跳变
  booted.value = true
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
  // 统计卡常驻，点卡只挪高亮不重渲染（重渲染会闪一场出场动画，像卡片消失）
}

/* ===== 筛选 / 分页（假分页：数据全在前端，切片渲染） ===== */
/* ===== 排序 ===== */
/**
 * 资源质量分（从资源名提取，越高越值得转）：
 * 分辨率(4K>1080>720) > 片源(原盘/REMUX > WEB-DL > HDTV) > 音轨(杜比全景声/TrueHD > DTS-HD；7.1 > 5.1 > 2.0) > HDR(杜比视界/HDR10+) > 编码(H.265 > H.264)
 */
function qualityScore(name: string): number {
  const n = name.toUpperCase()
  let s = 0
  if (/2160P?|4K/.test(n)) s += 400
  else if (/1080[P】]?|1080/.test(n)) s += 300
  else if (/720/.test(n)) s += 200
  if (/REMUX|原盘|BLURAY|BLU-RAY|BDMV|UHD/.test(n)) s += 50
  else if (/WEB-?DL/.test(n)) s += 30
  else if (/WEB/.test(n)) s += 20
  else if (/HDTV/.test(n)) s += 10
  if (/ATMOS|全景声|TRUEHD|DDP|DD\+|杜比|EAC3|AC3/.test(n)) s += 30
  else if (/DTS-?HD|DTS/.test(n)) s += 26
  if (/7\.1/.test(n)) s += 20
  else if (/5\.1/.test(n)) s += 12
  else if (/2\.0/.test(n)) s += 4
  if (/杜比视界|DOLBY.?VISION|\bDV\b/.test(n)) s += 15
  else if (/HDR10\+|HDR/.test(n)) s += 10
  if (/H\.?265|HEVC|X265/.test(n)) s += 6
  else if (/H\.?264|X264|AVC/.test(n)) s += 3
  return s
}

/** 网盘分组序（与 tab 顺序一致），全部 tab 下先按盘分组、组内按质量分降序 */
const driveRank = computed<Record<string, number>>(() => {
  const r: Record<string, number> = {}
  DRIVE_ORDER.forEach((t, i) => (r[t] = i))
  return r
})

const filtered = computed(() => {
  const rank = driveRank.value
  const sorted = [...results.value].sort((a, b) => {
    if (active.value === 'all') {
      const d = (rank[a.t] ?? 99) - (rank[b.t] ?? 99)
      if (d !== 0) return d
    }
    return qualityScore(b.n) - qualityScore(a.n)
  })
  return active.value === 'all' ? sorted : sorted.filter((r) => r.t === active.value)
})
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
const scanCount = ref(1) // 「已扫 N 个源」（8 = 八路来源：七网盘 + 磁力）
const elapsed = ref<string | null>(null) // 上一次检索耗时（首屏未知 → 不渲染耗时卡）
const rowEpoch = ref(0) // 结果 tbody 的 key：检索完成后整组重挂载，重放逐行入场动画
let scanTimer: number | undefined
const kwRef = ref()

async function doSearch() {
  if (busy.value) return
  // 没配 PanSou 就发请求只会拿到 502，先把话说明白
  if (pansouMissing.value) {
    message.error('还没配置 PanSou 搜索服务，请到「系统设置 → 搜索源」填写 PanSou 地址', 5)
    return
  }
  if (!kw.value.trim()) {
    message.warning('请输入搜索关键词')
    return
  }
  busy.value = true
  scanCount.value = 1
  elapsed.value = null
  page.value = 1 // 新一次搜索必须回第 1 页
  kwRef.value?.blur?.()

  // 扫源计数动画：模拟 PanSou 逐源返回（210ms 一跳，封顶 8 个源）
  scanTimer = window.setInterval(() => {
    scanCount.value = Math.min(8, scanCount.value + 1)
  }, 210)

  const t0 = Date.now()
  try {
    results.value = await getSearchResults(kw.value)
    elapsed.value = ((Date.now() - t0) / 1000).toFixed(1) + 's'
    rowEpoch.value++
  } catch (e: unknown) {
    // 后端 502（PanSou 地址错 / 连不上 / 返回异常）：原样吐给用户，不要静默
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(detail || '检索失败，请检查「系统设置 → 搜索源」里的 PanSou 地址', 5)
    results.value = []
  } finally {
    window.clearInterval(scanTimer)
    busy.value = false
    renderStats()
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
  // 卡片常驻：命中 + 全部网盘（含 0 计数），与 tab 那排同构。点卡只挪 sel 高亮，
  // 列表本身不随 tab 变——此前「单网盘只出一张卡」的收缩模式已按需求撤掉。
  const list: StatCard[] = [{ key: 'all', label: '命中资源', value: results.value.length }]
  for (const t of DRIVE_ORDER) {
    list.push({ key: t, label: DRIVE_META[t].full, value: countsBy.value[t] || 0, accent: DRIVE_META[t].color })
  }
  // 耗时不再占一张卡：PC 进「已检索 N 条」尾缀，手机进频道行右缘（见模板）
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

/* ===== 转存前死活预检（2026-10-06 用户定稿：点了先探，死链直接不让过）=====
 * pansou /api/check/links 单条透传（服务端有缓存，重复点很快）。
 * 只拦 bad；locked（需提取码）/uncertain（无法判定）/unknown（检测器不可用）一律放行，
 * 别让检测器挂掉把转存也挡死。检测中该行按钮禁用防连点。 */
const checkingRows = ref(new Set<string>())
function rowKeyOf(r: SearchResultItem) {
  return `${r.t}|${r.url}`
}
function isChecking(r: SearchResultItem) {
  return checkingRows.value.has(rowKeyOf(r))
}
async function aliveOrBlock(r: SearchResultItem): Promise<boolean> {
  if (!r.url || r.t === 'magnet') return true
  const key = rowKeyOf(r)
  if (checkingRows.value.has(key)) return false
  checkingRows.value = new Set(checkingRows.value).add(key)
  // 检测可能打 pansou 真探测（秒级）：300ms 还没回来就提示"正在检测"，别让用户以为卡死；
  // 缓存命中（点过查看文件）毫秒级返回，提示根本不会出现
  let tipShown = false
  const tipTimer = window.setTimeout(() => {
    tipShown = true
    message.loading({ content: '正在检测链接有效性…', key: 'linkcheck', duration: 0 })
  }, 300)
  try {
    const res = await checkShareLink(r.t, r.url, r.share_code || '')
    if (res.state === 'bad') {
      if (tipShown) message.warning({ content: `分享已失效（${res.summary || '链接检测未通过'}），不转了`, key: 'linkcheck', duration: 5 })
      else message.warning(`分享已失效（${res.summary || '链接检测未通过'}），不转了`, 5)
      return false
    }
    if (tipShown) message.success({ content: '链接有效', key: 'linkcheck', duration: 1 })
    return true
  } catch {
    return true // 检测挂了/超时：放行，别挡转存
  } finally {
    window.clearTimeout(tipTimer)
    if (!tipShown) message.destroy('linkcheck')
    const next = new Set(checkingRows.value)
    next.delete(key)
    checkingRows.value = next
  }
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

async function openQuick(r: SearchResultItem) {
  if (!r.ok || r.t === 'magnet' || !hasDD(r.t)) return // 磁力没法转存（按钮也不会出现，双保险）
  if (!(await aliveOrBlock(r))) return
  tmOpen.value = false
  qsType.value = r.t
  qsName.value = defaultName(r)
  qsUrl.value = r.url || ''
  qsCode.value = r.share_code || ''
  qsOpen.value = true
}
async function openTransfer(r: SearchResultItem) {
  if (!r.ok || r.t === 'magnet') return // 磁力没法转存（按钮也不会出现，双保险）
  if (!(await aliveOrBlock(r))) return
  qsOpen.value = false
  tmTarget.value = { type: r.t, name: defaultName(r), size: r.s, url: r.url || '', share_code: r.share_code || '' }
  tmOpen.value = true
}

/* ===== 默认任务名（用户 2026-10-04 定稿）=====
 * 不再用 pansou 给的 note 当默认名（太脏还带 emoji）：默认用搜索框关键词（去空格），
 * 再从 pansou 名里提取 4 位年份贴到后面——「给阿嬷的情书」+ pansou 名带 (2026)
 * → 「给阿嬷的情书 (2026)」；pansou 名没年份就不贴；关键词本身已带年份也不贴 */
const YEAR_RE = /(19|20)\d{2}/
function defaultName(r: SearchResultItem): string {
  const base = kw.value.trim()
  if (!base) return r.n
  if (YEAR_RE.test(base)) return base
  const m = YEAR_RE.exec(r.n || '')
  return m ? `${base} (${m[0]})` : base
}
/** 跳转：真实系统新开分享链接；mock 没有链接，给个反馈（原型同款 toast） */
function onJump(r: SearchResultItem) {
  if (!r.url) {
    message.warning('该结果没有分享链接')
    return
  }
  window.open(r.url, '_blank', 'noopener')
}

/* ===== 行内「查看文件 / 复制链接」（对齐自动转存行操作，放来源 tag 后面） ===== */
const sfOpen = ref(false)
const sfRec = ref<SearchResultItem | null>(null)
function onViewFiles(r: SearchResultItem) {
  if (!r.url) {
    message.warning('该结果没有分享链接')
    return
  }
  sfRec.value = r
  sfOpen.value = true
}
async function onCopy(r: SearchResultItem) {
  if (!r.url) {
    message.warning('该结果没有分享链接')
    return
  }
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(r.url)
    } else {
      // 非安全上下文（http 部署）兜底：临时 textarea + execCommand
      const ta = document.createElement('textarea')
      ta.value = r.url
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
    }
    message.success('已复制分享链接')
  } catch {
    message.error('复制失败')
  }
}

onUnmounted(() => {
  window.clearInterval(scanTimer)
  cancelAnimationFrame(rollRaf)
  // 快照当前搜索状态，回页面时原样恢复（searchCache.ts 模块级缓存）
  saveSearchCache({ kw: kw.value, results: results.value, active: active.value, elapsed: elapsed.value })
})
</script>

<template>
  <div>
    <!-- 顶部不确定进度条已按需求移除（动画速度观感差）；检索状态由骨架屏 + 扫源计数承担 -->

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
            <!-- 手机端不铺蓝色频道胶囊，只留一条数量摘要（点整条进频道设置看全量） -->
            <template v-if="!isMobile">
              <span v-for="c in selectedChannels.slice(0, 3)" :key="c" class="ch-chip">{{ c }}</span>
              <span v-if="selectedChannels.length > 3" class="ch-chip ch-more">+{{ selectedChannels.length - 3 }}</span>
            </template>
            <span v-else class="ch-chip ch-more">+{{ selectedChannels.length }}</span>
          </template>
          <span class="ch-edit">✎ 管理</span>
        </div>
        <span class="st-flex1"></span>
        <!-- 手机端耗时位：频道行右缘（PC 的耗时在上方 pk-tab-scan 里，这里 v-if 掉） -->
        <span v-if="isMobile && elapsed" class="ch-elapsed">耗时 {{ elapsed }}</span>
        <span class="engine-pill" :class="{ ok: engineOk === true, bad: engineOk === false || pansouMissing }">
          <span class="ep-dot"></span>
          <template v-if="pansouMissing">检索引擎未配置</template>
          <template v-else-if="engineOk === null">引擎状态检测中…</template>
          <template v-else-if="engineOk">检索引擎在线</template>
          <template v-else>检索引擎离线</template>
        </span>
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
          <i class="pkdot" :style="{ background: t.color }"></i>
          {{ t.name }}<span class="pknum">{{ t.count }}</span>
        </div>
        <span class="pk-tab-scan">
          <template v-if="busy"><span class="pkspin"></span>正在检索… 已扫 {{ scanCount }} 个源</template>
          <template v-else>
            <span class="pkdot-live"></span>已检索 {{ countsBy.all }} 条<template v-if="elapsed"> · 耗时 {{ elapsed }}</template>
          </template>
        </span>
      </div>

      <!-- 检索流动条：贴住筛选卡底边，仅检索中展示 -->
      <div v-if="busy" class="pk-strip" aria-hidden="true"><i class="pk-strip-fill"></i></div>
    </div>

    <!-- 统计卡常驻（命中 + 各网盘），点卡 = 切网盘 tab，只挪高亮不重排。
         手机上 tab 那排隐藏（见样式 media），统计卡就是手机端的网盘筛选入口；
         sel（紫环）：手机端必标，桌面端 tab 排可见、只标单盘卡 -->
    <div v-if="statCards.length" :key="statEpoch" class="statline enter">
      <div
        v-for="it in statCards"
        :key="it.key"
        class="stat clickable"
        :class="{ sel: active === it.key && (isMobile || it.key !== 'all') }"
        :style="it.accent ? { '--pk-accent': it.accent } : undefined"
        @click="setTab(it.key as TabKey)"
      >
        <b>{{ statShown[it.key] ?? it.value }}</b>
        <span>{{ it.label }}</span>
      </div>
    </div>

    <!-- 结果表 + 分页同一张白卡（padding:0 的卡里表尾不夹灰缝）；手机端换卡片列表 -->
    <div class="card st-flush st-res" :class="{ enter: rowEpoch > 0, 'is-empty': booted && !busy && !paged.length }">
      <!-- 空态：首屏数据落定后才渲染（booted），默认常规空态；接口确认没配 PanSou 才换成配置引导 -->
      <div v-if="booted && !busy && !paged.length" class="pk-empty-state">
        <template v-if="pansouMissing">
          <div class="pk-es-ico is-warn">⚙</div>
          <div class="pk-es-title">还没配置 PanSou 搜索服务</div>
          <div class="pk-es-sub">PanKeeper 的搜索结果全部来自 PanSou，地址填好之前搜不出任何东西</div>
          <a-button type="primary" class="pk-es-btn" @click="goPansouCfg">去「系统设置 → 搜索源」配置</a-button>
        </template>
        <template v-else>
          <div class="pk-es-ico">🔍</div>
          <div class="pk-es-title">暂无搜索结果</div>
          <div class="pk-es-sub">输入关键词开始检索，或切换上方网盘筛选试试</div>
        </template>
      </div>
      <!-- 检索中：能量环 + 放大镜动画（纯 CSS/SVG，无水印无体积） -->
      <div v-if="busy" class="st-searching">
        <div class="ss-orbit">
          <i class="ss-glow g1"></i>
          <i class="ss-glow g2"></i>
          <div class="ss-ring r-dash"></div>
          <div class="ss-ring r-solid"><i class="ss-dot"></i></div>
          <div class="ss-ring r-arc"></div>
          <div class="ss-core">
            <svg viewBox="0 0 48 48" fill="none">
              <circle cx="20" cy="20" r="11" stroke="#fff" stroke-width="3.5" />
              <path d="M29 29 L39 39" stroke="#fff" stroke-width="4.5" stroke-linecap="round" />
            </svg>
          </div>
        </div>
        <div class="ss-text">正在全网检索资源<i></i><i></i><i></i></div>
        <div class="ss-sub">已扫 {{ scanCount }} 个频道 · 结果按网盘自动分类</div>
      </div>

      <template v-else-if="paged.length">
      <table v-if="!isMobile" class="st-table">
        <thead>
          <tr>
            <th style="width: 50%">资源名称</th>
            <th>来源</th>
            <th>大小</th>
            <th>分享时间</th>
            <th style="width: 210px">操作</th>
          </tr>
        </thead>

        <tbody :key="'rows' + rowEpoch">
          <tr v-for="(r, i) in paged" :key="r.t + '-' + r.n" :style="{ animationDelay: 0.03 * i + 's' }">
            <td>
              <div class="st-namecell">
                <span class="srcbar" :style="{ background: DRIVE_META[r.t].color }"></span>
                <span class="resname" :title="r.n">{{ r.n }}</span>
                <span v-if="r.hot" class="tag t-123 st-hot">极速</span>
              </div>
            </td>
            <td>
              <span class="st-srccell">
                <span class="tag" :class="DRIVE_META[r.t].tag">{{ DRIVE_META[r.t].full }}</span>
                <a-tooltip v-if="r.url && r.t !== 'magnet'" title="查看文件"><button class="pa-ico pa-ico-view" @click.stop="onViewFiles(r)"><FolderOpenOutlined /></button></a-tooltip>
                <a-tooltip v-if="r.url" :title="r.t === 'magnet' ? '复制磁力链接' : '复制链接'"><button class="pa-ico pa-ico-copy" @click.stop="onCopy(r)"><CopyOutlined /></button></a-tooltip>
              </span>
            </td>
            <td class="small muted">{{ r.s }}</td>
            <td class="small muted">{{ r.d }}</td>
            <td>
              <!-- 三按钮前置条件各不相同：快速转存要凭据+转存配置；转存只要凭据；跳转常驻。
                   磁力没法转存网盘：只留复制磁力 -->
              <div class="rowbtns">
                <template v-if="r.t !== 'magnet'">
                  <button class="btn btn-quick" :disabled="quickDisabled(r) || isChecking(r)" :title="isChecking(r) ? '正在检测链接…' : quickTitle(r)" @click="openQuick(r)">快速转存</button>
                  <button class="btn btn-trans" :disabled="!r.ok || isChecking(r)" :title="isChecking(r) ? '正在检测链接…' : T_NO_CRED" @click="openTransfer(r)">转存</button>
                  <span class="rb-sep"></span>
                  <button class="btn btn-jump" @click="onJump(r)">跳转</button>
                </template>
                <button v-else class="btn btn-jump" @click="onCopy(r)">复制磁力</button>
              </div>
            </td>
          </tr>

        </tbody>
      </table>

      <!-- 手机端：单条结果一张卡（名称两行 + 来源/大小/时间 + 三按钮），动作与表格版同一批 handler -->
      <div v-else class="st-cards" :key="'mrows' + rowEpoch">
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
              <a-tooltip v-if="r.url && r.t !== 'magnet'" title="查看文件"><button class="pa-ico pa-ico-view" @click.stop="onViewFiles(r)"><FolderOpenOutlined /></button></a-tooltip>
              <a-tooltip v-if="r.url" :title="r.t === 'magnet' ? '复制磁力链接' : '复制链接'"><button class="pa-ico pa-ico-copy" @click.stop="onCopy(r)"><CopyOutlined /></button></a-tooltip>
              <span class="small muted">{{ r.s }}</span>
              <span class="small muted st-card-date">{{ r.d }}</span>
            </div>
            <div class="rowbtns st-card-ops">
              <template v-if="r.t !== 'magnet'">
                <button class="btn btn-quick" :disabled="quickDisabled(r) || isChecking(r)" :title="isChecking(r) ? '正在检测链接…' : quickTitle(r)" @click="openQuick(r)">快速转存</button>
                <button class="btn btn-trans" :disabled="!r.ok || isChecking(r)" :title="isChecking(r) ? '正在检测链接…' : T_NO_CRED" @click="openTransfer(r)">转存</button>
                <span class="rb-sep"></span>
                <button class="btn btn-jump" @click="onJump(r)">跳转</button>
              </template>
              <button v-else class="btn btn-jump" @click="onCopy(r)">复制磁力</button>
            </div>
          </div>
      </div>
      <PkPager v-if="paged.length" v-model:current="page" v-model:pageSize="size" :total="filtered.length" />
      </template>
    </div>



    <!-- 快速转存弹窗（qsMask）：凭转存配置直入队列 -->
    <QuickTransferModal v-model:open="qsOpen" :type="qsType" :share-name="qsName" :share-url="qsUrl" :share-code="qsCode" />
    <!-- 分享内容文件树弹窗：与自动转存「查看」同款（fetcher 按搜索结果的链接直取） -->
    <ShareFilesModal
      v-model:open="sfOpen"
      :task-id="null"
      :task-name="sfRec?.n || ''"
      :fetcher="(refresh: boolean) => getSearchShareFiles(sfRec!.t, sfRec!.url!, sfRec!.share_code || '', refresh)"
    />
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

<style scoped>
/* =====================================================================
 * 搜索结果区样式。
 * 模板里引用的 .st-* / .skel / .enter 等 class 此前在 pk.css 中并不存在，
 * 导致「骨架屏看不见、结果行瞬间闪出」，这里按项目视觉令牌补齐。
 * 注：pk.css 只保留跨页公共件；本页私有件按约定放本文件的 scoped 样式。
 * ===================================================================== */

/* 无内边距卡片：表格/卡片列表自己管留白，避免与卡内 padding 叠加错位 */
.st-flush { padding: 0; position: relative; overflow: hidden; }
.st-mb { margin-bottom: 16px; }
.st-flex1 { flex: 1; }
.st-res { overflow: hidden; }

/* ===== 加载骨架：横向扫光（与 ShareFilesModal 的 sfm-skel 同一套观感） ===== */
.skel {
  display: inline-block;
  height: 14px;
  border-radius: 6px;
  background: linear-gradient(90deg, var(--surface-2) 20%, var(--split) 45%, var(--surface-2) 70%);
  background-size: 220% 100%;
  animation: pkSkelSweep 1.35s cubic-bezier(0.4, 0, 0.6, 1) infinite;
}
@keyframes pkSkelSweep {
  0% { background-position: 120% 0; }
  100% { background-position: -80% 0; }
}
.skel-tr td { padding-top: 15px; padding-bottom: 15px; }

/* ===== 结果行：来源色条 + 名称省略 ===== */
.st-namecell { display: flex; align-items: center; gap: 8px; min-width: 0; }
.srcbar { flex: none; width: 3px; height: 15px; border-radius: 3px; }
.resname {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
}
.st-hot { flex: none; margin-right: 0; }

/* 来源格：网盘 tag + 「查看文件/复制链接」图标钮（同自动转存 .pa-ico 五色钮语义：
   查看=蓝 / 复制=紫；样式就近自带一份——scoped 不跨页） */
.st-srccell { display: inline-flex; align-items: center; gap: 2px; min-width: 0; }
.pa-ico {
  width: 26px;
  height: 26px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  line-height: 1;
  border: none;
  border-radius: 7px;
  background: transparent;
  cursor: pointer;
  color: var(--text2);
  transition: background 0.15s, color 0.15s;
  font-family: inherit;
}
.pa-ico:hover { background: var(--surface-3); }
.pa-ico.pa-ico-view { color: #1677ff; }
.pa-ico.pa-ico-view:hover { background: #f0f8ff; }
.pa-ico.pa-ico-copy { color: #722ed1; }
.pa-ico.pa-ico-copy:hover { background: #f9f0ff; }
html[data-theme='dark'] .pa-ico.pa-ico-view { color: #69b1ff; }
html[data-theme='dark'] .pa-ico.pa-ico-view:hover { background: #111a2c; }
html[data-theme='dark'] .pa-ico.pa-ico-copy { color: #b37feb; }
html[data-theme='dark'] .pa-ico.pa-ico-copy:hover { background: #1a1425; }

/* fixed 布局让「资源名称 50%」真正生效：超长名称在格子内省略（hover 有 title 全文），按钮列不再被挤没 */
table.st-table { table-layout: fixed; }

/* ===== 检索中动画：能量环 + 卫星点 + 核心放大镜（纯 CSS/SVG） ===== */
.st-searching {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 360px;
  padding: 36px 0;
}
.ss-orbit {
  position: relative;
  width: 180px;
  height: 180px;
}
/* 背景辉光斑：给整个环打氛围光 */
.ss-glow {
  position: absolute;
  width: 88px;
  height: 88px;
  border-radius: 50%;
  filter: blur(34px);
  opacity: 0.32;
}
.ss-glow.g1 {
  top: -20px;
  left: -10px;
  background: var(--primary);
  animation: ssDrift 5s ease-in-out infinite alternate;
}
.ss-glow.g2 {
  bottom: -24px;
  right: -12px;
  background: #7c5cfc;
  animation: ssDrift 5s ease-in-out infinite alternate-reverse;
}
@keyframes ssDrift {
  from { transform: translate(0, 0) scale(1); }
  to { transform: translate(18px, 12px) scale(1.25); }
}
/* 三层环：虚线慢转 / 实线反向带卫星 / 弧线快转 */
.ss-ring {
  position: absolute;
  border-radius: 50%;
}
.ss-ring.r-dash {
  inset: 0;
  border: 1.5px dashed color-mix(in srgb, var(--primary) 45%, transparent);
  animation: ssSpin 9s linear infinite;
}
.ss-ring.r-solid {
  inset: 26px;
  border: 1.5px solid color-mix(in srgb, #7c5cfc 45%, transparent);
  animation: ssSpin 5s linear infinite reverse;
}
.ss-dot {
  position: absolute;
  top: -5px;
  left: 50%;
  width: 9px;
  height: 9px;
  margin-left: -4.5px;
  border-radius: 50%;
  background: #7c5cfc;
  box-shadow: 0 0 10px 2px color-mix(in srgb, #7c5cfc 70%, transparent);
}
.ss-ring.r-arc {
  inset: 52px;
  border: 3.5px solid transparent;
  border-top-color: var(--primary);
  border-right-color: var(--primary);
  filter: drop-shadow(0 0 6px color-mix(in srgb, var(--primary) 60%, transparent));
  animation: ssSpin 1.1s linear infinite;
}
@keyframes ssSpin {
  to { transform: rotate(360deg); }
}
/* 核心：渐变圆 + 放大镜 + 呼吸光晕 */
.ss-core {
  position: absolute;
  inset: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--primary), #7c5cfc);
  animation: ssPulse 1.8s ease-out infinite;
}
.ss-core svg {
  width: 30px;
  height: 30px;
}
@keyframes ssPulse {
  0% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--primary) 45%, transparent); }
  70% { box-shadow: 0 0 0 22px transparent; }
  100% { box-shadow: 0 0 0 0 transparent; }
}
/* 文案：渐变流光 */
.ss-text {
  margin-top: 26px;
  font-size: 15px;
  font-weight: 600;
  display: flex;
  align-items: center;
  background: linear-gradient(90deg, var(--text) 30%, var(--primary), #7c5cfc, var(--text) 70%);
  background-size: 220% 100%;
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  animation: ssShine 2.6s linear infinite;
}
@keyframes ssShine {
  from { background-position: 120% 0; }
  to { background-position: -120% 0; }
}
.ss-text i {
  width: 4px;
  height: 4px;
  margin-left: 5px;
  border-radius: 50%;
  background: var(--text3);
  animation: ssBounce 1.2s ease-in-out infinite;
}
.ss-text i:nth-child(2) { animation-delay: 0.15s; }
.ss-text i:nth-child(3) { animation-delay: 0.3s; }
@keyframes ssBounce {
  0%, 100% { transform: translateY(0); opacity: 0.4; }
  50% { transform: translateY(-4px); opacity: 1; }
}
.ss-sub {
  margin-top: 8px;
  font-size: 12.5px;
  color: var(--text3);
}

/* ===== 入场过渡：卡片整体上浮淡入 + 结果行逐行错峰（配合模板的 animation-delay） ===== */
.enter { animation: pkEnter 0.3s cubic-bezier(0.22, 0.61, 0.36, 1) both; }
@keyframes pkEnter {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: none; }
}
.st-res tbody tr { animation: pkRowIn 0.36s cubic-bezier(0.22, 0.61, 0.36, 1) both; }
@keyframes pkRowIn {
  from { opacity: 0; transform: translateY(7px); }
  to { opacity: 1; transform: none; }
}
/* 骨架行不参与入场动画，否则会和扫光叠在一起显脏 */
.st-res .skel-tr { animation: none; }

/* ===== 手机端卡片列表（<768px 用它替代表格） ===== */
.st-cards { padding: 10px 12px; }
.st-card-item {
  padding: 12px 14px;
  border: 1px solid var(--split);
  border-radius: 10px;
  animation: pkRowIn 0.36s cubic-bezier(0.22, 0.61, 0.36, 1) both;
  transition: background 0.15s;
}
.st-card-item + .st-card-item { margin-top: 10px; }
.st-card-item:hover { background: var(--surface-3); }
.st-card-name { display: flex; align-items: flex-start; gap: 8px; margin-bottom: 8px; }
/* 文件名放开换行看全（用户要求：截断了根本不知道文件是啥）；色条随行首对齐 */
.st-card-title {
  min-width: 0;
  font-weight: 500;
  line-height: 1.5;
  word-break: break-all;
}
.st-card-meta { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.st-card-date { margin-left: auto; }
.st-card-ops { justify-content: flex-end; }

/* ===== 未配置 PanSou 时的空态引导：图标转警示色 + 去配置按钮 ===== */
.pk-empty-state .pk-es-ico.is-warn {
  color: var(--warning);
  background: linear-gradient(135deg, rgba(250, 173, 20, 0.14), rgba(250, 173, 20, 0.06));
  box-shadow: inset 0 0 0 1px rgba(250, 173, 20, 0.28);
}
.pk-es-btn {
  margin-top: 16px;
}
/* 空态副文案限宽，避免长句在宽屏下拉成一条 */
.pk-empty-state .pk-es-sub {
  max-width: 420px;
  text-align: center;
  line-height: 1.7;
}

/* ===== 统计卡（搜索页侧的补充态；.stat 基础样式在 pk.css） ===== */
/* 手机（<768px）：网盘 tab 那排隐藏——筛选条只留频道管理 + 引擎状态；
   切网盘走下面的统计卡（可点，当前 tab 有描边高亮），别在 393px 宽里塞 8 个 tab */
@media (max-width: 767px) {
  .pktabs {
    display: none;
  }
  /* 频道行两行式：上行「搜索源频道 +N 管理 … 耗时」，下行引擎状态胶囊独占一行 */
  .st-flex1 {
    display: none;
  }
  /* 耗时贴频道行右缘：频道摘要条不再 flex:1 撑满整行 */
  .ch-strip {
    flex: 0 1 auto;
  }
  .ch-elapsed {
    margin-left: auto;
    font-size: 12.5px;
    color: var(--text3);
    white-space: nowrap;
  }
  .engine-pill {
    flex-basis: 100%;
    margin-left: 10px; /* 与 ch-strip 内文字左缘对齐 */
    justify-self: start;
  }
}

/* 系统开启「减弱动态效果」时全部关闭 */
@media (prefers-reduced-motion: reduce) {
  .skel, .enter, .st-res tbody tr, .st-card-item { animation: none; }
}
</style>
