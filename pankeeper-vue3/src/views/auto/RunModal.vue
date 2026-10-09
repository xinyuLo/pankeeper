<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { message } from 'ant-design-vue';
import LogBox from '@/components/LogBox.vue';
import { DRIVE_META } from '@/api/mock/meta';
import { USE_MOCK, get } from '@/api/http';
import { buildPaRunSeq, finishPaRun, runPaTaskNow, type PaRunStep } from '@/api/modules/tasks';
import type { MainDriveType, PaTask, QueueLogLine, QueueState, QueueTask } from '@/types/model';
import { useBackGuard } from '@/composables/useBackGuard';
const props = defineProps<{
    open: boolean;
    task: PaTask | null;
}>();
const emit = defineEmits<{
    (e: 'update:open', v: boolean): void;
    (e: 'finished'): void;
}>();
useBackGuard(() => props.open, () => emit('update:open', false));
const meta = computed(() => (props.task ? DRIVE_META[props.task.type as MainDriveType] : DRIVE_META.baidu));
const stats = ref({ add: 0, skip: 0, fail: 0, excl: 0 });
const lines = ref<QueueLogLine[]>([]);
const hint = ref('正在执行，请稍候…');
const done = ref(false);
let pollTimer: number | null = null;
let notFoundTimer: number | null = null;
let seenTask = false;
const PHASE_TEXT: Record<string, string> = {
    '': '正在转存…',
    waitqms: '转存完成，等待触发 QMS 刮削…',
    qms: '触发 QMS 刮削…',
    waitstrm: 'QMS 已触发，等待生成 STRM…',
    strm: '生成 STRM…',
};
function syncFromQueue(t: QueueTask) {
    seenTask = true;
    if (notFoundTimer) {
        window.clearTimeout(notFoundTimer);
        notFoundTimer = null;
    }
    if (done.value)
        return;
    lines.value = t.logs || [];
    for (const l of t.logs) {
        let m = /转存完成：新增\s*(\d+)\s*\/\s*跳过\s*(\d+)\s*\/\s*失败\s*(\d+)/.exec(l.txt);
        if (m) {
            stats.value.add = Number(m[1]);
            stats.value.skip = Number(m[2]);
            stats.value.fail = Number(m[3]);
        }
        m = /排除清单：跳过\s*(\d+)\s*个文件/.exec(l.txt);
        if (m)
            stats.value.excl = Number(m[1]);
    }
    if (t.status === 'wait') {
        hint.value = '已入队，排队等待中…';
    }
    else if (t.status === 'run') {
        hint.value = PHASE_TEXT[t.phase || ''] || '正在执行，请稍候…';
    }
    else {
        done.value = true;
        if (t.status === 'done') {
            hint.value = '执行完成';
            message.success(`任务「${props.task?.name}」执行完成 · ${stats.value.add} 新增`);
        }
        else if (t.status === 'warn') {
            hint.value = '完成（分享可能已失效，详见日志）';
            message.warning(`任务「${props.task?.name}」完成但链接可能已失效`);
        }
        else {
            hint.value = '执行失败，详见日志';
            message.error(`任务「${props.task?.name}」执行失败`);
        }
        emit('finished');
    }
}
async function pollOnce() {
    if (done.value || !props.task)
        return;
    try {
        const s = await get<QueueState>('/queue/state');
        const t = (s.tasks || []).find((x) => x.paTaskId === props.task!.id);
        if (t)
            syncFromQueue(t);
    }
    catch {
    }
}
function errText(e: unknown): string {
    const resp = (e as {
        response?: {
            data?: {
                detail?: unknown;
            };
            status?: number;
        };
    })?.response;
    const detail = resp?.data?.detail;
    if (typeof detail === 'string' && detail.trim())
        return detail;
    if (resp)
        return `后端返回异常（HTTP ${resp.status ?? '?'}），详见服务端日志`;
    return '连不上后端（服务未启动或正在重启）';
}
async function startReal() {
    const t = props.task;
    if (!t)
        return;
    hint.value = '正在入队…';
    try {
        const r = await runPaTaskNow(t.id);
        if (!r.queued) {
            done.value = true;
            hint.value = r.reason || '未能入队';
            message.warning(r.reason || '任务未能入队');
            return;
        }
    }
    catch (e) {
        done.value = true;
        const why = errText(e);
        hint.value = `入队失败：${why}`;
        message.error(`入队失败：${why}`);
        return;
    }
    await pollOnce();
    pollTimer = window.setInterval(pollOnce, 1500);
    notFoundTimer = window.setTimeout(() => {
        if (!seenTask && !done.value) {
            done.value = true;
            hint.value = '未在队列中找到该任务（可能已被同链接去重）';
        }
    }, 10000);
}
const STEP_MS = 480;
let timer: number | null = null;
let idx = 0;
let seq: PaRunStep[] = [];
function stopMock() {
    if (timer) {
        clearInterval(timer);
        timer = null;
    }
}
async function finishMock() {
    const t = props.task;
    if (!t || done.value)
        return;
    done.value = true;
    hint.value = '已完成，可在转存记录查看详情';
    await finishPaRun(t.id, stats.value.add, stats.value.skip, stats.value.fail);
    message.success(`任务「${t.name}」执行完成 · ${t.last_result}`);
    emit('finished');
}
function stepMock() {
    if (idx >= seq.length) {
        stopMock();
        void finishMock();
        return;
    }
    const s = seq[idx++];
    const a = s.add;
    if (a) {
        if (a.add != null)
            stats.value.add = a.add;
        if (a.skip != null)
            stats.value.skip = a.skip;
        if (a.fail != null)
            stats.value.fail = a.fail;
        if (a.excl != null)
            stats.value.excl = a.excl;
    }
    const sec = Math.round((idx * STEP_MS) / 1000);
    const ts = `${String(Math.floor(sec / 60)).padStart(2, '0')}:${String(sec % 60).padStart(2, '0')}`;
    lines.value = [...lines.value, { lv: s.lv, txt: `${ts} ${s.txt}` }];
}
function startMock() {
    if (!props.task)
        return;
    seq = buildPaRunSeq(props.task);
    idx = 0;
    stats.value = { add: 0, skip: 0, fail: 0, excl: 0 };
    lines.value = [];
    hint.value = '正在执行，请稍候…';
    done.value = false;
    stopMock();
    timer = window.setInterval(stepMock, STEP_MS);
}
function start() {
    if (USE_MOCK)
        startMock();
    else
        void startReal();
}
function stopAll() {
    stopMock();
    if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
    }
    if (notFoundTimer) {
        window.clearTimeout(notFoundTimer);
        notFoundTimer = null;
    }
}
watch(() => props.open, (v) => {
    if (v) {
        stats.value = { add: 0, skip: 0, fail: 0, excl: 0 };
        lines.value = [];
        done.value = false;
        seenTask = false;
        start();
    }
    else {
        stopAll();
    }
});
function close() {
    stopAll();
    emit('update:open', false);
}
function onKey(e: KeyboardEvent) {
    if (e.key === 'Escape')
        close();
}
watch(() => props.open, (v) => {
    if (v)
        window.addEventListener('keydown', onKey);
    else
        window.removeEventListener('keydown', onKey);
});
onBeforeUnmount(() => {
    window.removeEventListener('keydown', onKey);
    stopAll();
});
</script>

