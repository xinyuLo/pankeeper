<script setup lang="ts">
/* 自动转存页（/auto/:type，百度/夸克/115 三路由共用一个组件）—— 原型 parts/page-auto.html 移植。
 * 工具条 + 任务表 8 列 + 五色行操作 + 设计说明；四个弹窗拆成独立组件：
 * TaskModal（任务配置，含叠加的 DirModal）/ RunModal（执行监控）/ ExclModal（排除清单）。
 * 互斥规则：开执行监控关掉其余弹窗；Esc 逐层关。排除入口在任务行（不在编辑弹窗里）。 */
import { computed, onMounted, ref, watch } from 'vue'
import { useBackGuard } from '@/composables/useBackGuard'
import { useRoute } from 'vue-router'
import { message } from 'ant-design-vue'
import {
  CaretRightOutlined,
  EditOutlined,
  MinusCircleOutlined,
  ProfileOutlined,
  FileTextOutlined,
  DeleteOutlined,
  FolderOpenOutlined,
  ExportOutlined,
  CopyOutlined,
  SyncOutlined,
} from '@ant-design/icons-vue'
import LogBox from '@/components/LogBox.vue'
import PkPager from '@/components/PkPager.vue'
import TaskModal from './TaskModal.vue'
import RunModal from './RunModal.vue'
import RunHistoryModal from './RunHistoryModal.vue'
import ExclModal from './ExclModal.vue'
import { useIsMobile } from '@/composables/useIsMobile'
import { DRIVE_META } from '@/api/mock/meta'
import {
  cronHuman,
  deletePaTask,
  getPaDetailLog,
  listPaTasks,
  retriggerTaskQms,
  togglePaTask,
} from '@/api/modules/tasks'
import type { MainDriveType, PaTask, QueueLogLine } from '@/types/model'
import ShareFilesModal from './ShareFilesModal.vue'

const route = useRoute()
const type = computed(() => (route.params.type as MainDriveType) || 'baidu')
const meta = computed(() => DRIVE_META[type.value])
/* 手机（<768px）8 列任务表换卡片列表，动作按钮一行铺开 */
const isMobile = useIsMobile()

/* ===== 任务列表：随路由参数切网盘 ===== */
const tasks = ref<PaTask[]>([])
async function reload() {
  tasks.value = await listPaTasks(type.value)
}
watch(type, reload)
onMounted(reload)

/* ===== 分页（内存切片；切网盘回第 1 页） ===== */
const page = ref(1)
const size = ref(20)
const pagedTasks = computed(() => tasks.value.slice((page.value - 1) * size.value, page.value * size.value))
watch(type, () => (page.value = 1))
watch(
  () => tasks.value.length,
  () => {
    const max = Math.max(1, Math.ceil(tasks.value.length / size.value))
    if (page.value > max) page.value = max
  },
)

/* ===== 表格展示助手 ===== */
function linkTrunc(url: string): string {
  // 18 字符封顶：链接列只要够认出是哪条分享即可，完整 URL 在 title 与「复制」里
  return url.length > 18 ? url.slice(0, 18) + '…' : url
}
/* 最近结果压缩显示：后端格式是「新增 N / 跳过 N / 失败 N」，跳过为 0 时是纯噪音，
   失败必须留（非 0 才显示）——压到一行「新增 106」量级，整格一行放得下（用户要求） */
function compactResult(s: string): string {
  const m = /新增\s*(\d+)\s*\/\s*跳过\s*(\d+)\s*\/\s*失败\s*(\d+)/.exec(s || '')
  if (!m) return s || ''
  const fail = Number(m[3])
  return fail ? `新增 ${m[1]} / 失败 ${m[3]}` : `新增 ${m[1]}`
}
function cronText(cron: string): string {
  return cronHuman(cron, '仅手动')
}
const STATUS_TEXT: Record<PaTask['last_status'], string> = {
  success: '成功',
  partial: '部分失败',
  fail: '失败',
  running: '执行中',
  never: '从未执行',
}

/* ===== 行操作 ===== */
async function onToggle(t: PaTask) {
  const on = await togglePaTask(t.id)
  message.success(on ? '已启用任务' : '已暂停任务')
}

/* ===== 分享链接三按钮：查看（实时文件树）/ 跳转 / 复制 ===== */
const sfOpen = ref(false)
const sfTask = ref<PaTask | null>(null)

function onViewFiles(t: PaTask) {
  sfTask.value = t
  sfOpen.value = true
}

