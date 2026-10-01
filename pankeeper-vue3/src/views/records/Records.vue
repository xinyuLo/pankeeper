<script setup lang="ts">
/* 转存记录页 —— 原型 _shell.html 记录段（tabs + 队列看板 + 全部记录表）的 Vue 移植。
 * 队列分段只做壳：看板本体在 @/queue/QueueBoard（引擎数据自动刷新）。
 * 全部记录：筛选/分页真实生效（内存过滤 + 切片）；记录是快照，抽屉展示转存当时的配置与结果。 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { message } from 'ant-design-vue'
import QueueBoard from '@/queue/QueueBoard.vue'
import PkPager from '@/components/PkPager.vue'
import LogBox from '@/components/LogBox.vue'
import { useIsMobile } from '@/composables/useIsMobile'
import { queueView } from '@/queue/engine'
import { DD_MEDIA, DRIVE_META, MAIN_ORDER } from '@/api/mock/meta'
import { recordsStore } from '@/api/mock/records'
import type { RecordRow } from '@/api/mock/records'
import { listQmsPaths, listStrmPaths } from '@/api/modules/dd'
import {
  clearRecords3MonthsAgo,
  deleteRecord,
  getRecordLog,
  listRecords,
  retrigQms,
  retryFailedItems,
  triggerQms,
  triggerStrm,
} from '@/api/modules/records'
import type { DdQmsPath, DdStrmPath, QueueLogLine } from '@/types/model'

/* ===== 分段 tab：默认全部记录；#queue 直达队列段（右下角浮标跳转用） ===== */
const route = useRoute()
/* 手机（<768px）8 列表格换卡片列表，点卡片=开详情抽屉 */
const isMobile = useIsMobile()
const seg = ref<'queue' | 'records'>('records')
// hash 是分段唯一事实源（同原型）：#queue → 队列段；hash 消失（如从别处切回 /records）回落记录段
function applyHash() {
  seg.value = route.hash === '#queue' ? 'queue' : 'records'
}
watch(() => route.hash, applyHash)

// tab 上的排队数：wait + run；空队列显示 (空)（原型 pqQueueNum 的格式）
const queueNum = computed(() => {
  const n = queueView.tasks.filter((t) => t.status === 'wait' || t.status === 'run').length
  return n ? `(${n})` : '(空)'
})

/* ===== 全部记录：数据 + 筛选 + 分页 ===== */
const rows = ref<RecordRow[]>([])
async function reload() {
  rows.value = await listRecords()
}
onMounted(async () => {
  applyHash()
  await reload()
})

const fStatus = ref('') // ''=全部状态 ok=完成 part=部分失败 fail=失败
const fPan = ref('') // ''=全部网盘
const kw = ref('')
const STATUS_OPTS = [
  { value: '', label: '全部状态' },
  { value: 'ok', label: '完成' },
  { value: 'part', label: '部分失败' },
  { value: 'fail', label: '失败' },
]
const PAN_OPTS = [
  { value: '', label: '全部网盘' },
  ...MAIN_ORDER.map((t) => ({ value: t, label: DRIVE_META[t].name })),
]

const filtered = computed(() =>
  rows.value.filter((r) => {
    // 结果列的三种长相：完成(t-ok) / 部分(t-off) / 失败(t-bad)
    if (fStatus.value === 'ok' && r.cls !== 't-ok') return false
    if (fStatus.value === 'part' && !r.st.startsWith('部分')) return false
    if (fStatus.value === 'fail' && !r.st.startsWith('失败')) return false
    if (fPan.value && r.t !== fPan.value) return false
    const k = kw.value.trim()
    if (k && !r.n.includes(k)) return false
    return true
  }),
)

const page = ref(1)
const size = ref(8)
const paged = computed(() => filtered.value.slice((page.value - 1) * size.value, page.value * size.value))
// 筛选条件变化回第 1 页；结果变少时把页码夹回有效范围
watch([fStatus, fPan, kw], () => {
  page.value = 1
})
watch(
  () => filtered.value.length,
  (n) => {
    const max = Math.max(1, Math.ceil(n / size.value))
    if (page.value > max) page.value = max
  },
)

