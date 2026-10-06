<script setup lang="ts">
/* 转存记录页 —— 原型 _shell.html 记录段（全部记录表）的 Vue 移植。
 * 队列入口已撤（右下角浮标侧边抽屉是唯一入口），本页只做记录：筛选/分页真实生效
 * （内存过滤 + 切片）；记录是快照，抽屉展示转存当时的配置与结果。 */
import { computed, onMounted, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { CopyOutlined, FolderOpenOutlined } from '@ant-design/icons-vue'
import PkPager from '@/components/PkPager.vue'
import LogBox from '@/components/LogBox.vue'
import ShareFilesModal from '@/views/auto/ShareFilesModal.vue'
import { useIsMobile } from '@/composables/useIsMobile'
import { DD_MEDIA, DRIVE_META, MAIN_ORDER } from '@/api/mock/meta'
import { recordsStore } from '@/api/mock/records'
import type { RecordRow } from '@/api/mock/records'
import { listQmsPaths, listStrmPaths } from '@/api/modules/dd'
import { getSettings } from '@/api/modules/settings'
import {
  clearRecords3MonthsAgo,
  deleteRecord,
  getRecordLog,
  getRecordShareFiles,
  listRecords,
  retriggerRecord,
  retrigQms,
  retryFailedItems,
  triggerQms,
  triggerStrm,
} from '@/api/modules/records'
import type { DdQmsPath, DdStrmPath, QueueLogLine } from '@/types/model'

/* ===== 全部记录：数据 + 筛选 + 分页 ===== */
/* 手机（<768px）8 列表格换卡片列表，点卡片=开详情抽屉 */
const isMobile = useIsMobile()

const rows = ref<RecordRow[]>([])
async function reload() {
  rows.value = await listRecords()
}
onMounted(async () => {
  await reload()
  getSettings().then((d) => (mediaBackend.value = d.media?.backend || 'qms')).catch(() => {})
})

const fStatus = ref('') // ''=全部状态 ok=完成 part=部分失败 fail=失败
const fPan = ref('') // ''=全部网盘
const kw = ref('')
const STATUS_OPTS = [
  { value: '', label: '全部状态' },
  { value: 'ok', label: '完成' },
  { value: 'part', label: '部分失败' },
  { value: 'warn', label: '链接失效' },
  { value: 'fail', label: '失败' },
]
const PAN_OPTS = [
  { value: '', label: '全部网盘' },
  ...MAIN_ORDER.map((t) => ({ value: t, label: DRIVE_META[t].name })),
]

const filtered = computed(() =>
  rows.value.filter((r) => {
    // 结果列的几种长相：完成(t-ok) / 部分(t-off) / 链接失效(t-warn) / 失败(t-bad)
    if (fStatus.value === 'ok' && r.cls !== 't-ok') return false
    if (fStatus.value === 'part' && !r.st.startsWith('部分')) return false
    if (fStatus.value === 'warn' && r.cls !== 't-warn') return false
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

/* ===== 行内「查看文件 / 复制链接」（对齐自动转存行操作） ===== */
const sfOpen = ref(false)
const sfRec = ref<RecordRow | null>(null)
function onViewFiles(r: RecordRow) {
  sfRec.value = r
  sfOpen.value = true
}
async function onCopy(r: RecordRow) {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(r.share_url)
    } else {
      // 非安全上下文（http 部署）兜底：临时 textarea + execCommand
      const ta = document.createElement('textarea')
      ta.value = r.share_url
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

// 详情抽屉里「最近结果 → 详情」的文件清单弹窗
const filesOpen = ref(false)
function fileCls(st: string): string {
  if (st === '已转存') return 't-ok'
  if (st === '未转存') return 't-bad'
  return 't-off'
}
function fmtSize(v: number): string {
  if (!v) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let x = v
  let i = 0
  while (x >= 1024 && i < units.length - 1) {
    x /= 1024
    i++
  }
  return `${i === 0 || x >= 100 ? Math.round(x) : x.toFixed(1)} ${units[i]}`
}

async function onRetry() {
  if (!cur.value) return
  const n = await retryFailedItems(cur.value)
  if (n > 0) message.success(`已重新提交 ${n} 个失败项`)
  else message.info('这条记录没有失败项，不用重试')
}
/** 联动后端（决定触发行为：qms=重触发刮削 / litepan=重发 Webhook） */
const mediaBackend = ref<'qms' | 'litepan'>('qms')
async function onRetrigQms() {
  if (!cur.value) return
  await retrigQms(cur.value)
  message.success('已再次触发 QMS 刮削')
}

/* ===== 行级「触发」（详情后）：按联动后端分流重新触发 ===== */
const retriggingRows = ref(new Set<number>())
async function onRetrigger(r: RecordRow) {
  if (retriggingRows.value.has(r.id)) return
  retriggingRows.value = new Set(retriggingRows.value).add(r.id)
  try {
    const res = await retriggerRecord(r.id)
    if (res.ok) message.success(res.message || '已触发')
    else message.error(res.message || '触发失败', 5)
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(detail || '触发失败（该目录可能未配置联动）', 5)
  } finally {
    const next = new Set(retriggingRows.value)
    next.delete(r.id)
    retriggingRows.value = next
  }
}
const isRetrigging = (id: number) => retriggingRows.value.has(id)
async function onDelete() {
  if (!cur.value) return
  await deleteRecord(cur.value.id)
  message.success('记录已删除')
  drawerOpen.value = false
  await reload()
}

/** 操作列的行内删除（红字，popconfirm 二次确认） */
async function onRowDelete(r: RecordRow) {
  await deleteRecord(r.id)
  message.success('记录已删除')
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
/** 目录列表拉取中：弹窗先开，下拉转圈等数据（QMS 在 NAS 上，现场拉要几百 ms～秒级） */
const pathsLoading = ref(false)

/** QMS/STRM 目录拉取：失败自动重试一次（代理偶发抖动别当成 QMS 没配），两连败才返回 null */
async function fetchPathsSafe(): Promise<[DdQmsPath[], DdStrmPath[]] | null> {
  for (let i = 0; i < 2; i++) {
    try {
      return [await listQmsPaths(), await listStrmPaths()]
    } catch {
      if (i) return null
      await new Promise((r) => setTimeout(r, 800))
    }
  }
  return null
}

async function openTrig() {
  // 弹窗立即开，目录后台拉（loading 态）——点按钮卡半秒的体验太差
  trigQms.value = ''
  trigStrm.value = ''
  trigOpen.value = true
  if (qmsPaths.value.length) return
  pathsLoading.value = true
  const got = await fetchPathsSafe()
  pathsLoading.value = false
  if (!got) message.warning('QMS 未启用或连接失败，目录列表拉不到；先到「转存配置」里联动 QMS')
  qmsPaths.value = got?.[0] || []
  strmPaths.value = got?.[1] || []
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
    <!-- 全部记录：筛选条与表格合并成一张卡（同搜索页：别让两块白卡夹灰缝）。
         队列入口在右下角浮标（侧边抽屉），本页不再放队列 tab -->
    <div class="card rk-flush">
        <div class="filterbar fb-head">
          <a-select v-model:value="fStatus" :options="STATUS_OPTS" style="width: 120px" />
          <a-select v-model:value="fPan" :options="PAN_OPTS" style="width: 120px" />
          <a-input v-model:value="kw" placeholder="资源名称" style="width: 200px" allow-clear />
          <span class="rk-flex1"></span>
          <a-button v-if="mediaBackend === 'qms'" type="primary" ghost @click="openTrig">触发 QMS / STRM</a-button>
          <a-button danger ghost @click="onClearOld">清空三月前记录</a-button>
        </div>
        <table v-if="!isMobile" class="rk-table">
          <thead>
            <tr>
              <!-- 列宽配比：名称/结果双主力列，长报错在结果列内截断（title 看全文），
                   右侧 QMS/STRM/时间/操作收窄，别让宽屏下中间断崖、右边全空 -->
              <th style="width: 28%">资源名称</th>
              <th style="width: 118px">来源</th>
              <th style="width: 200px">目标位置</th>
              <th style="width: 30%">结果</th>
              <th style="width: 96px">{{ mediaBackend === 'litepan' ? '整理' : 'QMS 整理' }}</th>
              <th style="width: 96px">{{ mediaBackend === 'litepan' ? 'STRM' : 'STRM 生成' }}</th>
              <th style="width: 88px">时间</th>
              <th style="width: 110px">操作</th>
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
              <td>
                <span class="rk-srccell">
                  <span class="tag" :class="metaOf(r).tag">{{ metaOf(r).name }}</span>
                  <template v-if="r.share_url">
                    <a-tooltip title="查看文件"><button class="pa-ico pa-ico-view" @click.stop="onViewFiles(r)"><FolderOpenOutlined /></button></a-tooltip>
                    <a-tooltip title="复制链接"><button class="pa-ico pa-ico-copy" @click.stop="onCopy(r)"><CopyOutlined /></button></a-tooltip>
                  </template>
                </span>
              </td>
              <td class="small muted rk-path" :title="r.p">{{ r.p }}</td>
              <td><span class="tag rk-tagclip" :class="r.cls" :title="r.st">{{ r.st }}</span></td>
              <td v-if="mediaBackend === 'litepan'">
                <span class="tag rk-tagclip rk-lp">LitePan 接管</span>
              </td>
              <td v-else><span class="tag rk-tagclip" :class="r.qms.cls" :title="r.qms.st">{{ r.qms.st }}</span></td>
              <td v-if="mediaBackend === 'litepan'" class="small muted">—</td>
              <td v-else><span class="tag rk-tagclip" :class="r.strm.cls" :title="r.strm.st">{{ r.strm.st }}</span></td>
              <td class="small muted rk-nowrap">{{ r.tm }}</td>
              <td>
                <a-button type="link" size="small" class="rk-detail" @click="openDrawer(r)">详情</a-button>
                <span class="rk-opdiv">丨</span>
                <a-button type="link" size="small" class="rk-detail" :disabled="isRetrigging(r.id)" :title="isRetrigging(r.id) ? '正在触发…' : '按联动后端重新触发'" @click="onRetrigger(r)">触发</a-button>
                <span class="rk-opdiv">丨</span>
                <a-popconfirm title="确定删除这条记录？" ok-text="删除" cancel-text="取消" @confirm="onRowDelete(r)">
                  <a-button type="link" danger size="small" class="rk-detail">删除</a-button>
                </a-popconfirm>
              </td>
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
            </div>
            <!-- 结果/报错独占一行：都放开换行——名字看得全，errno 长文案也看得全 -->
            <div class="rk-card-tagline">
              <span class="tag" :class="r.cls">{{ r.st }}</span>
            </div>
            <div class="rk-card-meta">
              <span class="tag" :class="metaOf(r).tag">{{ metaOf(r).name }}</span>
              <template v-if="r.share_url">
                <a-tooltip title="查看文件"><button class="pa-ico pa-ico-view" @click.stop="onViewFiles(r)"><FolderOpenOutlined /></button></a-tooltip>
                <a-tooltip title="复制链接"><button class="pa-ico pa-ico-copy" @click.stop="onCopy(r)"><CopyOutlined /></button></a-tooltip>
              </template>
              <span class="small muted rk-card-path">{{ r.p }}</span>
            </div>
            <div class="rk-card-foot">
              <span v-if="mediaBackend === 'litepan'" class="tag rk-card-tag rk-lp">整理：LitePan 接管</span>
              <template v-else>
                <span class="tag rk-card-tag" :class="r.qms.cls" :title="r.qms.st">整理：{{ r.qms.st }}</span>
                <span class="tag rk-card-tag" :class="r.strm.cls" :title="r.strm.st">STRM：{{ r.strm.st }}</span>
              </template>
              <span class="small muted rk-card-tm">{{ r.tm }}</span>
            </div>
          </div>
          <div v-if="!paged.length" class="pq-empty">没有匹配的记录 · 换个筛选条件试试</div>
        </div>
        <PkPager v-model:current="page" v-model:pageSize="size" :total="filtered.length" />
      </div>

    <!-- 详情抽屉：快照字段 + 执行日志 + 记录级操作（手机版宽度 100%，与队列抽屉同规矩） -->
    <a-drawer v-model:open="drawerOpen" :width="isMobile ? '100%' : 660" title="转存详情">
      <template v-if="cur">
        <dl class="snap">
          <dt>任务名称</dt>
          <dd class="rk-taskname">{{ cur.n }}</dd>
          <dt>所属网盘</dt>
          <dd><span class="tag" :class="metaOf(cur).tag">{{ metaOf(cur).full }}</span></dd>
          <dt>分享链接</dt>
          <dd class="small muted">
            {{ cur.share_url }}<template v-if="cur.share_code"> · 提取码 {{ cur.share_code }}</template>
          </dd>
          <dt>保存到</dt>
          <dd class="rk-mono">{{ cur.p }}</dd>
          <dt>包含子目录</dt>
          <dd><span class="tag" :class="cur.include_subdirs ? 't-ok' : 't-off'">{{ cur.include_subdirs ? '是' : '否' }}</span></dd>
          <dt>排除文件</dt>
          <dd>{{ cur.exclude_count }} 个</dd>
          <dt>完成后动作</dt>
          <dd>
            <span class="tag" :class="cur.qms.cls">QMS {{ cur.qms.st }}</span>
            <span class="tag" :class="cur.strm.cls">STRM {{ cur.strm.st }}</span>
            <span v-if="cur.post_notify" class="tag t-ok">Server 酱推送</span>
            <span
              v-if="cur.qms.cls === 't-off' && cur.strm.cls === 't-off' && !cur.post_notify"
              class="small muted"
            >—</span>
          </dd>
          <dt>上次执行</dt>
          <dd class="small muted">{{ cur.tm }}</dd>
          <dt>最近结果</dt>
          <dd>
            <span class="tag" :class="cur.cls">{{ cur.st }}</span>
            <a-button v-if="cur.files && cur.files.length" type="link" size="small" class="rk-filesbtn" @click="filesOpen = true">
              详情
            </a-button>
          </dd>
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

    <!-- 转存文件清单弹窗：转存当时的分享内文件快照（后端 files_json），逐文件标去向 -->
    <a-modal v-model:open="filesOpen" title="转存文件清单" :width="isMobile ? '94%' : 560" :footer="null">
      <div class="rk-filelist">
        <div v-for="(f, i) in cur?.files || []" :key="i" class="rk-file">
          <div class="rk-file-info">
            <div class="rk-file-path" :title="f.path">{{ f.path || f.name }}</div>
            <div class="small muted">{{ fmtSize(f.size) }}</div>
          </div>
          <span class="tag" :class="fileCls(f.st)">{{ f.st }}</span>
        </div>
        <div v-if="!cur?.files?.length" class="pq-empty">这条记录没有文件清单快照</div>
      </div>
    </a-modal>

    <!-- 分享内容文件树弹窗：与自动转存「查看」同款（fetcher 走记录的分享链接） -->
    <ShareFilesModal
      v-model:open="sfOpen"
      :task-id="null"
      :task-name="sfRec?.n || ''"
      :fetcher="(refresh: boolean) => getRecordShareFiles(sfRec!.id, refresh)"
    />

    <!-- 手动触发弹窗：QMS / STRM 都可空，选哪个触发哪个；都选时隔 10 秒触发第二个 -->
    <a-modal v-model:open="trigOpen" title="手动触发" :width="480" ok-text="立即触发" cancel-text="取消" @ok="confirmTrig">
      <div class="rk-tip">
        选择要触发的目标，<b>可以选空</b>；两个都选时先触发 QMS，间隔 15 秒后自动触发 STRM。
      </div>
      <div class="rk-field">
        <label>QMS 刮削目录</label>
        <a-select v-model:value="trigQms" :options="trigQmsOpts" :loading="pathsLoading" placeholder="目录加载中…" style="width: 100%" />
      </div>
      <div class="rk-field">
        <label>STRM 生成</label>
        <a-select v-model:value="trigStrm" :options="trigStrmOpts" :loading="pathsLoading" placeholder="目录加载中…" style="width: 100%" />
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

/* 来源格：网盘 tag + 「查看文件/复制链接」图标钮（同自动转存 .pa-ico 的五色钮语义：
   查看=蓝 / 复制=紫；本页只这两个，样式就近自带一份——scoped 不跨页） */
.rk-srccell {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  min-width: 0;
}
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
/* 详情丨删除 之间的竖线分隔 */
.rk-opdiv {
  color: var(--text4);
  font-size: 12px;
  margin: 0 2px;
  user-select: none;
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
/* 任务名称：手机上长名不折行会把 dl 网格顶乱，强制可断行 */
.rk-taskname {
  font-weight: 500;
  word-break: break-all;
}
.rk-filesbtn {
  padding: 0 4px;
}
/* 文件清单弹窗：等宽路径单行省略，右侧去向 tag 固定不挤 */
.rk-filelist {
  max-height: 62vh;
  overflow: auto;
}
.rk-file {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 2px;
  border-bottom: 1px solid var(--split);
}
.rk-file:last-child {
  border-bottom: none;
}
.rk-file-info {
  flex: 1;
  min-width: 0;
}
.rk-file-path {
  font-family: var(--font-mono);
  font-size: 12.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rk-file .tag {
  flex: none;
  margin-right: 0;
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

/* LitePan 接管徽标：淡紫描边（联动后端=litepan 时整理列） */
.rk-lp {
  color: #b39cf8;
  max-width: none !important;
  border-color: rgba(179, 156, 248, 0.45) !important;
  background: rgba(179, 156, 248, 0.08) !important;
}

/* ---- 移动端（<768px）：8 列表格换卡片列表；PC 一条不动 ---- */
.rk-cards { display: none; }
@media (max-width: 767px) {
  /* 筛选条手机版分行：状态/网盘挤一行，资源名称独占一行，两个按钮各占一行 */
  .fb-head :deep(.ant-select) {
    flex: 1 1 0;
    width: auto !important; /* 压掉行内 style="width:120px"，两个下拉平分一行 */
  }
  .fb-head .rk-flex1 {
    display: none; /* 弹性占位会拉着输入框同行，手机版去掉 */
  }
  .fb-head :deep(.ant-input-affix-wrapper),
  .fb-head :deep(.ant-input) {
    flex: 1 1 100%;
    width: auto !important;
  }
  .fb-head :deep(.ant-btn) {
    flex: 1 1 100%; /* 触发 / 清空各占一整行，好按 */
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
    /* 名称独占一行放开换行（用户要求看得全），不再两行截断 */
    word-break: break-all;
  }
  /* 结果/报错行：tag 放开换行看全文（errno 长文案折两行也比截断强） */
  .rk-card-tagline {
    margin: 8px 0 0 11px;
    min-width: 0;
  }
  .rk-card-tagline .tag {
    white-space: normal;
    word-break: break-all;
    max-width: 100%;
    margin-right: 0;
  }
  /* QMS/STRM 这类短 tag 保持限宽省略，别把时间挤跑 */
  .rk-card-tag {
    flex: 0 1 auto;
    min-width: 0;
    max-width: 72%;
    overflow: hidden;
    text-overflow: ellipsis;
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
