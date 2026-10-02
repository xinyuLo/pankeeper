<script setup lang="ts">
/* 自动转存页（/auto/:type，百度/夸克/115 三路由共用一个组件）—— 原型 parts/page-auto.html 移植。
 * 工具条 + 任务表 8 列 + 五色行操作 + 设计说明；四个弹窗拆成独立组件：
 * TaskModal（任务配置，含叠加的 DirModal）/ RunModal（执行监控）/ ExclModal（排除清单）。
 * 互斥规则：开执行监控关掉其余弹窗；Esc 逐层关。排除入口在任务行（不在编辑弹窗里）。 */
import { computed, onMounted, ref, watch } from 'vue'
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
  return url.length > 30 ? url.slice(0, 30) + '…' : url
}
function cronText(cron: string): string {
  return cronHuman(cron, '仅手动')
}
const STATUS_TEXT: Record<PaTask['last_status'], string> = {
  success: '成功',
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

function onCheckAll() {
  message.info(`已开始检查「${meta.value.full}」全部任务`)
}

/* ===== 任务弹窗（新增/编辑） ===== */
const taskOpen = ref(false)
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
      <table v-if="!isMobile" class="pa-table">
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
            <td class="pa-td">{{ cronText(t.cron) }}</td>
            <td class="pa-td pa-muted">{{ t.exclude_count ? t.exclude_count + ' 项' : '—' }}</td>
            <td class="pa-td pa-muted">{{ t.last_run || '—' }}</td>
            <td class="pa-td">
              <span class="pa-tag" :class="'pa-st-' + t.last_status">
                {{ STATUS_TEXT[t.last_status] }}<template v-if="t.last_result"> · {{ t.last_result }}</template>
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
      <PkPager v-if="!isMobile" v-model:current="page" v-model:pageSize="size" :total="tasks.length" />

      <!-- 手机端：一任务一卡（名称+开关 / 状态+定时 / 链接+复制 / 执行信息 / 五动作铺开） -->
      <div v-else class="pa-cards">
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
              {{ STATUS_TEXT[t.last_status] }}<template v-if="t.last_result"> · {{ t.last_result }}</template>
            </span>
            <span class="small muted">{{ cronText(t.cron) }}</span>
          </div>
          <div class="pa-c-row">
            <span v-if="t.share_code" class="pa-code">{{ t.share_code }}</span>
            <div class="pa-linkops">
              <button class="pa-op" @click="onViewFiles(t)">查看</button>
              <button class="pa-op" @click="onJump(t)">跳转</button>
              <button class="pa-op" @click="onCopy(t)">复制</button>
            </div>
          </div>
          <div class="pa-c-row pa-c-info">
            <span>上次执行 {{ t.last_run || '—' }}</span>
            <span>已排除 {{ t.exclude_count ? t.exclude_count + ' 项' : '—' }}</span>
          </div>
          <div class="pa-c-ops">
            <button class="pa-op pa-op-run" @click="openRun(t)">执行</button>
            <button class="pa-op pa-op-edit" @click="openEdit(t)">编辑</button>
            <button class="pa-op pa-op-excl" @click="openExcl(t)">
              排除<i v-if="t.exclude_count" class="pa-op-num">{{ t.exclude_count }}</i>
            </button>
            <button class="pa-op pa-op-detail" @click="openDetail(t)">详情</button>
            <a-popconfirm
              :title="`确认删除任务「${t.name}」？此操作不可恢复。`"
              ok-text="删除"
              cancel-text="取消"
              :ok-button-props="{ danger: true }"
              @confirm="onDel(t)"
            >
              <button class="pa-op pa-op-del">删除</button>
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
.pa-table { width: 100%; border-collapse: collapse; table-layout: fixed; }
/* 列宽配比：任务名/分享链接双主力，右列全收窄（操作列图标化后 150px 够） */
/* 列宽配比：任务名/分享链接双主力（链接列含 URL+提取码+三钮），右列全收窄 */
.pa-table th:nth-child(1) { width: 14%; }
.pa-table th:nth-child(2) { width: 56px; }
.pa-table th:nth-child(3) { width: auto; }
.pa-table th:nth-child(4) { width: 96px; }
.pa-table th:nth-child(5) { width: 60px; }
.pa-table th:nth-child(6) { width: 84px; }
.pa-table th:nth-child(7) { width: 124px; }
.pa-table th:nth-child(8) { width: 140px; }
.pa-table .pa-td { overflow: hidden; }
.pa-name { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* 任务名 + 三个链接小钮同格：名称截断，图标靠右 */
.pa-namecell { display: flex; align-items: center; gap: 8px; min-width: 0; }
.pa-namecell .pa-name { flex: 1 1 auto; min-width: 0; }
.pa-th {
  text-align: left;
  font-size: 12.5px;
  font-weight: 500;
  color: var(--text3);
  background: var(--surface-2);
  padding: 11px 14px;
  letter-spacing: 0.02em;
  white-space: nowrap;
  border-bottom: 1px solid var(--split);
}
.pa-th-ops { text-align: right; }
.pa-td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--split);
  font-size: 13.5px;
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
.pa-link { display: flex; align-items: center; gap: 6px; }
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

/* 最近结果染色 tag：success 绿 / fail 红 / running 蓝 / never 灰 */
.pa-tag { font-size: 12.5px; padding: 2px 8px; border-radius: 6px; display: inline-block; white-space: nowrap; }
.pa-st-success { color: #52c41a; background: rgba(82, 196, 26, 0.12); }
.pa-st-fail { color: #ff4d4f; background: rgba(255, 77, 79, 0.12); }
.pa-st-running { color: #1677ff; background: rgba(22, 119, 255, 0.12); }
.pa-st-never { color: var(--text3); background: rgba(0, 0, 0, 0.05); }
html[data-theme='dark'] .pa-st-success { color: #95de64; background: rgba(82, 196, 26, 0.16); }
html[data-theme='dark'] .pa-st-fail { color: #ff9c9c; background: rgba(255, 77, 79, 0.16); }
html[data-theme='dark'] .pa-st-running { color: #69b1ff; background: rgba(22, 119, 255, 0.2); }
html[data-theme='dark'] .pa-st-never { color: var(--text3); background: rgba(255, 255, 255, 0.06); }

/* ===== 行操作五色按钮（颜色即语义）：
   执行=蓝 / 编辑=青 / 排除=橙（带计数徽标）/ 详情=中性 / 删除=红（Popconfirm 确认）
   桌面用 .pa-ico 图标钮（26px 方块，悬浮出 tooltip + 染色底），手机卡片仍用文字 .pa-op ===== */
.pa-ops { display: flex; gap: 4px; justify-content: flex-end; }
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
.pa-ico.pa-op-del { color: #ff4d4f; }
.pa-ico.pa-op-del:hover { background: #fff1f0; }
/* 暗色：底色压暗、语义色提亮一档 */
html[data-theme='dark'] .pa-ico:hover { background: rgba(255, 255, 255, 0.08); }
html[data-theme='dark'] .pa-ico.pa-op-run:hover { background: #111a2c; }
html[data-theme='dark'] .pa-ico.pa-op-edit:hover { background: #0e2929; }
html[data-theme='dark'] .pa-ico.pa-op-excl:hover { background: #2b2111; }
html[data-theme='dark'] .pa-ico.pa-op-detail:hover { background: rgba(255, 255, 255, 0.1); color: var(--text); }
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
  .pa-c-row .pa-url { flex: 1; min-width: 0; max-width: none; }
  .pa-c-info { color: var(--text3); font-size: 12px; justify-content: space-between; flex-wrap: wrap; gap: 4px 10px; }
  .pa-c-ops {
    display: flex;
    gap: 6px;
    margin-top: 10px;
    flex-wrap: wrap;
  }
  .pa-c-ops .pa-op { flex: 1 1 auto; justify-content: center; height: 32px; }
  .pa-c-empty { padding: 40px 16px; text-align: center; color: var(--text3); font-size: 13px; }
}
</style>