function metaOf(r: RecordRow) {
  return DRIVE_META[r.t]
}

/* ===== 清空三月前记录 ===== */
async function onClearOld() {
  const n = await clearRecords3MonthsAgo()
  if (n > 0) {
    message.success(`已清空 ${n} 条三月前记录`)
    await reload()
  } else {
    message.info('没有三个月前的记录')
  }
}

/* ===== 详情抽屉 ===== */
const drawerOpen = ref(false)
const cur = ref<RecordRow | null>(null)
const curLog = ref<QueueLogLine[]>([])
async function openDrawer(r: RecordRow) {
  cur.value = r
  drawerOpen.value = true
  curLog.value = await getRecordLog(r)
}

// cron 表达式 → 人话（原型 openTaskDetail 的 cronText）
const CRON_LABEL: Record<string, string> = {
  '0 3 * * *': '每天 03:00',
  '0 12 * * *': '每天 12:00',
  '0 */6 * * *': '每 6 小时',
  '0 9 * * 1': '每周一 09:00',
  '30 8 * * *': '每天 08:30',
}
function cronText(c: string): string {
  if (!c) return '未开启定时'
  return (CRON_LABEL[c] || '自定义表达式') + '（' + c + '）'
}

async function onRetry() {
  if (!cur.value) return
  const n = await retryFailedItems(cur.value)
  if (n > 0) message.success(`已重新提交 ${n} 个失败项`)
  else message.info('这条记录没有失败项，不用重试')
}
async function onRetrigQms() {
  if (!cur.value) return
  await retrigQms(cur.value)
  message.success('已再次触发 QMS 刮削')
}
async function onDelete() {
  if (!cur.value) return
  await deleteRecord(cur.value.id)
  message.success('记录已删除')
  drawerOpen.value = false
  await reload()
}

/* ===== 手动触发弹窗（trigMask）=====
 * 两个下拉首项都是「（不触发）」；都空拦下不关；
 * 只选谁触发谁（立即）；都选 → QMS 立即 + STRM 真 setTimeout 15 秒。 */
const trigOpen = ref(false)
const trigQms = ref('')
const trigStrm = ref('')
const qmsPaths = ref<DdQmsPath[]>([])
const strmPaths = ref<DdStrmPath[]>([])

function qmsLabel(p: DdQmsPath): string {
  return `#${p.id} · ${DD_MEDIA[p.media_type] || p.media_type || '未分类'} · ${p.source_path}`
}
function strmLabel(p: DdStrmPath): string {
  return `#${p.id} · ${p.remote_path}`
}
const trigQmsOpts = computed(() => [
  { value: '', label: '（不触发）' },
  ...qmsPaths.value.map((p) => ({ value: String(p.id), label: qmsLabel(p) })),
])
const trigStrmOpts = computed(() => [
  { value: '', label: '（不触发）' },
  ...strmPaths.value.map((p) => ({ value: String(p.id), label: strmLabel(p) })),
])
async function openTrig() {
  // 候选来自转存配置页暴露的 QMS/STRM 目录，进弹窗时拉一次即可
  if (!qmsPaths.value.length) {
    qmsPaths.value = await listQmsPaths()
    strmPaths.value = await listStrmPaths()
  }
  trigQms.value = ''
  trigStrm.value = ''
  trigOpen.value = true
}
async function confirmTrig() {
  const qv = trigQms.value
  const sv = trigStrm.value
  if (!qv && !sv) {
    message.warning('请至少选择一个要触发的目标（或直接取消）')
    return
  }
  const q = qmsPaths.value.find((p) => String(p.id) === qv) || null
  const s = strmPaths.value.find((p) => String(p.id) === sv) || null
  const qTxt = q ? qmsLabel(q) : ''
  const sTxt = s ? strmLabel(s) : ''
  trigOpen.value = false
  if (q) {
    await triggerQms(q.id, qTxt)
    message.success('已触发 QMS 刮削 → ' + qTxt)
  }
  if (s) {
    if (q) {
      // 都选：STRM 与 QMS 真隔 10 秒（留痕 strmPending 对应原型 rcStrmPending）
      recordsStore.strmPending = true
      window.setTimeout(async () => {
        recordsStore.strmPending = false
        await triggerStrm(s.id, sTxt)
        message.success(`已触发 STRM 生成 → ${sTxt}（与 QMS 间隔 15 秒）`)
      }, 15000)
    } else {
      await triggerStrm(s.id, sTxt)
      message.success('已触发 STRM 生成 → ' + sTxt)
    }
  }
}
</script>