function onJump(t: PaTask) {
  if (!t.share_url) {
    message.warning('该任务没有分享链接')
    return
  }
  window.open(t.share_url, '_blank')
}

async function onCopy(t: PaTask) {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(t.share_url)
    } else {
      // 非安全上下文（http 部署）兜底：临时 textarea + execCommand
      const ta = document.createElement('textarea')
      ta.value = t.share_url
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

async function onDel(t: PaTask) {
  await deletePaTask(t.id)
  message.success(`已删除任务「${t.name}」`)
  await reload()
}

/* ===== 重新触发 QMS：QMS 侧刮失败后手动重刷（联动目标=任务配置优先、转存目录兜底；不动任务配置） ===== */
const reQmsId = ref<number | null>(null)
async function onRetriggerQms(t: PaTask) {
  if (reQmsId.value) return // 防连点
  reQmsId.value = t.id
  try {
    const r = await retriggerTaskQms(t.id)
    if (r.ok) message.success(r.message || '已触发 QMS 刮削')
    else message.warning(r.message || '触发失败')
  } catch (e) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(detail || '触发失败，详见服务端日志')
  } finally {
    reQmsId.value = null
  }
}

function onCheckAll() {
  message.info(`已开始检查「${meta.value.full}」全部任务`)
}

/* ===== 任务弹窗（新增/编辑） ===== */
const taskOpen = ref(false)
useBackGuard(taskOpen)
const editing = ref<PaTask | null>(null)
function openAdd() {
  editing.value = null
  taskOpen.value = true
}
function openEdit(t: PaTask) {
  editing.value = t
  taskOpen.value = true
}
function onSaved() {
  reload() // toast 在弹窗里发过；这里只刷表
}

/* ===== 执行（paRun）：开监控（互斥：关掉其余弹窗）。排除清单缓存由后端转存时自动刷新 ===== */
const runOpen = ref(false)
useBackGuard(runOpen)
const runTask = ref<PaTask | null>(null)
function openRun(t: PaTask) {
  runTask.value = t
  taskOpen.value = false
  exclOpen.value = false
  runOpen.value = true
}
function onRunFinished() {
  reload() // last_run/last_status/last_result 已回写，刷新让表格变色
}

/* ===== 排除清单：行内直开（不经过编辑弹窗） ===== */
const exclOpen = ref(false)
useBackGuard(exclOpen)
const exclTask = ref<PaTask | null>(null)
function openExcl(t: PaTask) {
  exclTask.value = t
  exclOpen.value = true
}
function onExclCommitted() {
  reload()
}

/* ===== 任务详情抽屉（原型 openTaskDetail：快照字段 + 执行日志） ===== */
const detailOpen = ref(false)
useBackGuard(detailOpen)
const detailTask = ref<PaTask | null>(null)
const detailLog = ref<QueueLogLine[]>([])
async function openDetail(t: PaTask) {
  detailTask.value = t
  detailOpen.value = true
  detailLog.value = await getPaDetailLog()
}

/* ===== 转存日志（RunHistory 卡片列表） ===== */
const runHistOpen = ref(false)
const runHistTask = ref<PaTask | null>(null)
function openRunHistory(t: PaTask) {
  runHistTask.value = t
  runHistOpen.value = true
}
function detailCron(c: string): string {
  return cronHuman(c, '未开启定时')
}
</script>

