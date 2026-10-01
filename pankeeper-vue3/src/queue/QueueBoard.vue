<script setup lang="ts">
/* 转存队列看板（记录页「转存队列」分段）—— 原型 queue-core.js pqRow/renderPanel 的组件化移植。
 * 日志单选：同一时刻只展开一条 = 运行中的那条；没有运行中就是最新完成的。
 * pinnedLogId 存渲染外：每 600ms 一拍的重渲染不会把用户的展开选择打回去。 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import type { QueueTask } from '@/types/model'
import { DRIVE_META } from '@/api/mock/meta'
import { queueView, pkQueue, setOnNewTaskStart } from '@/queue/engine'

const pinnedLogId = ref<number | null>(null)
setOnNewTaskStart(() => {
  pinnedLogId.value = null
})

const tasks = computed(() => queueView.tasks)
const activeCount = computed(() => tasks.value.filter((t) => t.status === 'wait' || t.status === 'run').length)

defineExpose({ activeCount })

function metaOf(t: QueueTask) {
  return DRIVE_META[t.type] || { name: t.type, color: '#1677ff' }
}

function statusOf(t: QueueTask): { cls: string; txt: string } {
  if (t.status === 'wait') return { cls: 'pkq-st-wait', txt: '排队中' }
  if (t.status === 'done') return { cls: 'pkq-st-done', txt: '已完成' }
  if (t.status === 'warn') return { cls: 'pkq-st-warn', txt: '链接已失效' }
  if (t.status === 'fail') return { cls: 'pkq-st-fail', txt: '失败' }
  const m: Record<string, string> = {
    transfer: '转存中',
    waitqms: '转存完成 · 待 QMS',
    qms: 'QMS 刮削中',
    waitstrm: 'QMS 完成 · 待 STRM',
    strm: 'STRM 生成中',
  }
  return { cls: 'pkq-st-run', txt: m[t.phase || 'transfer'] || '转存中' }
}

// 焦点行 = 正在运行的；没有运行中就是最新完成的
const focusId = computed(() => {
  let latestRun = 0
  let latestDone = 0
  for (const t of tasks.value) {
    if (t.status === 'run' && t.id > latestRun) latestRun = t.id
    if ((t.status === 'done' || t.status === 'warn') && t.id > latestDone) latestDone = t.id
  }
  return latestRun || latestDone
})

function isOpen(t: QueueTask): boolean {
  return pinnedLogId.value !== null ? pinnedLogId.value === t.id : t.id === focusId.value
}

function toggleLog(t: QueueTask) {
  const openNow = isOpen(t)
  pinnedLogId.value = openNow ? null : t.id
}

const reversed = computed(() => tasks.value.slice().reverse())

// 转存中的任务日志跟随滚动到底
const logRefs = ref(new Map<number, HTMLElement>())
function setLogRef(t: QueueTask, el: any) {
  if (el) logRefs.value.set(t.id, el as HTMLElement)
  else logRefs.value.delete(t.id)
}
let timer = 0
onMounted(() => {
  timer = window.setInterval(() => {
    for (const t of tasks.value) {
      if (t.status !== 'run') continue
      const el = logRefs.value.get(t.id)
      if (el && el.style.display !== 'none') el.scrollTop = el.scrollHeight
    }
  }, 600)
})
onUnmounted(() => clearInterval(timer))

// 供父组件/其他页面读取当前排队数
pkQueue.onChange(() => {
  /* queueView 是响应式镜像，这里无需手动同步；保留订阅以维持引擎监听链 */
})
</script>

<template>
  <div>
    <div v-if="tasks.length === 0" class="pq-empty">队列是空的 · 去搜索页点「转存 / 快速转存」就会排进来</div>
    <div v-for="t in reversed" :key="t.id" class="pq-item" :data-pqid="t.id">
      <div class="pq-main">
        <div class="pq-name">
          <i :style="{ background: metaOf(t).color }"></i>
          <span>{{ t.name }}</span>
          <span class="pq-path">{{ t.path }} · {{ t.files }} 项 · {{ t.size }}</span>
          <span class="pq-st" :class="statusOf(t).cls">{{ statusOf(t).txt }}</span>
        </div>

        <div v-if="t.status === 'run'" class="progress pkq-bar"><i :style="{ width: t.progress + '%' }"></i></div>
        <div v-else-if="t.status === 'done' || t.status === 'warn'" class="pq-pct">进度 100%</div>
        <div v-else class="pq-pct">等待空闲线程</div>

        <div
          :ref="(el) => setLogRef(t, el)"
          class="pq-log logbox"
          :style="{ display: isOpen(t) ? 'block' : 'none' }"
        >
          <template v-if="t.logs.length">
            <div v-for="(l, i) in t.logs" :key="i">
              <span :class="'lv-' + l.lv">[{{ l.lv }}]</span> {{ l.txt }}
            </div>
          </template>
          <div v-else class="pq-logempty">还没开始，轮到它就有日志</div>
        </div>
      </div>
      <button class="ant-btn pq-logbtn" type="button" @click="toggleLog(t)">
        {{ t.logs.length ? '日志' : '—' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.pq-logbtn {
  height: 28px;
  padding: 0 12px;
  font-size: 13px;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: var(--card);
  color: var(--text);
  cursor: pointer;
}
.pq-logbtn:hover {
  border-color: var(--primary);
  color: var(--primary);
}
</style>
