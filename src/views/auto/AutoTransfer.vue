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

const route = useRoute()
const type = computed(() => (route.params.type as MainDriveType) || 'baidu')
const meta = computed(() => DRIVE_META[type.value])

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

    <!-- 任务表 -->
    <div class="pa-card">
      <table class="pa-table">
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
              <span class="pa-url" :title="t.share_url">{{ linkTrunc(t.share_url) }}</span>
              <span v-if="t.share_code" class="pa-code">{{ t.share_code }}</span>
              <button class="pa-copy" @click="onCopy(t)">复制</button>
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
    </div>

    <!-- 设计说明 -->
    <div class="note-box">
      <b>设计说明</b>
      <ul>
        <li>三个网盘菜单共用同一套任务模型和页面，只是按 type 过滤——baidu/quark/115 的差异只体现在 adapter 层，不在 UI 层。</li>
        <li>任务配置、对比路径、排除清单这些原本在 bdsavepro 里放在 config.json 的字段，新版全部迁到 SQLite，避免高频回写把配置覆盖掉。</li>
        <li>「上次执行」和「最近结果」来自 task_history 快照表，改任务配置不会影响旧记录。</li>
        <li>「排除」入口在任务行上（不在编辑弹窗里）：任务执行时会顺手缓存文件清单，点开排除弹窗直接命中缓存秒开。</li>
      </ul>
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
.pa-table { width: 100%; border-collapse: collapse; }
.pa-th {
  text-align: left;
  font-size: 13px;
  font-weight: 500;
  color: var(--text2);
  background: var(--surface-2);
  padding: 10px 12px;
  border-bottom: 1px solid var(--split);
  white-space: nowrap;
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
   执行=蓝 / 编辑=青 / 排除=橙（带计数徽标）/ 详情=中性 / 删除=红（Popconfirm 确认） ===== */
.pa-ops { display: flex; gap: 4px; justify-content: flex-end; }
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
</style>
