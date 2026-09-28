<script setup lang="ts">
/* =====================================================================
 * 缓存配置页 —— 原型 parts/page-cache.html（cc- 前缀）的 Vue 移植。
 * 三段：缓存策略（开关/失效时间/自动刷新）→ 内存保护（水位条/阈值/降级/条目上限）
 *       → 已缓存目录表（fresh 绿 / stale 红，行内刷新/清除）。
 * 配置改完即静默写回 mock store（原型如此）；刷新=重置过期时间，清除=删行。
 * 水位条颜色随实际占用变化：<75 绿 / 75-85 黄 / >85 红。
 * ===================================================================== */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { DRIVE_META } from '@/api/mock/meta'
import {
  clearAllCacheTrees,
  clearCacheTree,
  getCacheConfig,
  listCacheTrees,
  refreshAllCacheTrees,
  refreshCacheTree,
  saveCacheConfig,
} from '@/api/modules/cache'
import type { CacheCfg, CacheTree, MemUsage } from '@/api/mock/cache'

/* ===== 页面状态 ===== */
const cfg = reactive<CacheCfg>({
  master: true,
  auto: true,
  ttl: 30,
  ttlUnit: '小时',
  memHigh: 85,
  act: 'ladder',
  maxSizeMb: 800,
})
const mem = ref<MemUsage>({ pct: 62, usedMb: 0, totalMb: 800 })
const trees = ref<CacheTree[]>([])

let loaded = false // 首次装载期间不回写（否则 onMounted 的赋值会触发一轮保存）
onMounted(async () => {
  const r = await getCacheConfig()
  Object.assign(cfg, r.cfg)
  mem.value = r.mem
  trees.value = await listCacheTrees()
  loaded = true
})

