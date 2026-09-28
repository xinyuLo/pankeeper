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
import {
  getInitialResults,
  getPanSouAddr,
  getSearchChannels,
  getSearchResults,
  type SearchChannel,
} from '@/api/modules/search'
import { listDdItems } from '@/api/modules/dd'
import { DRIVE_META } from '@/api/mock/meta'
import type { DriveType, SearchResultItem } from '@/types/model'

/** tab 固定顺序 = DRIVE_META 的键序（全部/百度/夸克/115/123/阿里/迅雷/UC，与原型一致） */
const DRIVE_ORDER = Object.keys(DRIVE_META) as DriveType[]

/* ===== 静态文案 ===== */
const T_NO_CRED = '请先到「网盘连接」页配置该网盘凭据'
const T_NO_DD = '请先到「转存配置」页给该网盘添加一个路径'

/* ===== 基础数据 ===== */
const kw = ref('庆余年 第二季')
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
const tmOpen = ref(false)
const tmTarget = ref<TransferTarget | null>(null)

function openQuick(r: SearchResultItem) {
  if (!r.ok || !hasDD(r.t)) return
  tmOpen.value = false
  qsType.value = r.t
  qsName.value = r.n
  qsOpen.value = true
}
function openTransfer(r: SearchResultItem) {
  if (!r.ok) return
  qsOpen.value = false
  tmTarget.value = { type: r.t, name: r.n, size: r.s }
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
        <span class="muted">搜索源频道</span>
        <!-- 频道勾选记录检索范围（mock 不改变结果集，与原型一致） -->
        <a-checkbox v-for="c in channels" :key="c.name" v-model:checked="c.on">{{ c.name }}</a-checkbox>
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

    <!-- 结果表 + 分页同一张白卡（padding:0 的卡里表尾不夹灰缝） -->
    <div class="card st-flush st-res" :class="{ enter: rowEpoch > 0 }">
      <table>
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
      <PkPager v-model:current="page" v-model:pageSize="size" :total="filtered.length" />
    </div>

    <div class="note-box">
      <b>设计说明</b>
      <ul>
        <li>点「转存」时目录树会按这条结果的来源<b>自动加载对应网盘的目录</b>——百度资源出百度树，不需要手动选网盘。</li>
        <li>若该网盘尚未配置凭据，「转存」按钮置灰并引导去「网盘连接」；「快速转存」还要该网盘配过转存目录。</li>
        <li>搜索请求由后端代理转发 PanSou，前端不直连，避免 TG 频道配置与地址暴露在浏览器。</li>
      </ul>
    </div>

    <!-- 快速转存弹窗（qsMask）：凭转存配置直入队列 -->
    <QuickTransferModal v-model:open="qsOpen" :type="qsType" :share-name="qsName" />
    <!-- 转存弹窗（transferMask）：分享树勾选 + 目标目录树 -->
    <TransferModal v-model:open="tmOpen" :target="tmTarget" />
  </div>
</template>

<style scoped>
/* 卡片去内边距：筛选条接 tab、表格接分页，连成完整卡（原型 padding:0;overflow:hidden） */
.st-flush {
  padding: 0;
  overflow: hidden;
}
/* 只有筛选卡需要下间距（.view 是 flex 容器，子项 margin 不折叠，结果卡不要再加） */
.st-mb {
  margin-bottom: 16px;
}
.st-flex1 {
  flex: 1;
}

/* ---- 搜索框：比常规输入高一档，是全页最显眼的操作 ---- */
.searchwrap {
  display: flex;
  gap: 12px;
  margin-bottom: 18px;
}
.kw-input {
  height: 44px;
  font-size: 15px;
  border-radius: 10px;
}
.btn-search {
  width: 104px;
  height: 44px;
  font-size: 15px;
  border-radius: 10px;
}

/* ---- 顶部不确定进度条：搜索时出现，完成拉满再淡出 ---- */
.pk-topbar {
  position: fixed;
  left: 212px; /* 侧栏宽，进度条从内容区起画 */
  right: 0;
  top: 0;
  height: 2px;
  z-index: 60;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.25s;
}
.pk-topbar.on {
  opacity: 1;
}
.pk-topbar i {
  display: block;
  height: 100%;
  width: 40%;
  border-radius: 2px;
  background: linear-gradient(90deg, rgba(22, 119, 255, 0), #1677ff 45%, #69c0ff 60%, rgba(22, 119, 255, 0));
  animation: pkSlide 1.15s cubic-bezier(0.45, 0.05, 0.55, 0.95) infinite;
}
@keyframes pkSlide {
  0% { transform: translateX(-100%); }
  100% { transform: translateX(320%); }
}
/* 收束：数据到位后拉满再淡出 */
.pk-topbar.done i {
  width: 100%;
  transform: none;
  animation: none;
  background: #1677ff;
  transition: width 0.2s;
}

/* ---- 横向网盘 tab（胶囊分段，包在筛选卡里所以不再有白底/阴影） ---- */
.pktabs {
  display: flex;
  gap: 6px;
  align-items: center;
  flex-wrap: wrap;
  background: transparent;
  padding: 12px 14px 13px;
}
.pktab {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 13px;
  cursor: pointer;
  user-select: none;
  border-radius: 8px;
  font-size: 13.5px;
  color: var(--text2);
  background: transparent;
  transition: background 0.16s, color 0.16s, box-shadow 0.16s;
  white-space: nowrap;
}
.pktab:hover {
  background: var(--split);
  color: var(--text);
}
.pktab.on {
  color: #fff;
  font-weight: 500;
  background: var(--primary);
  box-shadow: 0 2px 8px rgba(22, 119, 255, 0.28);
}
.pktab.on:hover {
  background: var(--primary-h);
  color: #fff;
}
/* 小圆点：有结果的网盘用网盘色标识 */
.pktab .pkdot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
  opacity: 0.85;
}
.pktab.on .pkdot {
  background: #fff !important;
  opacity: 0.9;
}
.pktab .pknum {
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  line-height: 1;
  padding: 2px 7px;
  border-radius: 999px;
  background: var(--split);
  color: var(--text2);
  transition: background 0.16s, color 0.16s;
}
.pktab.on .pknum {
  background: rgba(255, 255, 255, 0.24);
  color: #fff;
}
.pktab.dim {
  opacity: 0.4;
}
.pktab.dim .pknum {
  background: transparent;
}
/* tab 行右侧的扫描状态 */
.pktabs .pk-tab-scan {
  margin-left: auto;
  font-size: 12.5px;
  color: var(--text3);
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.pkdot-live {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #52c41a;
  flex-shrink: 0;
  box-shadow: 0 0 0 0 rgba(82, 196, 26, 0.5);
  animation: pkPulse 1.8s ease-out infinite;
}
@keyframes pkPulse {
  0% { box-shadow: 0 0 0 0 rgba(82, 196, 26, 0.45); }
  70% { box-shadow: 0 0 0 6px rgba(82, 196, 26, 0); }
  100% { box-shadow: 0 0 0 0 rgba(82, 196, 26, 0); }
}
.pkspin {
  width: 12px;
  height: 12px;
  border: 2px solid rgba(22, 119, 255, 0.25);
  border-top-color: #1677ff;
  border-radius: 50%;
  display: inline-block;
  animation: pkSpin 0.7s linear infinite;
}
@keyframes pkSpin {
  to { transform: rotate(360deg); }
}

/* ---- 统计卡：出场动画 + 单网盘态 ---- */
.statline.enter .stat {
  animation: pkStatIn 0.34s cubic-bezier(0.22, 1, 0.36, 1) both;
}
.statline.enter .stat:nth-child(1) { animation-delay: 0s; }
.statline.enter .stat:nth-child(2) { animation-delay: 0.04s; }
.statline.enter .stat:nth-child(3) { animation-delay: 0.08s; }
.statline.enter .stat:nth-child(4) { animation-delay: 0.12s; }
.statline.enter .stat:nth-child(5) { animation-delay: 0.16s; }
.statline.enter .stat:nth-child(6) { animation-delay: 0.2s; }
.statline.enter .stat:nth-child(7) { animation-delay: 0.24s; }
.statline.enter .stat:nth-child(8) { animation-delay: 0.28s; }
@keyframes pkStatIn {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: none; }
}
.stat.wide {
  flex: 0 0 auto;
  min-width: 210px;
}
.statline.single {
  justify-content: flex-start;
}

/* ---- 结果表逐行入场 ---- */
.st-res.enter tbody tr {
  animation: pkRowIn 0.3s cubic-bezier(0.22, 1, 0.36, 1) both;
}
@keyframes pkRowIn {
  from { opacity: 0; transform: translateY(5px); }
  to { opacity: 1; transform: none; }
}

/* 资源名称格：品牌色条 + 名称 + 「极速」标 */
.st-namecell {
  display: flex;
  align-items: center;
  min-width: 0;
}
.srcbar {
  width: 3px;
  height: 30px;
  border-radius: 2px;
  flex: none;
  margin-right: 10px;
}
.resname {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.st-hot {
  margin-left: 8px;
  margin-right: 0;
  flex: none;
}

/* ---- 骨架屏 ---- */
.skel {
  height: 13px;
  border-radius: 4px;
  display: inline-block;
  vertical-align: middle;
  background: linear-gradient(90deg, #eef1f5 25%, #e2e7ee 37%, #eef1f5 63%);
  background-size: 400% 100%;
  animation: pkShim 1.25s ease-in-out infinite;
}
@keyframes pkShim {
  0% { background-position: 100% 50%; }
  100% { background-position: 0 50%; }
}
.skel-tr td {
  padding: 14px 16px;
}

/* 结果为空 */
.pk-empty {
  padding: 52px 20px;
  text-align: center;
  color: var(--text3);
}
.pk-empty b {
  display: block;
  font-size: 15px;
  color: var(--text2);
  margin-bottom: 6px;
  font-weight: 500;
}

/* ---- 暗色适配：硬编码浅色在这里翻面 ---- */
html[data-theme='dark'] .pktab:hover {
  background: rgba(255, 255, 255, 0.07);
}
html[data-theme='dark'] .pktab .pknum {
  background: rgba(255, 255, 255, 0.09);
}
html[data-theme='dark'] .skel {
  background: linear-gradient(90deg, #20242c 25%, #2a3038 37%, #20242c 63%);
  background-size: 400% 100%;
}
html[data-theme='dark'] .pk-topbar i {
  background: linear-gradient(90deg, rgba(64, 150, 255, 0), #4096ff 45%, #91caff 60%, rgba(64, 150, 255, 0));
}
html[data-theme='dark'] .pk-topbar.done i {
  background: #4096ff;
}
</style>
