<script setup lang="ts">
/* 转存历史页（侧边栏：自动转存 → 转存历史）
 * 全任务视角的自动转存执行历史：筛选（任务 / 网盘 / 状态 / 关键词）+ 分页，
 * 行上「详情」开共用 RunDetailModal。数据源 GET /pa/runs（与单任务「转存日志」同库同形）。
 * 与「转存记录」页刻意分开：那边只展示手动查询转存。 */
import { computed, onMounted, ref, watch } from 'vue'
import ClearHistoryButton, { type ClearRange } from '@/components/ClearHistoryButton.vue'
import { message } from 'ant-design-vue'
import { ReloadOutlined } from '@ant-design/icons-vue'
import PkPager from '@/components/PkPager.vue'
import RunDetailModal from './RunDetailModal.vue'
import { DRIVE_META } from '@/api/mock/meta'
import { listPaRuns, listPaTasks, clearRuns, type PaRunListItem } from '@/api/modules/tasks'
import { useIsMobile } from '@/composables/useIsMobile'
import type { MainDriveType, PaTask } from '@/types/model'

const isMobile = useIsMobile()

/* ===== 筛选 ===== */
const tasks = ref<PaTask[]>([])
const taskOpts = computed(() => [
  { value: 0, label: '全部任务' },
  ...tasks.value.map((t) => ({ value: t.id, label: `${t.name}（${DRIVE_META[t.type].name}）` })),
])
const fTask = ref(0)
const fType = ref('')
const fStatus = ref('')
const kw = ref('')

const TYPE_OPTS = [
  { value: '', label: '全部网盘' },
  { value: 'baidu', label: '百度网盘' },
  { value: 'quark', label: '夸克网盘' },
  { value: '115', label: '115 网盘' },
]
const STATUS_OPTS = [
  { value: '', label: '全部状态' },
  { value: 'success', label: '成功' },
  { value: 'fail', label: '失败' },
]