/* 配置任意一项变化 → 静默持久化（真实系统为 PUT /api/cache/config） */
watch(cfg, (v) => {
  if (loaded) void saveCacheConfig({ ...v })
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

/* ===== 已缓存目录表 ===== */
const staleCount = computed(() => trees.value.filter((t) => t.ttlMin < 0).length)
const countTip = computed(() =>
  trees.value.length
    ? `${trees.value.length} 棵目录树 · ${staleCount.value ? staleCount.value + ' 棵已过期' : '全部有效'}`
    : '暂无缓存',
)

function metaName(type: CacheTree['type']): string {
  return DRIVE_META[type]?.name ?? type
}
function metaColor(type: CacheTree['type']): string {
  return DRIVE_META[type]?.color ?? '#1677ff'
}
function ttlText(t: CacheTree): string {
  if (t.ttlMin < 0) return '已过期 · 待刷新'
  const h = Math.round((t.ttlMin / 60) * 10) / 10
  return h >= 1 ? `缓存中 · 剩 ${h} 小时` : `缓存中 · 剩 ${t.ttlMin} 分钟`
}

async function reload() {
  trees.value = await listCacheTrees()
}

/* 刷新一棵 = 重置为当前配置下的满血 TTL（真实系统重拉网盘目录后回填） */
async function onRefreshRow(t: CacheTree) {
  await refreshCacheTree(t.id)
  message.success(`已刷新「${t.path}」的目录树缓存`)
  await reload()
}
async function onClearRow(t: CacheTree) {
  await clearCacheTree(t.id)
  message.success(`已清除「${t.path}」的缓存`)
  await reload()
}
async function onRefreshAll() {
  const n = await refreshAllCacheTrees()
  message.success(`已刷新全部 ${n} 棵目录树`)
  await reload()
}
async function onClearAll() {
  await clearAllCacheTrees()
  message.success('缓存已清空，下次打开转存弹窗会重新拉取')
  await reload()
}
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
          <a-button @click="onRefreshAll">立即刷新全部</a-button>
          <a-popconfirm title="清空全部目录树缓存？" ok-text="清空" cancel-text="取消" @confirm="onClearAll">
            <a-button>清空缓存</a-button>
          </a-popconfirm>
        </div>
      </div>

      <!-- ===== 段一：缓存策略 ===== -->
      <div class="cc-sect">缓存策略<span class="cc-secttip">多久过期、要不要提前续</span></div>
      <div class="formrow">
        <label>目录树缓存</label>
        <div class="ctl">
          <a-switch v-model:checked="cfg.master" @change="onMasterSw" />
          <span class="muted small">总开关，关闭后转存弹窗每次都实时拉网盘目录</span>
        </div>
      </div>
      <div class="formrow">
        <label>缓存失效时间</label>
        <div class="ctl">
          <a-input-number v-model:value="cfg.ttl" :min="1" style="width: 110px" @change="toastCfg()" />
          <a-select v-model:value="cfg.ttlUnit" :options="TTLUNIT_OPTS" style="width: 110px" @change="toastCfg()" />
          <span class="muted small">超时未命中就重新拉取，保证新建文件夹能被看到</span>
        </div>
      </div>
      <div class="formrow">
        <label>自动刷新</label>
        <div class="ctl">
          <a-switch v-model:checked="cfg.auto" @change="onAutoSw" />
          <span class="muted small">过期前后台预取下一版目录树，打开转存弹窗永远零等待</span>
        </div>
      </div>

      <!-- ===== 段二：内存保护 ===== -->
      <div class="cc-sect">内存保护<span class="cc-secttip">内存吃紧时自动降级，转存按钮永远打得开</span></div>
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
        <label>内存水位阈值</label>
        <div class="ctl">
          <a-select v-model:value="cfg.memHigh" :options="MEMHIGH_OPTS" style="width: 180px" @change="toastCfg()" />
          <span class="muted small">超过这条水位线开始降级</span>
        </div>
      </div>
      <div class="formrow">
        <label>降级动作</label>
        <div class="ctl">
          <a-select v-model:value="cfg.act" :options="ACT_OPTS" style="width: 180px" @change="toastCfg('降级策略已更新')" />
          <span class="muted small">
            压缩条目 → 减半缓存上限 → 关闭缓存 · 降到哪一级都只是变慢，不会让转存不可用
          </span>
        </div>
      </div>
      <div class="formrow">
        <label>缓存大小上限（MB）</label>
        <div class="ctl">
          <a-input-number v-model:value="cfg.maxSizeMb" :min="50" style="width: 130px" @change="toastCfg()" />
          <span class="muted small">超过后按「最久未使用」淘汰目录</span>
        </div>
      </div>

      <!-- ===== 段三：已缓存目录 ===== -->
      <div class="cc-sect">已缓存目录<span class="cc-secttip">{{ countTip }}</span></div>
      <div class="cc-tablewrap pk-hscroll">
        <table class="cc-table">
          <thead>
            <tr>
              <th class="cc-th" style="width: 110px">网盘</th>
              <th class="cc-th" style="width: 160px">账号</th>
              <th class="cc-th">目录</th>
              <th class="cc-th" style="width: 80px">条目</th>
              <th class="cc-th" style="width: 80px">大小</th>
              <th class="cc-th" style="width: 130px">状态</th>
              <th class="cc-th cc-th-ops" style="width: 130px">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="t in trees" :key="t.id" class="cc-row">
              <td class="cc-td">
                <span class="cc-pantag">
                  <i class="cc-pandot" :style="{ background: metaColor(t.type) }"></i>{{ metaName(t.type) }}
                </span>
              </td>
              <td class="cc-td">{{ t.acc }}</td>
              <td class="cc-td cc-path">{{ t.path }}</td>
              <td class="cc-td cc-num">{{ t.entries }}</td>
              <td class="cc-td cc-num">{{ t.size }}</td>
              <td class="cc-td cc-ttl" :class="t.ttlMin >= 0 ? 'fresh' : 'stale'">{{ ttlText(t) }}</td>
              <td class="cc-td">
                <span class="cc-ops">
                  <button class="cc-op" @click="onRefreshRow(t)">刷新</button>
                  <button class="cc-op cc-op-del" @click="onClearRow(t)">清除</button>
                </span>
              </td>
            </tr>
            <tr v-if="!trees.length">
              <td colspan="7" class="cc-empty">缓存是空的 · 打开一次转存弹窗就会把目录树存进来</td>
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
  flex: 0 0 40%;
  max-width: 40%;
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
.cc-ttl.fresh {
  color: #389e0d;
}
.cc-ttl.stale {
  color: var(--error);
}
html[data-theme='dark'] .cc-ttl.fresh {
  color: #73d13d;
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
.cc-op:hover {
  border-color: var(--primary);
  color: var(--primary);
}
.cc-op-del:hover {
  border-color: var(--error);
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