<template>
  <teleport to="body">
    <div v-if="open" class="mt-mask" style="z-index: 1004" @click.self="close">
      <div class="mt-dialog" style="width: 640px">
        <div class="mt-dialog-head">
          <span class="mt-color-dot" :style="{ background: meta.color }"></span>
          <span class="mt-dialog-title">执行监控 · {{ task?.name || '任务' }}</span>
          <span class="mt-dialog-sub">{{ meta.full }}</span>
          <button class="mt-close" title="关闭" @click="close">×</button>
        </div>
        <div class="mt-dialog-body">
          
          <div class="mt-progress" :class="{ 'is-done': done }"></div>

          
          <div class="mt-stats">
            <div class="mt-stat-card mt-stat-add">
              <div class="mt-stat-num">{{ stats.add }}</div>
              <div class="mt-stat-label">新增</div>
            </div>
            <div class="mt-stat-card mt-stat-skip">
              <div class="mt-stat-num">{{ stats.skip }}</div>
              <div class="mt-stat-label">跳过</div>
            </div>
            <div class="mt-stat-card mt-stat-fail">
              <div class="mt-stat-num">{{ stats.fail }}</div>
              <div class="mt-stat-label">失败</div>
            </div>
            <div class="mt-stat-card mt-stat-excl">
              <div class="mt-stat-num">{{ stats.excl }}</div>
              <div class="mt-stat-label">已排除</div>
            </div>
          </div>

          <LogBox :lines="lines" follow-bottom class="mt-runlog" />
        </div>
        <div class="mt-dialog-foot">
          <span class="mt-run-foot" style="margin: 0">{{ hint }}</span>
          <button class="mt-btn mt-btn-primary" style="margin-left: auto" @click="close">关闭</button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<style scoped src="./mt-modal.css"></style>
