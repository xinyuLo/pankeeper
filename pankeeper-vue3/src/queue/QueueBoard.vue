<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import type { QueueTask } from '@/types/model';
import { DRIVE_META } from '@/api/mock/meta';
import { queueView, pkQueue, setOnNewTaskStart, isAutoQueued } from '@/queue/engine';
const tasks = computed(() => queueView.tasks.filter((t) => !isAutoQueued(t)));
const activeCount = computed(() => tasks.value.filter((t) => t.status === 'wait' || t.status === 'run').length);
defineExpose({ activeCount });
function metaOf(t: QueueTask) {
    return DRIVE_META[t.type] || { name: t.type, color: '#1677ff' };
}
function statusOf(t: QueueTask): {
    cls: string;
    txt: string;
} {
    if (t.status === 'wait')
        return { cls: 'pkq-st-wait', txt: '排队中' };
    if (t.status === 'done')
        return { cls: 'pkq-st-done', txt: '已完成' };
    if (t.status === 'warn')
        return { cls: 'pkq-st-warn', txt: '链接已失效' };
    if (t.status === 'fail')
        return { cls: 'pkq-st-fail', txt: '失败' };
    const m: Record<string, string> = {
        transfer: '转存中',
        waitqms: '转存完成 · 待 QMS',
        qms: 'QMS 刮削中',
        waitstrm: 'QMS 完成 · 待 STRM',
        strm: 'STRM 生成中',
    };
    return { cls: 'pkq-st-run', txt: m[t.phase || 'transfer'] || '转存中' };
}
const focusId = computed(() => {
    let latestRun = 0;
    let latestDone = 0;
    for (const t of tasks.value) {
        if (t.status === 'run' && t.id > latestRun)
            latestRun = t.id;
        if (['done', 'warn', 'fail'].includes(t.status) && t.id > latestDone)
            latestDone = t.id;
    }
    return latestRun || latestDone;
});
const pinnedId = ref<number | null>(null);
const COLLAPSED = -1;
setOnNewTaskStart(() => {
    pinnedId.value = null;
});
const isOpen = (t: QueueTask) => (pinnedId.value === COLLAPSED ? false : pinnedId.value !== null ? pinnedId.value === t.id : t.id === focusId.value);
function toggleDetail(t: QueueTask) {
    pinnedId.value = isOpen(t) ? COLLAPSED : t.id;
}
const detailText = (t: QueueTask): string => (isOpen(t) ? '收起' : '详情');
const reversed = computed(() => tasks.value.slice().reverse());
const logRefs = ref(new Map<number, HTMLElement>());
function setLogRef(t: QueueTask, el: any) {
    if (el)
        logRefs.value.set(t.id, el as HTMLElement);
    else
        logRefs.value.delete(t.id);
}
let timer = 0;
onMounted(() => {
    timer = window.setInterval(() => {
        for (const t of tasks.value) {
            if (t.status !== 'run')
                continue;
            const el = logRefs.value.get(t.id);
            if (el && el.style.display !== 'none')
                el.scrollTop = el.scrollHeight;
        }
    }, 600);
});
onUnmounted(() => clearInterval(timer));
pkQueue.onChange(() => {
});
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

        <div v-if="t.status === 'run'" class="progress pkq-bar pkq-loading"><i></i></div>
        <div v-else-if="t.status === 'done' || t.status === 'warn'" class="pq-pct">进度 100%</div>
        <div v-else-if="t.status === 'fail'" class="pq-pct pq-fail">转存失败</div>
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
      <div class="pq-ops">
        <button v-if="t.logs.length" class="pq-detail" :class="{ pinned: isOpen(t) }" @click="toggleDetail(t)">
          {{ detailText(t) }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 运行中 = 不定量流动 loading 条（转存快时百分比条会"嗖一下"结束，观感差） */
.pkq-loading i {
  width: 40%;
  transition: none;
  background: linear-gradient(90deg, rgba(22, 119, 255, 0), #1677ff 30%, #69c0ff 70%, rgba(105, 192, 255, 0));
  animation: pkqFlow 1.4s ease-in-out infinite;
}
@keyframes pkqFlow {
  0% { margin-left: -40%; }
  100% { margin-left: 100%; }
}
html[data-theme='dark'] .pkq-loading i {
  background: linear-gradient(90deg, rgba(105, 192, 255, 0), #69c0ff 30%, #91caff 70%, rgba(145, 202, 255, 0));
}

/* 行内右侧：详情/收起（未展开的行给入口看日志；展开的行可收回自动跟随） */
.pq-ops { display: flex; align-items: flex-start; }
.pq-detail {
  height: 24px;
  padding: 0 10px;
  font-size: 12px;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: var(--card);
  color: var(--text2);
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}
.pq-detail:hover { border-color: var(--primary); color: var(--primary); }
.pq-detail.pinned { border-color: var(--primary); color: var(--primary); }
/* 失败态的进度文案 */
.pq-fail { color: var(--error); }
</style>
