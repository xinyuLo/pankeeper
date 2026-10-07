<script setup lang="ts">
/* =====================================================================
 * 缓存配置页 —— 原型 parts/page-cache.html（cc- 前缀）的 Vue 移植。
 * 三段：缓存策略（开关/失效时间/自动刷新）→ 内存保护（水位条/阈值/降级/条目上限）
 *       → 已缓存目录表（fresh 绿 / stale 红，行内刷新/清除）。
 * 配置改完即静默写回 mock store（原型如此）；刷新=重置过期时间，清除=删行。
 * 水位条颜色随实际占用变化：<75 绿 / 75-85 黄 / >85 红。
 * ===================================================================== */
import { computed, createVNode, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { ExclamationCircleOutlined, LoadingOutlined } from '@ant-design/icons-vue'
import { DRIVE_META } from '@/api/mock/meta'
import { getRootDirs } from '@/api/modules/accounts'
import {
  clearAllCacheTrees,
  clearCacheTree,
  getCacheConfig,
  listCacheTrees,
  refreshCacheTree,
  saveCacheConfig,
  warmAllAccounts,
  warmAllStatus,
  type WarmAllStatus,
} from '@/api/modules/cache'
import type { CacheCfg, CacheTree, MemUsage } from '@/api/mock/cache'

/* ===== 页面状态 ===== */
const cfg = reactive<CacheCfg>({
  master: true,
  persist: true,
  auto: true,
  ttl: 30,
  ttlUnit: '小时',
  memHigh: 85,
  act: 'ladder',
  maxSizeMb: 200,
})
/* 后端没响应时保持 0 占用，别拿 mock 假值糊弄人 */
const mem = ref<MemUsage>({ pct: 0, usedMb: 0, totalMb: 200 })
const trees = ref<CacheTree[]>([])
/** 各网盘默认根目录（行内刷新只刷这一层；空 = 刷真根） */
const rootDirs = ref<Record<string, string>>({})

let loaded = false // 首次装载期间不回写（否则 onMounted 的赋值会触发一轮保存）
onMounted(async () => {
  const r = await getCacheConfig()
  Object.assign(cfg, r.cfg)
  mem.value = r.mem
  trees.value = await listCacheTrees()
  rootDirs.value = await getRootDirs().catch(() => ({}))
  // watch 回调不是同步跑的（flush:'pre' 排队），等这一拍过去再放行
  await nextTick()
  loaded = true
})

/* 配置任意一项变化 → 防抖 600ms 后持久化（数字框连点/下拉连切只发一次） */
let cfgSaveTimer: number | undefined
watch(cfg, (v) => {
  if (!loaded) return
  window.clearTimeout(cfgSaveTimer)
  cfgSaveTimer = window.setTimeout(() => {
    void saveCacheConfig({ ...v })
      .then(() => message.success('已自动保存'))
      .catch(() => message.error('自动保存失败，请重试'))
  }, 600)
})

/* 下拉/数字项变更的提示做防抖：数字框连续点按不刷屏（原型逐次 toast） */
let cfgToastTimer: number | undefined
function toastCfg(msg = '缓存策略已更新') {
  window.clearTimeout(cfgToastTimer)
  cfgToastTimer = window.setTimeout(() => message.success(msg), 400)
}
function onMasterSw(v: boolean | string | number) {
  message.success(v ? '目录树缓存已开启' : '目录树缓存已关闭，转存弹窗将实时拉取')
}
function onPersistSw(v: boolean | string | number) {
  message.success(v ? '缓存持久化已开启：重启不再丢缓存' : '缓存持久化已关闭：重启后缓存清空')
}
function onAutoSw(v: boolean | string | number) {
  message.success(v ? '自动刷新已开启' : '自动刷新已关闭，过期后打开弹窗会稍等一次')
}

/* ===== 内存水位：颜色随实际占用变化（<75 绿 / 75-85 黄 / >85 红） ===== */
const memCls = computed(() => (mem.value.pct > 85 ? 'hot' : mem.value.pct >= 75 ? 'warm' : 'ok'))

/* ===== 下拉选项 ===== */
const TTLUNIT_OPTS = [
  { value: '分钟', label: '分钟' },
  { value: '小时', label: '小时' },
]
const MEMHIGH_OPTS = [
  { value: 75, label: '75%' },
  { value: 85, label: '85%' },
  { value: 95, label: '95%' },
]
const ACT_OPTS = [
  { value: 'ladder', label: '逐级降级' },
  { value: 'off', label: '直接关闭缓存功能' },
  { value: 'compress', label: '只压缩条目，不关闭' },
]

/* ===== 已缓存目录表：一个账号一行（条目/大小累加） ===== */
interface AccRow {
  key: string
  type: CacheTree['type']
  accName: string
  entries: number
  sizeKb: number
  stale: number
  /** 最新一次缓存条目的剩余分钟数（下次刷新时间 = now + nextMin） */
  nextMin: number
  /** 本组条目里最新一次缓存写入时间（秒级时间戳，0=无记录） */
  lastCachedAt: number
  ids: (number | string)[]
}
const accRows = computed<AccRow[]>(() => {
  const map = new Map<string, AccRow>()
  for (const t of trees.value) {
    // 同一账号在缓存键里可能是 "main"（老默认账号写法）或账号 id，两种 acc 指向同一个账号；
    // acc_name 是后端按账号表解析出的统一展示名 —— 按它归并，一个账号只出一行。
    const key = `${t.type}/${t.acc_name || t.acc}`
    let row = map.get(key)
    if (!row) {
      row = {
        key,
        type: t.type,
        accName: t.acc_name || t.acc,
        entries: 0,
        sizeKb: 0,
        stale: 0,
        nextMin: -Infinity,
        lastCachedAt: 0,
        ids: [],
      }
      map.set(key, row)
    }
    row.entries += t.entries || 0
    row.sizeKb += Math.max(1, (t.entries || 0) * 2) // 与后端同口径：每条 ~2KB
    if (t.ttlMin < 0) row.stale += 1
    // 「下次刷新」取最新缓存那条：同账号各条目 TTL 相同，cachedAt 最大即到期最晚
    row.nextMin = Math.max(row.nextMin, t.ttlMin)
    row.lastCachedAt = Math.max(row.lastCachedAt, t.cachedAt || 0)
    row.ids.push(t.id)
  }
  return [...map.values()]
})
const staleCount = computed(() => trees.value.filter((t) => t.ttlMin < 0).length)

function metaName(type: CacheTree['type']): string {
  return DRIVE_META[type]?.name ?? type
}
function metaColor(type: CacheTree['type']): string {
  return DRIVE_META[type]?.color ?? '#1677ff'
}
/** 下次刷新时间：本组最新缓存那条的到期时间 = now + max(ttlMin)，统一 yyyy-MM-dd HH:mm */
function nextRefreshText(row: AccRow): string {
  if (!Number.isFinite(row.nextMin)) return '—'
  if (row.stale) return '已过期 · 打开即刷新'
  const d = new Date(Date.now() + row.nextMin * 60000)
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  const ymd = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  return `${ymd} ${hh}:${mm}`
}

/** 缓存时间：本组最新一次缓存写入，yyyy-MM-dd HH:mm */
function cachedAtText(row: AccRow): string {
  if (!row.lastCachedAt) return '—'
  const d = new Date(row.lastCachedAt * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

/** 距下次刷新倒计时：紧凑格式（1天5小时 / 5小时59分 / 42分钟） */
function countdownText(row: AccRow): { text: string; warn: boolean } | null {
  if (!Number.isFinite(row.nextMin)) return null
  if (row.stale) return null
  const d = Math.floor(row.nextMin / 1440)
  const h = Math.floor((row.nextMin % 1440) / 60)
  const m = row.nextMin % 60
  const text = d > 0 ? `${d}天${h}小时` : h > 0 ? `${h}小时${m}分` : `${m}分钟`
  // 剩余不足 1 小时给警示色，提醒快到期了
  return { text, warn: row.nextMin < 60 }
}


async function reload() {
  trees.value = await listCacheTrees()
}

/* 刷新一个账号 = 该账号全部缓存键失效，下次浏览直连重拉。
 * 状态 tag 给三态反馈：刷新中（转圈）→ 刚刷完带条数闪 4 秒 → 回常规状态。 */
const refreshing = reactive<Record<string, boolean>>({})
const flash = reactive<Record<string, string>>({})
const flashTimers: Record<string, number> = {}
async function onRefreshRow(row: AccRow) {
  if (refreshing[row.key]) return
  refreshing[row.key] = true
  window.clearTimeout(flashTimers[row.key])
  delete flash[row.key]
  try {
    // 只刷**默认根层**（2026-10-08 用户定稿）：深层不跟着实拉——几百层逐层打网盘又慢又
    // 容易撞限速，深层的更新交给 TTL 过期或下次浏览时的懒加载
    const rootPath = rootDirs.value[row.type] || ''
    const acc = String(row.ids[0] ?? '').split('/')[1] || 'main'
    const rootKey = `${row.type}/${acc}/${rootPath || (row.type === 'baidu' ? '/' : '0')}`
    await refreshCacheTree(rootKey)
    await reload()
    const done = accRows.value.find((r) => r.key === row.key)
    flash[row.key] = `已刷新 · ${done?.entries ?? 0} 条`
    window.clearTimeout(flashTimers[row.key])
    flashTimers[row.key] = window.setTimeout(() => delete flash[row.key], 4000)
    message.success(`已刷新「${row.accName}」的默认根层${rootPath ? `（${rootPath}）` : ''}`)
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(`刷新失败：${detail || '网盘拉取失败'}`, 5)
  } finally {
    refreshing[row.key] = false
  }
}
async function onClearRow(row: AccRow) {
  for (const id of row.ids) await clearCacheTree(id)
  message.success(`已清除「${row.accName}」的缓存`)
  await reload()
}
async function onClearAll() {
  await clearAllCacheTrees()
  message.success('缓存已清空，下次打开转存弹窗会重新拉取')
  await reload()
}

/* ===== 缓存预热：全部已连接账号入队，后端单工队列串行跑（同网盘多账号绝不并行——防风控） ===== */
const warmRunning = ref(false)
const warmInfo = ref('') // 按钮旁的实时进度行
let warmPollTimer: number | undefined

function warmLine(st: WarmAllStatus): string {
  const cur = st.current
  const curTxt = cur
    ? `当前：${DRIVE_META[cur.type as 'baidu']?.name || cur.type}${cur.acc_name ? ' · ' + cur.acc_name : ''}（${cur.done} 个文件夹）`
    : ''
  const base = `预热中 ${st.done_jobs}/${st.total_jobs} 个账号`
  return curTxt ? `${base} · ${curTxt}` : `${base}（排队 ${st.queued}）`
}

function onWarmAll() {
  Modal.confirm({
    title: '重新缓存全部网盘？',
    content: '将把所有已连接账号的目录树逐个排队预热（串行跑，同网盘多账号也不会并行，避免触发风控）。大网盘可能需要几分钟到十几分钟。',
    okText: '开始预热',
    cancelText: '取消',
    onOk: () => startWarmAll(),
  })
}

async function startWarmAll() {
  try {
    const { count } = await warmAllAccounts()
    if (!count) {
      message.info('没有已连接的账号，无从预热')
      return
    }
    warmRunning.value = true
    warmInfo.value = `已入队 ${count} 个账号，等待预热…`
    pollWarm()
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(`预热入队失败：${detail || '请求失败'}`, 5)
  }
}

/** 轮询队列总览：全部落定（无排队无在跑）→ 汇报结果并停表 */
function pollWarm() {
  window.clearTimeout(warmPollTimer)
  warmPollTimer = window.setTimeout(async () => {
    try {
      const st = await warmAllStatus()
      const active = st.queued > 0 || !!st.current
      if (!active) {
        warmRunning.value = false
        warmInfo.value = ''
        if (st.total_jobs === 0) return // 队列空转一拍（后端重启过）：静默收场
        if (st.failed_jobs > 0) message.warning(`预热完成：${st.done_jobs - st.failed_jobs} 成功 / ${st.failed_jobs} 失败（失败账号可稍后重试）`, 6)
        else message.success(`预热完成 · 共 ${st.done_jobs} 个账号`, 4)
        await reload()
        return
      }
      warmInfo.value = warmLine(st)
      pollWarm()
    } catch {
      warmRunning.value = false
      warmInfo.value = ''
      message.error('预热进度查询失败，已停止跟踪（后台仍在跑）', 5)
    }
  }, 3000)
}

onUnmounted(() => window.clearTimeout(warmPollTimer))
</script>

<template>
  <div>
    <div class="card cc-card">
      <!-- 卡头：标题 + 设计定位说明 + 全局操作 -->
      <div class="cc-cardhd">
        <div class="cc-headtt">
          <h2>缓存配置</h2>
          <div class="cc-headdesc">
            缓存的是<b>网盘的文件夹（目录树）</b>：转存按钮弹出的目录树、快速转存的路径建议都直接读这份缓存，
            不实时打网盘接口——所以它不总在开目录选择页，打开就是秒出。
          </div>
        </div>
        <div class="cc-headact">
          <a-button type="primary" @click="onWarmAll">缓存预热</a-button>
          <a-popconfirm title="清空全部目录树缓存？" ok-text="清空" cancel-text="取消" @confirm="onClearAll">
            <a-button>清空缓存</a-button>
          </a-popconfirm>
        </div>
      </div>
      <!-- 预热进行中：按钮下方一条实时进度（当前跑哪个账号、已缓存多少文件夹） -->
      <div v-if="warmRunning && warmInfo" class="cc-warmline">
        <LoadingOutlined spin />
        <span>{{ warmInfo }}</span>
      </div>

      <!-- ===== 段一：缓存策略 ===== -->
      <div class="cc-sect">缓存策略</div>
      <div class="formrow">
        <label>目录树缓存</label>
        <div class="ctl cc-mastrow">
          <a-switch v-model:checked="cfg.master" @change="onMasterSw" />
          <!-- 持久化（写穿 SQLite，重启不丢）；默认开 -->
          <span class="cc-persist">
            <a-tooltip title="开启后缓存写入数据库：重启/重装不丢，恢复零网盘请求">
              <span class="cc-persist-label">持久化</span>
            </a-tooltip>
            <a-switch v-model:checked="cfg.persist" size="small" @change="onPersistSw" />
          </span>
        </div>
      </div>
      <div class="formrow">
        <label>缓存失效时间</label>
        <div class="ctl">
          <a-input-number v-model:value="cfg.ttl" :min="1" style="width: 110px" @change="toastCfg()" />
          <a-select v-model:value="cfg.ttlUnit" :options="TTLUNIT_OPTS" style="width: 110px" @change="toastCfg()" />
        </div>
      </div>
      <div class="formrow">
        <label>自动刷新</label>
        <div class="ctl">
          <a-switch v-model:checked="cfg.auto" @change="onAutoSw" />
        </div>
      </div>

      <!-- ===== 段二：内存保护 ===== -->
      <div class="cc-sect">内存保护</div>
      <!-- 水位条钉死 40% 卡宽：整行都是灰条太空 -->
      <div class="cc-memrow">
        <div class="progress cc-membar" :class="memCls">
          <i :style="{ width: mem.pct + '%' }"></i>
        </div>
        <span class="cc-memtext" :class="{ hot: mem.pct > 85 }">
          缓存占用 <b>{{ mem.pct }}%</b> · {{ mem.usedMb }} MB / {{ mem.totalMb }} MB
        </span>
      </div>
      <div class="formrow">
        <label>内存阈值</label>
        <div class="ctl">
          <a-select v-model:value="cfg.memHigh" :options="MEMHIGH_OPTS" style="width: 180px" @change="toastCfg()" />
        </div>
      </div>
      <div class="formrow">
        <label>降级动作</label>
        <div class="ctl">
          <a-select v-model:value="cfg.act" :options="ACT_OPTS" style="width: 180px" @change="toastCfg('降级策略已更新')" />
        </div>
      </div>
      <div class="formrow">
        <label>缓存大小上限（MB）</label>
        <div class="ctl">
          <a-input-number v-model:value="cfg.maxSizeMb" :min="50" style="width: 130px" @change="toastCfg()" />
        </div>
      </div>

      <!-- ===== 段三：已缓存目录 ===== -->
      <div class="cc-sect">已缓存目录</div>
      <div class="cc-tablewrap pk-hscroll">
        <table class="cc-table">
          <thead>
            <tr>
              <th class="cc-th" style="width: 110px">网盘</th>
              <th class="cc-th">账号</th>
              <th class="cc-th" style="width: 150px">缓存时间</th>
              <th class="cc-th" style="width: 150px">下次刷新</th>
              <th class="cc-th" style="width: 130px">距下次缓存</th>
              <th class="cc-th" style="width: 100px">缓存条数</th>
              <th class="cc-th" style="width: 90px">大小</th>
              <th class="cc-th" style="width: 140px">状态</th>
              <th class="cc-th cc-th-ops" style="width: 130px">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in accRows" :key="row.key" class="cc-row">
              <td class="cc-td">
                <span class="cc-pantag">
                  <i class="cc-pandot" :style="{ background: metaColor(row.type) }"></i>{{ metaName(row.type) }}
                </span>
              </td>
              <td class="cc-td">{{ row.accName }}</td>
              <td class="cc-td">{{ cachedAtText(row) }}</td>
              <td class="cc-td">{{ nextRefreshText(row) }}</td>
              <td class="cc-td">
                <span v-if="countdownText(row)" class="cc-count" :class="{ warn: countdownText(row)!.warn }">
                  {{ countdownText(row)!.text }}
                </span>
                <span v-else class="cc-count expired">已过期</span>
              </td>
              <td class="cc-td cc-num">{{ row.entries }}</td>
              <td class="cc-td cc-num">{{ row.sizeKb }} KB</td>
              <td class="cc-td">
                <span v-if="refreshing[row.key]" class="tag t-off cc-status"><LoadingOutlined spin class="cc-spin" /> 刷新中…</span>
                <span v-else-if="flash[row.key]" class="tag t-ok cc-status">{{ flash[row.key] }}</span>
                <span v-else class="tag cc-status" :class="row.stale ? 't-warn' : 't-ok'">
                  {{ row.stale ? `${row.stale} 条已过期` : '全部有效' }}
                </span>
              </td>
              <td class="cc-td">
                <span class="cc-ops">
                  <button class="cc-op" @click="onRefreshRow(row)">刷新</button>
                  <button class="cc-op cc-op-del" @click="onClearRow(row)">清除</button>
                </span>
              </td>
            </tr>
            <tr v-if="!accRows.length">
              <td colspan="9" class="cc-empty">缓存是空的 · 打开一次转存弹窗就会把目录树存进来</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="cc-foot">转存弹窗打开时优先读缓存；过期或未命中才实时拉取，拉完立即回填这份表。</div>
    </div>


  </div>
</template>

<style scoped>
/* 卡片去内边距：卡头/分段/表格连成一张完整卡（原型 padding:0; overflow:hidden） */
.cc-card {
  padding: 0;
  overflow: hidden;
}
.cc-cardhd {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  padding: 18px 22px 16px;
  border-bottom: 1px solid var(--split);
}
.cc-headtt h2 {
  font-size: 15.5px;
  font-weight: 600;
  margin: 0 0 5px;
  line-height: 1.35;
}
.cc-headdesc {
  font-size: 12.5px;
  color: var(--text3);
  line-height: 1.7;
  max-width: 640px;
}
.cc-headdesc b {
  color: var(--text2);
  font-weight: 500;
}
.cc-headact {
  flex: 0 0 auto;
  display: flex;
  gap: 10px;
  padding-top: 2px;
}
/* 预热进度行：卡头下方一条安静的实时状态（排队/在跑哪个账号/已缓存文件夹数） */
.cc-warmline {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 10px 22px 0;
  padding: 8px 12px;
  border-radius: 8px;
  background: rgba(22, 119, 255, 0.07);
  color: var(--primary);
  font-size: 12.5px;
}

/* 段标题：主色小竖条 + 提示（原型 cc-sect::before） */
.cc-sect {
  padding: 16px 22px 4px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text2);
  display: flex;
  align-items: center;
  gap: 8px;
}
.cc-sect::before {
  content: '';
  width: 4px;
  height: 14px;
  border-radius: 2px;
  background: var(--primary);
  flex: none;
}
.cc-secttip {
  font-weight: 400;
  font-size: 12px;
  color: var(--text3);
}

/* 内存水位：条钉 40% 宽 + 占用文案；颜色随阈值三段变化 */
.cc-memrow {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 2px 22px 0;
}
.cc-membar {
  flex: 0 0 200px;
  max-width: 200px;
  margin: 0;
}
.cc-membar.ok i {
  background: var(--success);
}
.cc-membar.warm i {
  background: var(--warning);
}
.cc-membar.hot i {
  background: var(--error);
}
.cc-memtext {
  font-size: 12.5px;
  color: var(--text2);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.cc-memtext b {
  color: var(--primary);
}
.cc-memtext.hot b {
  color: var(--error);
}

/* 已缓存目录表（自带列宽/等宽路径/fresh-stale 染色，不用 antd 表格以对齐原型） */
.cc-tablewrap {
  padding: 12px 8px 16px;
}
.cc-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
}
.cc-th {
  text-align: left;
  font-size: 12.5px;
  font-weight: 500;
  color: var(--text3);
  background: var(--surface-2);
  padding: 10px 14px;
  border-bottom: 1px solid var(--split);
  white-space: nowrap;
}
.cc-th-ops {
  text-align: right;
}
.cc-td {
  padding: 12px 14px;
  border-bottom: 1px solid var(--split);
  font-size: 13px;
  color: var(--text);
  vertical-align: middle;
  overflow: hidden;
}
.cc-row:hover {
  background: var(--surface-3);
}
.cc-row:last-child .cc-td {
  border-bottom: none;
}
.cc-pantag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.cc-pandot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex: none;
}
.cc-path {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text2);
}
.cc-num {
  font-variant-numeric: tabular-nums;
}
.cc-ttl {
  font-size: 12px;
}
/* 状态 tag：带框文字（全局 .tag + t-ok/t-warn 色系），转圈图标跟文字对齐 */
.cc-status { display: inline-flex; align-items: center; gap: 5px; }
.cc-spin { font-size: 11px; }
/* 目录树缓存行：主开关 + 持久化小开关并排 */
.cc-mastrow {
  display: flex;
  align-items: center;
  gap: 14px;
}
.cc-persist {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.cc-persist-label {
  font-size: 12.5px;
  color: var(--text3);
  cursor: help;
}
/* 距下次缓存倒计时：低饱和药丸样式，等宽数字；快到期转橙、过期转红 */
.cc-count {
  display: inline-block;
  padding: 2px 10px;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  border-radius: 999px;
  color: var(--text2);
  background: color-mix(in srgb, var(--primary) 7%, var(--card));
  border: 1px solid color-mix(in srgb, var(--primary) 18%, var(--border));
}
.cc-count.warn {
  color: #d46b08;
  background: color-mix(in srgb, #fa8c16 12%, var(--card));
  border-color: color-mix(in srgb, #fa8c16 40%, var(--border));
}
.cc-count.expired {
  color: var(--error);
  background: color-mix(in srgb, var(--error) 8%, var(--card));
  border-color: color-mix(in srgb, var(--error) 30%, var(--border));
}
html[data-theme='dark'] .cc-count.warn {
  color: #ffa940;
}
html[data-theme='dark'] .cc-count.expired {
  color: #ff7875;
}
.cc-ops {
  display: flex;
  gap: 4px;
  justify-content: flex-end;
}
/* 行内小按钮保留原型自定义长相（不换 antd 默认样式） */
.cc-op {
  height: 26px;
  padding: 0 9px;
  font-size: 12.5px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--card);
  cursor: pointer;
  color: var(--text);
  transition: all 0.15s;
}
.cc-op {
  border-color: color-mix(in srgb, var(--primary) 35%, var(--border));
  background: color-mix(in srgb, var(--primary) 8%, var(--card));
  color: var(--primary);
}
.cc-op:hover {
  border-color: var(--primary);
  background: color-mix(in srgb, var(--primary) 16%, var(--card));
  color: var(--primary);
}
.cc-op-del {
  border-color: color-mix(in srgb, var(--error) 35%, var(--border));
  background: color-mix(in srgb, var(--error) 8%, var(--card));
  color: var(--error);
}
.cc-op-del:hover {
  border-color: var(--error);
  background: color-mix(in srgb, var(--error) 16%, var(--card));
  color: var(--error);
}
.cc-empty {
  padding: 44px 20px;
  text-align: center;
  color: var(--text3);
  font-size: 13px;
}
.cc-foot {
  padding: 0 22px 16px;
  font-size: 12px;
  color: var(--text3);
}

/* ---- 移动端（<768px）：卡头堆叠、操作按钮占满整行；PC 一条不动 ---- */
@media (max-width: 767px) {
  .cc-cardhd {
    flex-direction: column;
    gap: 12px;
    padding: 14px 14px 12px;
  }
  .cc-headact { width: 100%; }
  .cc-headact :deep(.ant-btn) { flex: 1; }
  .cc-sect { padding: 14px 14px 4px; }
  .cc-memrow { padding: 2px 14px 0; flex-wrap: wrap; }
  .cc-membar { flex: 1 1 100%; max-width: none; }
  .cc-tablewrap { padding: 10px 8px 14px; }
  .cc-foot { padding: 0 14px 14px; }
}
</style>
