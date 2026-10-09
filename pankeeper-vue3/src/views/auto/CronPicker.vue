<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { cronHuman } from '@/api/modules/tasks';
import { useBackGuard } from '@/composables/useBackGuard';
const props = defineProps<{
    open: boolean;
    cron: string;
}>();
const emit = defineEmits<{
    (e: 'update:open', v: boolean): void;
    (e: 'save', cron: string): void;
}>();
useBackGuard(() => props.open, () => emit('update:open', false));
type Mode = 'daily' | 'weekly' | 'hourly' | 'everyn' | 'custom';
const mode = ref<Mode>('daily');
const time = ref('03:00');
const weekdays = ref<number[]>([1]);
const everyN = ref<number>(30);
const custom = ref('');
const WEEK_OPTS = [
    { v: 1, label: '周一' },
    { v: 2, label: '周二' },
    { v: 3, label: '周三' },
    { v: 4, label: '周四' },
    { v: 5, label: '周五' },
    { v: 6, label: '周六' },
    { v: 0, label: '周日' },
];
const N_OPTS = [5, 10, 15, 20, 30].map((v) => ({ value: v, label: `${v} 分钟` }));
function pad(v: string | number) {
    return String(v).padStart(2, '0');
}
const timeCron = computed(() => {
    const [mm = '0', hh = '3'] = (time.value || '').split(':');
    return `0 ${pad(mm)} ${pad(hh)}`;
});
const expr = computed(() => {
    if (mode.value === 'daily')
        return timeCron.value + ' * * *';
    if (mode.value === 'weekly') {
        const days = [...weekdays.value].sort((a, b) => a - b);
        if (!days.length)
            return '';
        return timeCron.value + ' * * ' + days.join(',');
    }
    if (mode.value === 'hourly')
        return '0 * * * *';
    if (mode.value === 'everyn')
        return `*/${everyN.value} * * * *`;
    return custom.value.trim();
});
const exprHuman = computed(() => (expr.value ? cronHuman(expr.value, '') : ''));
function parseCron(cron: string) {
    custom.value = cron || '';
    const p = (cron || '').trim().split(/\s+/);
    if (p.length !== 5) {
        mode.value = 'custom';
        return;
    }
    const [m, h, dom, mon, dow] = p;
    if (dom === '*' && mon === '*' && dow === '*') {
        if (m === '0' && h === '*') {
            mode.value = 'hourly';
            return;
        }
        if (m.startsWith('*/') && h === '*' && /^\d+$/.test(m.slice(2))) {
            mode.value = 'everyn';
            everyN.value = Math.min(59, Math.max(1, parseInt(m.slice(2), 10)));
            return;
        }
        if (/^\d+$/.test(m) && /^\d+$/.test(h)) {
            mode.value = 'daily';
            time.value = `${pad(h)}:${pad(m)}`;
            return;
        }
    }
    if (dom === '*' && mon === '*' && /^\d+(,\d+)*$/.test(dow) && /^\d+$/.test(m) && /^\d+$/.test(h)) {
        mode.value = 'weekly';
        weekdays.value = dow.split(',').map(Number);
        time.value = `${pad(h)}:${pad(m)}`;
        return;
    }
    mode.value = 'custom';
}
watch(() => props.open, (v) => {
    if (v)
        parseCron(props.cron);
});
function ok() {
    if (!expr.value)
        return;
    emit('save', expr.value);
    emit('update:open', false);
}
function close() {
    emit('update:open', false);
}
</script>

<template>
  <a-modal :open="open" :width="440" title="选择定时策略" ok-text="确定" cancel-text="取消" @ok="ok" @cancel="close">
    <a-tabs v-model:activeKey="mode" size="small">
      <a-tab-pane key="daily" tab="每天">
        <div class="cp-row">
          <span class="cp-lab">执行时间</span>
          <a-time-picker v-model:value="time" value-format="HH:mm" format="HH:mm" :allow-clear="false" style="width: 120px" />
        </div>
      </a-tab-pane>

      <a-tab-pane key="weekly" tab="每周">
        <div class="cp-row">
          <span class="cp-lab">星期</span>
          <a-checkbox-group v-model:value="weekdays" :options="WEEK_OPTS.map((w) => ({ label: w.label, value: w.v }))" />
        </div>
        <div class="cp-row">
          <span class="cp-lab">执行时间</span>
          <a-time-picker v-model:value="time" value-format="HH:mm" format="HH:mm" :allow-clear="false" style="width: 120px" />
        </div>
      </a-tab-pane>

      <a-tab-pane key="hourly" tab="每小时">
        <div class="cp-hintline">每小时整点执行一次（0 * * * *）。</div>
      </a-tab-pane>

      <a-tab-pane key="everyn" tab="每 N 分钟">
        <div class="cp-row">
          <span class="cp-lab">间隔</span>
          <a-select v-model:value="everyN" :options="N_OPTS" style="width: 120px" />
        </div>
      </a-tab-pane>

      <a-tab-pane key="custom" tab="自定义">
        <a-input v-model:value="custom" placeholder="分 时 日 月 周，如 0 3 * * *" />
        <div class="cp-hintline">标准 5 位 cron（分 时 日 月 周）。</div>
      </a-tab-pane>
    </a-tabs>

    <div class="cp-preview">
      <template v-if="expr">
        <div class="cp-preview-cron">{{ expr }}</div>
        <div class="cp-preview-human">{{ exprHuman || '（自定义表达式）' }}</div>
      </template>
      <div v-else class="cp-preview-human cp-preview-empty">先选一个模式（每周模式需勾选星期）</div>
    </div>
  </a-modal>
</template>

<style scoped>
.cp-row { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
.cp-lab { color: var(--text2); font-size: 13px; flex: 0 0 60px; }
.cp-hintline { color: var(--text3); font-size: 12.5px; line-height: 1.7; }
.cp-preview {
  margin-top: 14px;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--surface-2);
}
.cp-preview-cron { font-family: var(--font-mono); font-size: 13px; color: var(--primary); font-weight: 600; }
.cp-preview-human { font-size: 12.5px; color: var(--text3); margin-top: 3px; }
.cp-preview-empty { color: var(--text4); }
</style>