<template>
  <div class="pa-wrap">
    <!-- 工具条 -->
    <div class="pa-toolbar">
      <div class="pa-left">
        <h2 class="pa-title">{{ meta.full }} · 自动转存</h2>
        <span class="pa-count">共 {{ tasks.length }} 个任务</span>
      </div>
      <div class="pa-right">
        <button class="pa-btn pa-btn-primary" @click="openAdd">＋ 新增任务</button>
        <button class="pa-btn" @click="onCheckAll">立即检查全部</button>
      </div>
    </div>

    <!-- 任务表：PC 表格 / 手机卡片列表互斥 -->
    <div class="pa-card">
      <!-- 横向滚动兜底：表格自带 min-width，窗口过窄时滚列而不是毁列宽 -->
      <div v-if="!isMobile" class="pa-tablewrap">
      <table class="pa-table">
        <thead>
          <tr>
            <th class="pa-th">任务名</th>            <th class="pa-th">启用</th>
            <th class="pa-th">分享链接</th>
            <th class="pa-th">定时策略</th>
            <th class="pa-th">已排除</th>
            <th class="pa-th">上次执行</th>
            <th class="pa-th">最近结果</th>
            <th class="pa-th pa-th-ops">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in pagedTasks" :key="t.id" class="pa-row" :style="{ borderLeft: '3px solid ' + meta.color }">
            <td class="pa-td pa-namecell">
              <span class="pa-name" :title="t.name">{{ t.name }}</span>
            </td>
            <td class="pa-td">
              <span
                class="pa-switch"
                :class="{ 'pa-on': t.enabled }"
                role="switch"
                :aria-checked="t.enabled"
                @click="onToggle(t)"
              ><span class="pa-knob"></span></span>
            </td>
            <td class="pa-td pa-link">
              <span class="pa-url" :title="t.share_url">{{ linkTrunc(t.share_url) }}</span>
              <span v-if="t.share_code" class="pa-code">{{ t.share_code }}</span>
              <div class="pa-linkops">
                <a-tooltip title="查看文件"><button class="pa-ico pa-ico-view" @click="onViewFiles(t)"><FolderOpenOutlined /></button></a-tooltip>
                <a-tooltip title="打开网盘"><button class="pa-ico pa-ico-jump" @click="onJump(t)"><ExportOutlined /></button></a-tooltip>
                <a-tooltip title="复制链接"><button class="pa-ico pa-ico-copy" @click="onCopy(t)"><CopyOutlined /></button></a-tooltip>
              </div>
            </td>
            <td class="pa-td pa-nowrap">{{ cronText(t.cron) }}</td>
            <td class="pa-td pa-muted">{{ t.exclude_count ? t.exclude_count + ' 项' : '—' }}</td>
            <td class="pa-td pa-muted pa-nowrap">{{ t.last_run || '—' }}</td>
            <td class="pa-td">
              <span class="pa-tag" :class="'pa-st-' + t.last_status" :title="t.last_result || ''">
                {{ STATUS_TEXT[t.last_status] }}<template v-if="t.last_result"> · {{ compactResult(t.last_result) }}</template>
              </span>
            </td>
            <td class="pa-td">
              <div class="pa-ops">
                <a-tooltip title="立即执行"><button class="pa-ico pa-op-run" @click="openRun(t)"><CaretRightOutlined /></button></a-tooltip>
                <a-tooltip title="编辑任务"><button class="pa-ico pa-op-edit" @click="openEdit(t)"><EditOutlined /></button></a-tooltip>
                <a-tooltip :title="t.exclude_count ? `排除清单（${t.exclude_count} 项）` : '排除清单'">
                  <button class="pa-ico pa-op-excl" @click="openExcl(t)">
                    <MinusCircleOutlined /><i v-if="t.exclude_count" class="pa-op-num">{{ t.exclude_count }}</i>
                  </button>
                </a-tooltip>
                <a-tooltip title="执行详情"><button class="pa-ico pa-op-detail" @click="openDetail(t)"><ProfileOutlined /></button></a-tooltip>
                <a-tooltip title="转存日志"><button class="pa-ico pa-op-log" @click="openRunHistory(t)"><FileTextOutlined /></button></a-tooltip>
                <a-tooltip
                  :title="t.last_status === 'partial'
                    ? '重新触发 QMS：先清掉 QMS 侧本次文件的失败记录再重刮（配了 STRM 会在刮削完成后自动续上）'
                    : '仅当最近一次是「部分失败」（QMS 刮失败）时可重刷'"
                >
                  <button
                    class="pa-ico pa-op-qms"
                    :disabled="reQmsId === t.id || t.last_status !== 'partial'"
                    @click="onRetriggerQms(t)"
                  >
                    <SyncOutlined :spin="reQmsId === t.id" />
                  </button>
                </a-tooltip>
                <a-popconfirm
                  :title="`确认删除任务「${t.name}」？此操作不可恢复。`"
                  ok-text="删除"
                  cancel-text="取消"
                  :ok-button-props="{ danger: true }"
                  @confirm="onDel(t)"
                >
                  <a-tooltip title="删除任务"><button class="pa-ico pa-op-del"><DeleteOutlined /></button></a-tooltip>
                </a-popconfirm>
              </div>
            </td>
          </tr>
          <tr v-if="!tasks.length">
            <td class="pa-td pa-muted" colspan="8" style="text-align: center; padding: 28px">当前网盘暂无自动转存任务</td>
          </tr>
        </tbody>
      </table>
      </div>
      <PkPager v-if="!isMobile" v-model:current="page" v-model:pageSize="size" :total="tasks.length" />

      <!-- 手机端：一任务一卡（名称+开关 / 状态+定时 / 网盘链接+提取码+复制 / 执行信息 / 主操作+次操作字链） -->
      <div v-if="isMobile" class="pa-cards">
        <div v-for="t in pagedTasks" :key="t.id" class="pa-carditem" :style="{ borderLeft: '3px solid ' + meta.color }">
          <div class="pa-c-top">
            <span class="pa-c-name">{{ t.name }}</span>
            <span
              class="pa-switch"
              :class="{ 'pa-on': t.enabled }"
              role="switch"
              :aria-checked="t.enabled"
              @click="onToggle(t)"
            ><span class="pa-knob"></span></span>
          </div>
          <div class="pa-c-meta">
            <span class="pa-tag" :class="'pa-st-' + t.last_status">
              {{ STATUS_TEXT[t.last_status] }}<template v-if="t.last_result"> · {{ compactResult(t.last_result) }}</template>
            </span>
            <span class="small muted">{{ cronText(t.cron) }}</span>
          </div>
          <div class="pa-c-row">
            <!-- 网盘链接本体：点击直接打开分享（替代原「跳转」按钮） -->
            <a class="pa-url" :href="t.share_url" target="_blank" rel="noopener" :title="t.share_url">{{ linkTrunc(t.share_url) }}</a>
            <span v-if="t.share_code" class="pa-code">{{ t.share_code }}</span>
            <button class="pa-op" @click="onCopy(t)">复制</button>
          </div>
          <div class="pa-c-row pa-c-info">
            <span>上次执行 {{ t.last_run || '—' }}</span>
            <span>已排除 {{ t.exclude_count ? t.exclude_count + ' 项' : '—' }}</span>
          </div>
          <div class="pa-c-ops">
            <button class="pa-op pa-op-run" @click="openRun(t)">执行</button>
            <button class="pa-op pa-op-log" @click="openRunHistory(t)">转存日志</button>
            <button class="pa-op pa-op-edit" @click="openEdit(t)">编辑</button>
          </div>
          <div class="pa-c-sub">
            <button class="pa-sublink" @click="onViewFiles(t)">查看文件</button>
            <button
              class="pa-sublink"
              :disabled="t.last_status !== 'partial'"
              :style="t.last_status !== 'partial' ? 'opacity:.45;cursor:default' : ''"
              @click="t.last_status === 'partial' && onRetriggerQms(t)"
            >重触发 QMS</button>
            <button class="pa-sublink" @click="openExcl(t)">排除<i v-if="t.exclude_count" class="pa-op-num">{{ t.exclude_count }}</i></button>
            <button class="pa-sublink" @click="openDetail(t)">详情</button>
            <a-popconfirm
              :title="`确认删除任务「${t.name}」？此操作不可恢复。`"
              ok-text="删除"
              cancel-text="取消"
              :ok-button-props="{ danger: true }"
              @confirm="onDel(t)"
            >
              <button class="pa-sublink danger">删除</button>
            </a-popconfirm>
          </div>
        </div>
        <div v-if="!tasks.length" class="pa-c-empty">当前网盘暂无自动转存任务</div>
      </div>
      <PkPager v-if="isMobile" v-model:current="page" v-model:pageSize="size" :total="tasks.length" />
    </div>



    <!-- 任务弹窗（内含叠加的目录选择弹窗） -->
    <TaskModal
      v-model:open="taskOpen"
      :type="type"
      :task="editing"
      :suspended="exclOpen"
      @saved="onSaved"
    />

    <!-- 执行监控：互斥，开它关其余 -->
    <RunModal v-model:open="runOpen" :task="runTask" @finished="onRunFinished" />
    <!-- 转存日志：历史执行卡片 + 详情 -->
    <RunHistoryModal v-model:open="runHistOpen" :task="runHistTask" />

    <!-- 排除清单：可叠在任务弹窗上（z-index 1002） -->
    <ExclModal v-model:open="exclOpen" :task="exclTask" @committed="onExclCommitted" />

    <!-- 任务详情抽屉 -->
    <a-drawer v-model:open="detailOpen" :width="620" :title="`任务详情 · ${detailTask?.name || ''}`">
      <template v-if="detailTask">
        <dl class="pa-snap">
          <dt>任务名称</dt>
          <dd>{{ detailTask.name }}</dd>
          <dt>所属网盘</dt>
          <dd><span class="tag" :class="meta.tag">{{ meta.full }}</span></dd>
          <dt>分享链接</dt>
          <dd class="small muted">
            {{ detailTask.share_url }}<template v-if="detailTask.share_code"> · 提取码 {{ detailTask.share_code }}</template>
          </dd>
          <dt>保存到</dt>
          <dd class="pa-mono">{{ detailTask.save_dir }}</dd>
          <dt>对比路径</dt>
          <dd class="pa-mono">{{ detailTask.compare_path || '（未设置，回退用保存目录）' }}</dd>
          <dt>定时策略</dt>
          <dd>{{ detailCron(detailTask.cron) }}</dd>
          <dt>包含子目录</dt>
          <dd>
            <span class="tag" :class="detailTask.include_subdirs ? 't-ok' : 't-off'">{{ detailTask.include_subdirs ? '是' : '否' }}</span>
          </dd>
          <dt>排除文件</dt>
          <dd>{{ detailTask.exclude_count || 0 }} 个</dd>
          <dt>完成后动作</dt>
          <dd>
            <span v-if="detailTask.post_qms" class="tag t-ok">触发 QMS</span>
            <span v-if="detailTask.post_notify" class="tag t-ok">Server 酱推送</span>
            <span v-if="!detailTask.post_qms && !detailTask.post_notify" class="small muted">—</span>
          </dd>
          <dt>上次执行</dt>
          <dd class="small muted">{{ detailTask.last_run || '—' }}</dd>
          <dt>最近结果</dt>
          <dd>
            <span class="pa-tag" :class="'pa-st-' + detailTask.last_status">{{ STATUS_TEXT[detailTask.last_status] }}</span>
            {{ detailTask.last_result || '—' }}
          </dd>
        </dl>
        <div class="pa-sect">执行日志（最近一次快照）</div>
        <LogBox :lines="detailLog" />
      </template>
    </a-drawer>
  </div>
  <!-- 查看分享内容：实时文件树 -->
  <ShareFilesModal v-model:open="sfOpen" :task-id="sfTask?.id ?? null" :task-name="sfTask?.name || ''" />
