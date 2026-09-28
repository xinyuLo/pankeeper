<script setup lang="ts">
/* 自动转存页（/auto/:type，百度/夸克/115 三路由共用一个组件）—— 原型 parts/page-auto.html 移植。
 * 工具条 + 任务表 8 列 + 五色行操作 + 设计说明；四个弹窗拆成独立组件：
 * TaskModal（任务配置，含叠加的 DirModal）/ RunModal（执行监控）/ ExclModal（排除清单）。
 * 互斥规则：开执行监控关掉其余弹窗；Esc 逐层关。排除入口在任务行（不在编辑弹窗里）。 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { message } from 'ant-design-vue'
import LogBox from '@/components/LogBox.vue'
import TaskModal from './TaskModal.vue'
import RunModal from './RunModal.vue'
import ExclModal from './ExclModal.vue'
import { useIsMobile } from '@/composables/useIsMobile'
import { DRIVE_META } from '@/api/mock/meta'
import {
  cronHuman,
  deletePaTask,
  getPaDetailLog,
  listPaTasks,
  primeExclCache,
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

/* ===== 执行（paRun）：先就地预热排除清单缓存，再开监控（互斥：关掉其余弹窗） ===== */
const runOpen = ref(false)
const runTask = ref<PaTask | null>(null)
function openRun(t: PaTask) {
  primeExclCache(t) // 原型 mtPrimeExclCache：排除弹窗随后打开即命中
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
            <th class="pa-th">任务名</th>
            <th class="pa-th">启用</th>
            <th class="pa-th">分享链接</th>
            <th class="pa-th">定时策略</th>
            <th class="pa-th">已排除</th>
            <th class="pa-th">上次执行</th>
            <th class="pa-th">最近结果</th>
            <th class="pa-th pa-th-ops">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in tasks" :key="t.id" class="pa-row" :style="{ borderLeft: '3px solid ' + meta.color }">
            <td class="pa-td pa-name">{{ t.name }}</td>
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
              <span v-if="t.share_code" class="pa-code">{{ t.share_code }}</span>
              <div class="pa-linkops">
                <button class="pa-op" @click="onViewFiles(t)">查看</button>
                <button class="pa-op" @click="onJump(t)">跳转</button>
                <button class="pa-op" @click="onCopy(t)">复制</button>
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
            </td>
          </tr>
          <tr v-if="!tasks.length">
            <td class="pa-td pa-muted" colspan="8" style="text-align: center; padding: 28px">当前网盘暂无自动转存任务</td>
          </tr>
        </tbody>
      </table>

      <!-- 手机端：一任务一卡（名称+开关 / 状态+定时 / 链接+复制 / 执行信息 / 五动作铺开） -->
      <div v-else class="pa-cards">
        <div v-for="t in tasks" :key="t.id" class="pa-carditem" :style="{ borderLeft: '3px solid ' + meta.color }">
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