<template>
  <div>
    <!-- 分段 tab：转存动作全部入队，这里看排队进度和日志 -->
    <div class="tabs pq-tabs">
      <div :class="{ on: seg === 'queue' }" @click="seg = 'queue'">转存队列<span class="pq-num">{{ queueNum }}</span></div>
      <div :class="{ on: seg === 'records' }" @click="seg = 'records'">全部记录</div>
    </div>

    <!-- 转存队列分段：看板本体在 QueueBoard 组件（日志单选/进度/空态都自带） -->
    <template v-if="seg === 'queue'">
      <div class="card rk-flush">
        <QueueBoard />
      </div>

    </template>

    <!-- 全部记录分段：筛选条与表格合并成一张卡（同搜索页：别让两块白卡夹灰缝） -->
    <template v-else>
      <div class="card rk-flush">
        <div class="filterbar fb-head">
          <a-select v-model:value="fStatus" :options="STATUS_OPTS" style="width: 120px" />
          <a-select v-model:value="fPan" :options="PAN_OPTS" style="width: 120px" />
          <a-input v-model:value="kw" placeholder="资源名称" style="width: 200px" allow-clear />
          <span class="rk-flex1"></span>
          <a-button type="primary" ghost @click="openTrig">触发 QMS / STRM</a-button>
          <a-button danger ghost @click="onClearOld">清空三月前记录</a-button>
        </div>
        <table v-if="!isMobile" class="rk-table">
          <thead>
            <tr>
              <th style="width: 24%">资源名称</th>
              <th style="width: 64px">来源</th>
              <th style="width: 160px">目标位置</th>
              <th>结果</th>
              <th style="width: 96px">QMS 整理</th>
              <th style="width: 96px">STRM 生成</th>
              <th style="width: 76px">时间</th>
              <th style="width: 60px">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in paged" :key="r.id">
              <td>
                <div class="rk-namecell">
                  <span class="srcbar" :style="{ background: metaOf(r).color }"></span>
                  <span class="resname" :title="r.n">{{ r.n }}</span>
                </div>
              </td>
              <td><span class="tag" :class="metaOf(r).tag">{{ metaOf(r).name }}</span></td>
              <td class="small muted rk-path" :title="r.p">{{ r.p }}</td>
              <td><span class="tag rk-tagclip" :class="r.cls" :title="r.st">{{ r.st }}</span></td>
              <td><span class="tag rk-tagclip" :class="r.qms.cls" :title="r.qms.st">{{ r.qms.st }}</span></td>
              <td><span class="tag rk-tagclip" :class="r.strm.cls" :title="r.strm.st">{{ r.strm.st }}</span></td>
              <td class="small muted rk-nowrap">{{ r.tm }}</td>
              <td><a-button type="link" size="small" class="rk-detail" @click="openDrawer(r)">详情</a-button></td>
            </tr>
            <tr v-if="!paged.length">
              <td colspan="8" class="pq-empty">没有匹配的记录 · 换个筛选条件试试</td>
            </tr>
          </tbody>
        </table>

        <!-- 手机端：一条记录一张卡，点卡片开详情（与表格同一 handler） -->
        <div v-else class="rk-cards">
          <div
            v-for="r in paged"
            :key="r.id"
            class="rk-card"
            role="button"
            tabindex="0"
            @click="openDrawer(r)"
            @keydown.enter.prevent="openDrawer(r)"
          >
            <div class="rk-card-top">
              <span class="srcbar" :style="{ background: metaOf(r).color }"></span>
              <span class="rk-card-name">{{ r.n }}</span>
              <span class="tag" :class="r.cls">{{ r.st }}</span>
            </div>
            <div class="rk-card-meta">
              <span class="tag" :class="metaOf(r).tag">{{ metaOf(r).name }}</span>
              <span class="small muted rk-card-path">{{ r.p }}</span>
            </div>
            <div class="rk-card-foot">
              <span class="tag" :class="r.qms.cls">{{ r.qms.st }}</span>
              <span class="tag" :class="r.strm.cls">{{ r.strm.st }}</span>
              <span class="small muted rk-card-tm">{{ r.tm }}</span>
            </div>
          </div>
          <div v-if="!paged.length" class="pq-empty">没有匹配的记录 · 换个筛选条件试试</div>
        </div>
        <PkPager v-model:current="page" v-model:pageSize="size" :total="filtered.length" />
      </div>

    </template>

    <!-- 详情抽屉：快照字段 + 执行日志 + 记录级操作 -->
    <a-drawer v-model:open="drawerOpen" :width="660" title="转存详情">
      <template v-if="cur">
        <dl class="snap">
          <dt>任务名称</dt>
          <dd>{{ cur.n }}</dd>
          <dt>所属网盘</dt>
          <dd><span class="tag" :class="metaOf(cur).tag">{{ metaOf(cur).full }}</span></dd>
          <dt>分享链接</dt>
          <dd class="small muted">
            {{ cur.share_url }}<template v-if="cur.share_code"> · 提取码 {{ cur.share_code }}</template>
          </dd>
          <dt>保存到</dt>
          <dd class="rk-mono">{{ cur.p }}</dd>
          <dt>定时策略</dt>
          <dd>{{ cronText(cur.cron) }}</dd>
          <dt>包含子目录</dt>
          <dd><span class="tag" :class="cur.include_subdirs ? 't-ok' : 't-off'">{{ cur.include_subdirs ? '是' : '否' }}</span></dd>
          <dt>排除文件</dt>
          <dd>{{ cur.exclude_count }} 个</dd>
          <dt>完成后动作</dt>
          <dd>
            <span v-if="cur.post_qms" class="tag t-ok">触发 QMS</span>
            <span v-if="cur.post_notify" class="tag t-ok">Server 酱推送</span>
            <span v-if="!cur.post_qms && !cur.post_notify" class="small muted">—</span>
          </dd>
          <dt>上次执行</dt>
          <dd class="small muted">{{ cur.tm }}</dd>
          <dt>最近结果</dt>
          <dd><span class="tag" :class="cur.cls">{{ cur.st }}</span></dd>
        </dl>
        <div class="sect">执行日志</div>
        <LogBox :lines="curLog" />
        <div class="rk-drawerbtns">
          <a-button type="primary" @click="onRetry">重试失败项</a-button>
          <a-button @click="onRetrigQms">再次触发 QMS</a-button>
          <a-button danger @click="onDelete">删除记录</a-button>
        </div>
      </template>
    </a-drawer>

    <!-- 手动触发弹窗：QMS / STRM 都可空，选哪个触发哪个；都选时隔 10 秒触发第二个 -->
    <a-modal v-model:open="trigOpen" title="手动触发" :width="480" ok-text="立即触发" cancel-text="取消" @ok="confirmTrig">
      <div class="rk-tip">
        选择要触发的目标，<b>可以选空</b>；两个都选时先触发 QMS，间隔 15 秒后自动触发 STRM。
      </div>
      <div class="rk-field">
        <label>QMS 刮削目录</label>
        <a-select v-model:value="trigQms" :options="trigQmsOpts" style="width: 100%" />
      </div>
      <div class="rk-field">
        <label>STRM 生成</label>
        <a-select v-model:value="trigStrm" :options="trigStrmOpts" style="width: 100%" />
      </div>
    </a-modal>
  </div>