const rows = ref<PaRunListItem[]>([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const size = ref(20)

async function load() {
  loading.value = true
  try {
    const r = await listPaRuns({
      task_id: fTask.value || null,
      type: fType.value,
      status: fStatus.value,
      keyword: kw.value,
      page: page.value,
      page_size: size.value,
    })
    rows.value = r.items
    total.value = r.total
  } catch {
    message.error('转存历史加载失败')
  } finally {
    loading.value = false
  }
}

/* 筛选/每页变化回第一页（分页参数变了也重拉） */
watch([fTask, fType, fStatus, kw, size], () => {
  page.value = 1
  load()
})
watch(page, load)

onMounted(async () => {
  try {
    tasks.value = await listPaTasks()
  } catch {
    /* 任务下拉失败不影响历史列表 */
  }
  load()
})

/* ===== 详情 ===== */
const detailOpen = ref(false)
const detailId = ref<number | null>(null)
function openDetail(id: number) {
  detailId.value = id
  detailOpen.value = true
}

/* ===== 展示助手 ===== */
function metaOf(t: string) {
  return DRIVE_META[(t as MainDriveType)] || DRIVE_META.baidu
}
function durTxt(s: number): string {
  if (!s) return '—'
  if (s < 60) return `${s} 秒`
  return `${Math.floor(s / 60)} 分 ${s % 60} 秒`
}
/** 清空转存历史（下拉选范围） */
async function onClear(range: ClearRange) {
  let before = ''
  if (range !== 'all') {
    const d = new Date()
    d.setMonth(d.getMonth() - { '1m': 1, '3m': 3, '6m': 6 }[range])
    before = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} 00:00:00`
  }
  const n = await clearRuns(before)
  message.success(n > 0 ? `已清空 ${n} 条执行记录` : '没有符合条件的记录')
  await load()
}
</script>

<template>
  <div>
    <div class="card hr-flush">
      <div class="filterbar fb-head">
        <a-select v-model:value="fTask" :options="taskOpts" style="width: 220px" />
        <a-select v-model:value="fType" :options="TYPE_OPTS" style="width: 120px" />
        <a-select v-model:value="fStatus" :options="STATUS_OPTS" style="width: 110px" />
        <a-input v-model:value="kw" placeholder="任务名 / 消息关键词" style="width: 200px" allow-clear />
        <span class="hr-flex1"></span>
        <ClearHistoryButton @clear="onClear" />
        <a-button :loading="loading" type="primary" ghost @click="load">
          <template #icon><ReloadOutlined /></template>
          刷新
        </a-button>
      </div>

      <table v-if="!isMobile" class="hr-table">
        <thead>
          <tr>
            <th style="width: 18%">任务</th>
            <th style="width: 64px">网盘</th>
            <th style="width: 152px">开始时间</th>
            <th style="width: 152px">结束时间</th>
            <th style="width: 72px">结果</th>
            <th style="width: 240px">统计</th>
            <th>说明</th>
            <th style="width: 72px">耗时</th>
            <th style="width: 64px">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id">
            <td>
              <div class="hr-namecell">
                <span class="hr-srcbar" :style="{ background: metaOf(r.task_type).color }"></span>
                <span class="hr-resname" :title="r.task_name">{{ r.task_name }}</span>
              </div>
            </td>
            <td><span class="tag" :class="metaOf(r.task_type).tag">{{ metaOf(r.task_type).name }}</span></td>
            <td class="small muted hr-nowrap">{{ r.started }}</td>
            <td class="small muted hr-nowrap">{{ r.finished || '—' }}</td>
            <td><span class="tag" :class="r.overall?.cls || (r.status === 'success' ? 't-ok' : 't-bad')">{{ r.overall?.st || (r.status === 'success' ? '成功' : '失败') }}</span></td>
            <td>
              <div class="hr-statline">
                <span class="hr-pill" :class="r.add ? 'ok' : 'dim'">新增 {{ r.add }}</span>
                <span class="hr-pill dim">跳过 {{ r.skip }}</span>
                <span class="hr-pill" :class="r.fail ? 'bad' : 'dim'">失败 {{ r.fail }}</span>
              </div>
              <span class="hr-substat">分享 {{ r.total_share }} · 正则未命中 {{ r.regex_miss }} · MD5 跳过 {{ r.skip_md5 }}</span>
            </td>
            <td class="small hr-msgclip" :class="r.status === 'success' ? 'hr-msg-ok' : 'hr-msg-bad'" :title="r.message">{{ r.message || '—' }}</td>
            <td class="small muted hr-nowrap">{{ durTxt(r.duration) }}</td>
            <td>
              <a-button type="link" size="small" class="hr-op" @click="openDetail(r.id)">详情</a-button>
            </td>
          </tr>
          <tr v-if="!rows.length">
            <td colspan="9" class="pq-empty">{{ loading ? '加载中…' : '还没有执行历史 · 自动任务跑过一次这里就有记录' }}</td>
          </tr>
        </tbody>
      </table>

      <!-- 手机端：一次执行一张卡，点卡片看详情 -->
      <div v-else class="hr-cards">
        <div
          v-for="r in rows"
          :key="r.id"
          class="hr-card"
          role="button"
          tabindex="0"
          @click="openDetail(r.id)"
          @keydown.enter.prevent="openDetail(r.id)"
        >
          <div class="hr-card-top">
            <span class="hr-srcbar" :style="{ background: metaOf(r.task_type).color }"></span>
            <span class="hr-card-name">{{ r.task_name }}</span>
            <span class="tag" :class="r.overall?.cls || (r.status === 'success' ? 't-ok' : 't-bad')">{{ r.overall?.st || (r.status === 'success' ? '成功' : '失败') }}</span>
          </div>
          <div class="hr-card-meta small muted">{{ r.started }}<template v-if="r.finished"> → {{ r.finished }}</template></div>
          <div class="hr-card-stats small">
            新增 <b class="ok">{{ r.add }}</b> · 跳过 {{ r.skip }} · 失败 <b :class="{ bad: r.fail }">{{ r.fail }}</b> · 耗时 {{ durTxt(r.duration) }}
          </div>
          <div class="hr-card-msg small muted">{{ r.message || '—' }}</div>
        </div>
        <div v-if="!rows.length" class="pq-empty">{{ loading ? '加载中…' : '还没有执行历史' }}</div>
      </div>

      <PkPager v-model:current="page" v-model:pageSize="size" :total="total" />
    </div>

    <RunDetailModal v-model:open="detailOpen" :run-id="detailId" />
  </div>
</template>

<style scoped>
/* 卡片去内边距：筛选条接表头、表格接分页连成一张卡（记录页同款，但类名自带——跨页借 scoped 类不生效） */
.hr-flush { padding: 0; overflow: hidden; }
.hr-flex1 { flex: 1; }
.hr-namecell { display: flex; align-items: center; min-width: 0; }
.hr-srcbar { width: 3px; height: 30px; border-radius: 2px; flex: none; margin-right: 10px; }
.hr-resname { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* 表格走全局基础样式（th 13px/600、td padding 14/20），这里只收窄横向内边距 + 固定布局
   —— 别再自己压字号/行高，会跟记录页两张表长得不一样（实测对比过） */
.hr-table { table-layout: fixed; }
.hr-table th,
.hr-table td { padding-left: 12px; padding-right: 12px; vertical-align: middle; }
.hr-nowrap { white-space: nowrap; }
/* 统计列 tag 三段配色（2026-10-03 晚定稿，原则：颜色只传递信号，不做装饰）——
   绿 = 确实新增了（新增 0 不染绿，免得跟「结果·成功」tag 混成一个脸）；
   红 = 确实有失败；灰 = 中性/零值。跳过永远是灰：它是预期行为，不该抢眼睛。
   三段统一细边框，形状一致，只有语义色不同（与「结果」tag 同族长相）。 */
.hr-statline { display: flex; gap: 8px; align-items: center; white-space: nowrap; }
.hr-pill { font-size: 12px; padding: 1px 8px; border-radius: 6px; border: 1px solid var(--split); background: var(--surface-2); color: var(--text2); }
.hr-pill.ok { color: #389e0d; background: #f6ffed; border-color: #b7eb8f; }
.hr-pill.bad { color: #cf1322; background: #fff1f0; border-color: #ffa39e; }
.hr-pill.dim { color: var(--text3); background: var(--surface-2); border-color: var(--split); }
html[data-theme='dark'] .hr-pill { color: var(--text2); background: rgba(255, 255, 255, 0.06); border-color: rgba(255, 255, 255, 0.14); }
html[data-theme='dark'] .hr-pill.ok { color: #95de64; background: rgba(82, 196, 26, 0.16); border-color: rgba(82, 196, 26, 0.38); }
html[data-theme='dark'] .hr-pill.bad { color: #ff9c9c; background: rgba(255, 77, 79, 0.16); border-color: rgba(255, 77, 79, 0.38); }
html[data-theme='dark'] .hr-pill.dim { color: var(--text3); background: rgba(255, 255, 255, 0.06); border-color: rgba(255, 255, 255, 0.12); }
.hr-substat { display: block; margin-top: 2px; font-size: 12px; color: var(--text3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
/* ⚠️ 别再加 max-width: 0——那是 auto 布局时代的技巧，fixed 布局下会把单元格内容盒压成 0，
   说明列直接一片空白（2026-10-03 实测踩过） */
.hr-msgclip { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* 说明列与统计列之间留一点间距（用户要求，16px 温和不挤） */
.hr-table td.hr-msgclip { padding-left: 16px; }
/* 说明列按成败染色（同转存日志弹窗口径） */
.hr-msg-ok { color: #389e0d; }
.hr-msg-bad { color: #cf1322; }
html[data-theme='dark'] .hr-msg-ok { color: #95de64; }
html[data-theme='dark'] .hr-msg-bad { color: #ff9c9c; }
/* 操作列文字链与记录页同款（link 按钮去掉内边距，密集排布才不飘） */
.hr-op { padding: 0; }
/* 手机卡片：与记录页同款形态——平铺行 + 细分隔线 + 内容缩进对齐色条，不用独立小盒子 */
.hr-cards { display: block; }
.hr-card { padding: 12px 14px; border-bottom: 1px solid var(--split); cursor: pointer; -webkit-tap-highlight-color: transparent; }
.hr-card:active { background: var(--surface-3); }
.hr-card-top { display: flex; align-items: center; gap: 8px; min-width: 0; }
.hr-card-name { flex: 1; min-width: 0; font-size: 13.5px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hr-card-meta { margin: 8px 0 0 11px; font-family: var(--font-mono); font-size: 11.5px; color: var(--text3); }
.hr-card-stats { margin: 7px 0 0 11px; font-size: 12.5px; }
.hr-card-stats b.ok { color: #52c41a; }
.hr-card-stats b.bad { color: #ff4d4f; }
.hr-card-msg { margin: 7px 0 0 11px; font-size: 12.5px; color: var(--text3); overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }

/* 手机端筛选条分行（同记录页）：下拉平分一行、输入独占一行、按钮整行好按 */
@media (max-width: 767px) {
  .fb-head :deep(.ant-select) { flex: 1 1 0; width: auto !important; }
  .fb-head .hr-flex1 { display: none; }
  .fb-head :deep(.ant-input-affix-wrapper),
  .fb-head :deep(.ant-input) { flex: 1 1 100%; width: auto !important; }
  .fb-head :deep(.ant-btn) { flex: 1 1 100%; }
}
</style>