</template>

<style scoped>
/* 布局 .content 已带 28px 内边距，这里不再叠加（原型 pa-wrap 的 padding 由外壳负责） */
.pa-wrap { display: flex; flex-direction: column; }

/* ===== 工具条 ===== */
.pa-toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; gap: 12px; flex-wrap: wrap; }
.pa-left { display: flex; align-items: center; gap: 10px; }
.pa-title { margin: 0; font-size: 18px; font-weight: 600; color: var(--text); }
.pa-count {
  font-size: 13px;
  color: var(--text2);
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 2px 10px;
}
.pa-right { display: flex; gap: 8px; }
.pa-btn {
  height: 34px;
  padding: 0 14px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--card);
  color: var(--text);
  cursor: pointer;
  font-size: 13.5px;
  line-height: 1;
  font-family: inherit;
  transition: all 0.15s;
}
.pa-btn:hover { border-color: var(--primary-h); color: var(--primary); }
.pa-btn-primary { background: var(--primary); border-color: var(--primary); color: #fff; }
.pa-btn-primary:hover { background: var(--primary-h); border-color: var(--primary-h); color: #fff; }

/* ===== 任务表 ===== */
.pa-card { background: var(--card); border-radius: var(--r); box-shadow: var(--shadow); overflow: hidden; }
/* 横向滚动兜底容器：表格 min-width 撑住列宽，窗口窄了滚列不毁版面 */
.pa-tablewrap { overflow-x: auto; }
.pa-table { width: 100%; border-collapse: collapse; table-layout: auto; min-width: 1080px; }
/* 列宽配比（2026-10-03 晚重配，用户反馈：链接列吃光空白、上次执行挤到换行、操作列贴死右缘）：
   放弃 fixed 布局——fixed 下 `auto` 的链接列独吞全部剩余宽度（那格只有 230px 内容，其它全是空）。
   改 auto 布局后剩余空间按各列内容占比分摊，链接列被 .pa-url max-width 230px 压住不再霸场。
   仅保留必要 hint：启用（开关 64）与操作（六钮 196）按内容定死，其余交给浏览器。
   min-width 1080：窗口再窄由外层容器横向滚动兜底。 */
.pa-table th:nth-child(2) { width: 64px; }
/* 上次执行：MM-DD HH:MM 一行放下（84px 会折成两行——实测截图踩过） */
.pa-table th:nth-child(6) { width: 110px; white-space: nowrap; }
/* 操作列 7 个 26px 图标钮 + 6×2px 间距 = 194，加单元格内边距 24 → 230 够 */
.pa-table th:nth-child(8) { width: 230px; }
.pa-table .pa-td { overflow: hidden; }
.pa-nowrap { white-space: nowrap; }
/* 最近结果可能很长（成功 · 新增 106 / 跳过 0 / 失败 0），允许标签内换行，别截断 */
.pa-table .pa-td .pa-tag { white-space: normal; overflow-wrap: anywhere; }
.pa-name { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* 任务名 + 三个链接小钮同格：名称截断，图标靠右 */
.pa-namecell { display: flex; align-items: center; gap: 8px; min-width: 0; }
.pa-namecell .pa-name { flex: 1 1 auto; min-width: 0; }
/* 表头/单元格吃全局表格约定（th 13px/600/text2、td 13.5px、行 hover），这里只做两件本地事：
   ①横向内边距收窄到 12px（8 列，20px 太奢侈）；②表头与单元格左右内边距保持一致。
   别再自己压字号/字重——那会让本表和记录页一眼两种风格（实测对比过）。 */
.pa-th {
  padding: 13px 12px;
  white-space: nowrap;
}
/* 操作列表头/图标都左对齐：右对齐会贴死卡片右缘（用户反馈看着奇怪），左对齐跟图标对齐更自然 */
.pa-th-ops { text-align: left; }
.pa-td {
  padding: 14px 12px;
  color: var(--text);
  vertical-align: middle;
}
tbody tr.pa-row:last-child .pa-td { border-bottom: none; }
.pa-name { font-weight: 500; }
.pa-muted { color: var(--text3); }

/* 启用开关（原型自绘，保留手感和 class） */
.pa-switch {
  display: inline-block;
  width: 40px;
  height: 22px;
  border-radius: 11px;
  background: var(--border);
  position: relative;
  cursor: pointer;
  transition: background 0.2s;
  vertical-align: middle;
}
.pa-switch.pa-on { background: var(--primary); }
.pa-knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--card);
  transition: left 0.2s;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.2);
}
.pa-switch.pa-on .pa-knob { left: 20px; }