</template>

<style scoped>
/* 卡片去内边距：筛选条接表头、表格接分页，连成一张完整卡（原型 padding:0;overflow:hidden） */
.rk-flush {
  padding: 0;
  overflow: hidden;
}
.rk-flex1 {
  flex: 1;
}

/* 资源名称格：品牌色条 + 名称，超长省略（原型 srcbar/resname 结构） */
.rk-namecell {
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

/* 表格单元格：路径走等宽、时间别折行 */
/* 记录表：8 列挤一屏，左右内边距从全局 20px 收到 12px（全局值留给内容少的页） */
/* table-layout: fixed 让表头的宽度声明生效——否则浏览器按内容分配，
   一条超长结果文案就能把「目标位置」挤成 5 行（实测踩坑） */
.rk-table {
  table-layout: fixed;
}
.rk-table :deep(th),
.rk-table :deep(td) {
  padding-left: 12px;
  padding-right: 12px;
}
.rk-path {
  font-family: var(--font-mono);
  /* 路径整条省略号，不要 word-break: break-all —— 它会把长路径硬折成 5 行 */
  max-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rk-nowrap {
  white-space: nowrap;
}
/* 结果/QMS/STRM 的 tag：文案可能很长（如「提取码验证失败：errno=-9（…）」），
   在定宽列里必须截断，否则它顶着 .tag 的 nowrap 把整张表的列宽撑爆 */
.rk-tagclip {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: middle;
}
.rk-detail {
  padding: 0;
}

/* 详情抽屉：快照 dl 网格（原型 .snap/.sect 结构） */
.snap {
  display: grid;
  grid-template-columns: 96px 1fr;
  gap: 11px 14px;
  font-size: 13.5px;
  margin-bottom: 22px;
}
.snap dt {
  color: var(--text3);
}
.snap dd {
  min-width: 0;
  overflow-wrap: anywhere;
}
.rk-mono {
  font-family: var(--font-mono);
}
.sect {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 10px;
  color: var(--text2);
}
.rk-drawerbtns {
  margin-top: 20px;
  display: flex;
  gap: 10px;
}

/* 手动触发弹窗：提示条 + 两行下拉（对应原型 dd-tip/dd-field 的观感） */
.rk-tip {
  font-size: 13px;
  color: var(--text2);
  background: var(--surface-2);
  border-radius: var(--r-sm);
  padding: 10px 12px;
  margin-bottom: 16px;
  line-height: 1.7;
}
.rk-field {
  margin-bottom: 14px;
}
.rk-field label {
  display: block;
  font-size: 13px;
  color: var(--text2);
  margin-bottom: 6px;
}

/* ---- 移动端（<768px）：8 列表格换卡片列表；PC 一条不动 ---- */
.rk-cards { display: none; }
@media (max-width: 767px) {
  /* 筛选条两颗动作按钮换行时占满整行，好按 */
  .filterbar :deep(.ant-btn) {
    flex: 1 1 auto;
  }

  .rk-cards { display: block; }
  .rk-card {
    padding: 12px 14px;
    border-bottom: 1px solid var(--split);
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
  }
  .rk-card:active { background: var(--surface-3); }
  .rk-card-top {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
  }
  .rk-card-name {
    flex: 1;
    min-width: 0;
    font-size: 13.5px;
    font-weight: 500;
    line-height: 1.45;
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    word-break: break-all;
  }
  .rk-card-meta {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 7px 0 0 11px;
    min-width: 0;
  }
  .rk-card-path {
    flex: 1;
    min-width: 0;
    font-family: var(--font-mono);
    font-size: 11.5px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .rk-card-foot {
    display: flex;
    align-items: center;
    gap: 6px;
    margin: 8px 0 0 11px;
    flex-wrap: wrap;
  }
  .rk-card-foot .tag { margin-right: 0; }
  .rk-card-tm { margin-left: auto; white-space: nowrap; font-size: 11.5px; }

  /* 详情抽屉操作按钮窄屏换行 */
  .rk-drawerbtns { flex-wrap: wrap; }
}
</style>