/* 分享链接格 */
.pa-link { display: flex; align-items: center; gap: 6px; white-space: nowrap; }
.pa-linkops { display: flex; gap: 2px; }
/* 链接三钮带语义色：查看=蓝 / 打开网盘=青 / 复制=紫，悬浮染同色浅底
   （双类选择器压过后面 .pa-ico 的默认色，单类会被盖掉——实测踩坑） */
.pa-ico.pa-ico-view { color: #1677ff; }
.pa-ico.pa-ico-view:hover { background: #f0f8ff; }
.pa-ico.pa-ico-jump { color: #08979c; }
.pa-ico.pa-ico-jump:hover { background: #e6fffb; }
.pa-ico.pa-ico-copy { color: #722ed1; }
.pa-ico.pa-ico-copy:hover { background: #f9f0ff; }
html[data-theme='dark'] .pa-ico.pa-ico-view { color: #69b1ff; }
html[data-theme='dark'] .pa-ico.pa-ico-view:hover { background: #111a2c; }
html[data-theme='dark'] .pa-ico.pa-ico-jump { color: #36cfc9; }
html[data-theme='dark'] .pa-ico.pa-ico-jump:hover { background: #0e2929; }
html[data-theme='dark'] .pa-ico.pa-ico-copy { color: #b37feb; }
html[data-theme='dark'] .pa-ico.pa-ico-copy:hover { background: #1a1425; }
.pa-url { color: var(--text2); max-width: 230px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pa-code {
  font-size: 12px;
  color: var(--text2);
  background: var(--split);
  border-radius: 4px;
  padding: 1px 6px;
  white-space: nowrap;
}
.pa-copy {
  height: 24px;
  padding: 0 8px;
  font-size: 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--card);
  cursor: pointer;
  color: var(--text2);
  font-family: inherit;
}
.pa-copy:hover { border-color: var(--primary-h); color: var(--primary); }

/* 最近结果染色 tag：success 绿 / partial 橙（转存成功但 QMS 有失败）/ fail 红 / running 蓝 / never 灰 */
.pa-tag { font-size: 12.5px; padding: 2px 8px; border-radius: 6px; display: inline-block; white-space: nowrap; }
.pa-st-success { color: #52c41a; background: rgba(82, 196, 26, 0.12); }
.pa-st-partial { color: #d48806; background: rgba(250, 173, 20, 0.14); }
.pa-st-fail { color: #ff4d4f; background: rgba(255, 77, 79, 0.12); }
.pa-st-running { color: #1677ff; background: rgba(22, 119, 255, 0.12); }
.pa-st-never { color: var(--text3); background: rgba(0, 0, 0, 0.05); }
html[data-theme='dark'] .pa-st-success { color: #95de64; background: rgba(82, 196, 26, 0.16); }
html[data-theme='dark'] .pa-st-partial { color: #ffc53d; background: rgba(250, 173, 20, 0.16); }
html[data-theme='dark'] .pa-st-fail { color: #ff9c9c; background: rgba(255, 77, 79, 0.16); }
html[data-theme='dark'] .pa-st-running { color: #69b1ff; background: rgba(22, 119, 255, 0.2); }
html[data-theme='dark'] .pa-st-never { color: var(--text3); background: rgba(255, 255, 255, 0.06); }

/* ===== 行操作五色按钮（颜色即语义）：
   执行=蓝 / 编辑=青 / 排除=橙（带计数徽标）/ 详情=中性 / 删除=红（Popconfirm 确认）
   桌面用 .pa-ico 图标钮（26px 方块，悬浮出 tooltip + 染色底），手机卡片仍用文字 .pa-op ===== */
.pa-ops { display: flex; gap: 2px; justify-content: flex-start; }
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
  position: relative;
  color: var(--text2);
  transition: background 0.15s, color 0.15s;
  font-family: inherit;
}
.pa-ico:hover { background: var(--surface-3); }
/* 计数徽标：右上角小角标，不占按钮内部空间 */
.pa-ico .pa-op-num {
  position: absolute;
  top: -4px;
  right: -6px;
  font-size: 10px;
  line-height: 14px;
  padding: 0 4px;
  border-radius: 999px;
  font-style: normal;
  background: #fff1b8;
  color: #d48806;
  border: 1px solid #ffe58f;
}
.pa-ico.pa-op-run { color: #1677ff; }
.pa-ico.pa-op-run:hover { background: #f0f8ff; }
.pa-ico.pa-op-edit { color: #08979c; }
.pa-ico.pa-op-edit:hover { background: #e6fffb; }
.pa-ico.pa-op-excl { color: #d48806; }
.pa-ico.pa-op-excl:hover { background: #fffbe6; }
.pa-ico.pa-op-detail { color: var(--text2); }
.pa-ico.pa-op-detail:hover { background: var(--surface-3); color: var(--text); }
/* 重新触发 QMS = 紫色（媒体联动语义，与转存的绿/排除的橙区分开） */
.pa-ico.pa-op-qms { color: #722ed1; }
.pa-ico.pa-op-qms:hover { background: #f9f0ff; }
.pa-ico.pa-op-qms:disabled { opacity: 0.55; cursor: default; }
.pa-ico.pa-op-del { color: #ff4d4f; }
.pa-ico.pa-op-del:hover { background: #fff1f0; }
/* 暗色：底色压暗、语义色提亮一档 */
html[data-theme='dark'] .pa-ico:hover { background: rgba(255, 255, 255, 0.08); }
html[data-theme='dark'] .pa-ico.pa-op-run:hover { background: #111a2c; }
html[data-theme='dark'] .pa-ico.pa-op-edit:hover { background: #0e2929; }
html[data-theme='dark'] .pa-ico.pa-op-excl:hover { background: #2b2111; }
html[data-theme='dark'] .pa-ico.pa-op-detail:hover { background: rgba(255, 255, 255, 0.1); color: var(--text); }
html[data-theme='dark'] .pa-ico.pa-op-qms { color: #b37feb; }
html[data-theme='dark'] .pa-ico.pa-op-qms:hover { background: #1a1425; }
html[data-theme='dark'] .pa-ico.pa-op-del:hover { background: #2b1314; }
html[data-theme='dark'] .pa-ico .pa-op-num { background: #594214; color: #ffe58f; border-color: #594214; }
.pa-op {
  height: 28px;
  padding: 0 10px;
  font-size: 13px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--card);
  cursor: pointer;
  color: var(--text);
  font-family: inherit;
  transition: all 0.15s;
  white-space: nowrap;
}
.pa-op:hover { border-color: var(--primary); color: var(--primary); }
.pa-op-run { color: #1677ff; border-color: #91caff; }
.pa-op-run:hover { background: #f0f8ff; border-color: #1677ff; color: #1677ff; }
.pa-op-edit { color: #08979c; border-color: #87e8de; }
.pa-op-edit:hover { background: #e6fffb; border-color: #08979c; color: #08979c; }
.pa-op-excl { color: #d48806; border-color: #ffd591; }
.pa-op-excl:hover { background: #fffbe6; border-color: #d48806; color: #d48806; }
.pa-op-num {
  display: inline-block;
  margin-left: 3px;
  font-size: 11px;
  background: #fff1b8;
  border-radius: 999px;
  padding: 0 5px;
  line-height: 16px;
  font-style: normal;
}
.pa-op-detail { color: var(--text2); }
.pa-op-detail:hover { border-color: var(--text3); color: var(--text); }
.pa-op-del { color: #ff4d4f; border-color: #ffccc7; }
.pa-op-del:hover { background: #fff1f0; border-color: #ff4d4f; color: #ff4d4f; }
/* 暗色一套（浅色 hover 底直接压暗，语义色提亮一档） */
html[data-theme='dark'] .pa-op-run { color: #69b1ff; border-color: #1d3948; }
html[data-theme='dark'] .pa-op-run:hover { background: #111a2c; border-color: #69b1ff; }
html[data-theme='dark'] .pa-op-edit { color: #36cfc9; border-color: #134848; }
html[data-theme='dark'] .pa-op-edit:hover { background: #0e2929; border-color: #36cfc9; }
html[data-theme='dark'] .pa-op-excl { color: #ffc53d; border-color: #594214; }
html[data-theme='dark'] .pa-op-excl:hover { background: #2b2111; border-color: #ffc53d; }
html[data-theme='dark'] .pa-op-num { background: #594214; color: #ffe58f; }
html[data-theme='dark'] .pa-op-del { color: #ff7875; border-color: #582a27; }
html[data-theme='dark'] .pa-op-del:hover { background: #2b1314; border-color: #ff7875; }
html[data-theme='dark'] .pa-sublink.danger { color: #ff7875; }

/* ===== 详情抽屉：快照 dl 网格（与转存记录页同一长相） ===== */
.pa-snap { display: grid; grid-template-columns: 96px 1fr; gap: 11px 14px; font-size: 13.5px; margin-bottom: 22px; }
.pa-snap dt { color: var(--text3); }
.pa-snap dd { min-width: 0; overflow-wrap: anywhere; }
.pa-mono { font-family: var(--font-mono); }
.pa-sect { font-size: 13px; font-weight: 600; margin-bottom: 10px; color: var(--text2); }

/* ---- 移动端（<768px）：任务卡片列表；PC 一条不动 ---- */
.pa-cards { display: none; }
@media (max-width: 767px) {
  .pa-toolbar { margin-bottom: 12px; }
  .pa-title { font-size: 16.5px; }
  /* 新增任务按钮宽出来，主操作好按 */
  .pa-right { width: 100%; }
  .pa-right .pa-btn { flex: 1; }

  .pa-cards { display: block; }
  .pa-carditem {
    padding: 12px 14px;
    border-bottom: 1px solid var(--split);
  }
  .pa-carditem:last-child { border-bottom: none; }
  .pa-c-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
  }
  .pa-c-name {
    flex: 1;
    min-width: 0;
    font-weight: 500;
    font-size: 14px;
    line-height: 1.45;
    word-break: break-all;
  }
  .pa-c-meta {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    margin-top: 8px;
  }
  .pa-c-row {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-top: 8px;
    min-width: 0;
  }
  .pa-c-row .pa-url { flex: 1; min-width: 0; max-width: none; color: var(--primary); text-decoration: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .pa-c-info { color: var(--text3); font-size: 12px; justify-content: space-between; flex-wrap: wrap; gap: 4px 10px; }
  .pa-c-ops {
    display: flex;
    gap: 6px;
    margin-top: 10px;
    flex-wrap: wrap;
  }
  .pa-c-ops .pa-op { flex: 1 1 auto; justify-content: center; height: 32px; }
  /* 次操作字链：低频动作收一行小字，别跟主操作挤成一堆按钮 */
  .pa-c-sub {
    display: flex;
    align-items: center;
    gap: 16px;
    margin-top: 8px;
    flex-wrap: wrap;
  }
  .pa-sublink {
    background: none;
    border: none;
    padding: 0;
    font-size: 12.5px;
    color: var(--text3);
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 3px;
  }
  .pa-sublink.danger { color: #cf1322; }
  .pa-c-empty { padding: 40px 16px; text-align: center; color: var(--text3); font-size: 13px; }
}
</style>
