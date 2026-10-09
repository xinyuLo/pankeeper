<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useIsMobile } from '@/composables/useIsMobile';
const props = withDefaults(defineProps<{
    total: number;
    current: number;
    pageSize: number;
    pageSizes?: number[];
}>(), { pageSizes: () => [8, 20, 50] });
const emit = defineEmits<{
    (e: 'update:current', v: number): void;
    (e: 'update:pageSize', v: number): void;
}>();
const isMobile = useIsMobile();
const jumpInput = ref('');
const totalPages = computed(() => Math.max(1, Math.ceil(props.total / props.pageSize)));
const pages = computed<(number | 'gap')[]>(() => {
    const tp = totalPages.value;
    const cur = props.current;
    if (tp <= 9)
        return Array.from({ length: tp }, (_, i) => i + 1);
    const set = new Set<number>([1, tp, cur - 2, cur - 1, cur, cur + 1, cur + 2]);
    const list = [...set].filter((p) => p >= 1 && p <= tp).sort((a, b) => a - b);
    const out: (number | 'gap')[] = [];
    let prev = 0;
    for (const p of list) {
        if (p - prev > 1)
            out.push('gap');
        out.push(p);
        prev = p;
    }
    return out;
});
watch(() => props.pageSize, () => emit('update:current', 1));
function goPage(p: number) {
    const target = Math.min(totalPages.value, Math.max(1, p));
    emit('update:current', target);
}
function doJump() {
    const n = parseInt(jumpInput.value, 10);
    if (!isNaN(n))
        goPage(n);
    jumpInput.value = '';
}
function fmtRange(): string {
    if (props.total === 0)
        return '第 0 条';
    const start = (props.current - 1) * props.pageSize + 1;
    const end = Math.min(props.total, props.current * props.pageSize);
    return `第 ${start}–${end} 条`;
}
</script>

<template>
  <div class="pager" v-if="total > 0">
    
    <template v-if="isMobile">
      <div class="pg-nav">
        <button class="pg-arrow" :disabled="current <= 1" aria-label="上一页" @click="goPage(current - 1)">‹</button>
        <span class="pg-indicator">{{ current }}<i>/</i>{{ totalPages }}</span>
        <button class="pg-arrow" :disabled="current >= totalPages" aria-label="下一页" @click="goPage(current + 1)">›</button>
      </div>
      <div class="pg-meta">
        <span class="pg-total">共 {{ total }} 条</span>
        <label class="pg-size">
          <select :value="pageSize" @change="emit('update:pageSize', parseInt(($event.target as HTMLSelectElement).value, 10))">
            <option v-for="s in pageSizes" :key="s" :value="s">{{ s }} 条/页</option>
          </select>
        </label>
      </div>
    </template>

    
    <template v-else>
      <span class="pg-total">共 <b>{{ total }}</b> 条 · {{ fmtRange() }}</span>

      <div class="pg-list">
        <button class="pg-arrow" :disabled="current <= 1" aria-label="上一页" @click="goPage(current - 1)">‹</button>
        <template v-for="(p, i) in pages" :key="i">
          <span v-if="p === 'gap'" class="pg-gap">…</span>
          <button v-else class="pg-it" :class="{ on: p === current }" @click="goPage(p)">{{ p }}</button>
        </template>
        <button class="pg-arrow" :disabled="current >= totalPages" aria-label="下一页" @click="goPage(current + 1)">›</button>
      </div>

      <div class="pg-size">
        每页
        <select :value="pageSize" @change="emit('update:pageSize', parseInt(($event.target as HTMLSelectElement).value, 10))">
          <option v-for="s in pageSizes" :key="s" :value="s">{{ s }}</option>
        </select>
      </div>
      <div class="pg-jump">
        跳至 <input v-model="jumpInput" @keydown.enter="doJump" /> 页
      </div>
    </template>
  </div>
</template>

<style scoped>
.pager {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 12px 16px;
  border-top: 1px solid var(--split);
  font-size: 13px;
  color: var(--text2);
  background: var(--card);
}

/* 页码 / 箭头共用的幽灵按钮：无边框，悬浮浅底，避免一排描边盒子的廉价感 */
.pg-it,
.pg-arrow {
  min-width: 30px;
  height: 30px;
  padding: 0 6px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text2);
  font-size: 13px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
  font-family: inherit;
}
.pg-arrow {
  font-size: 17px;
  line-height: 1;
  padding: 0 4px;
  color: var(--text3);
}
.pg-it:hover:not(:disabled):not(.on),
.pg-arrow:hover:not(:disabled) {
  background: var(--surface-3);
  color: var(--primary-h);
}
.pg-it.on {
  background: var(--primary);
  color: #fff;
  font-weight: 600;
  box-shadow: 0 2px 6px rgb(22 119 255 / 0.28);
}
.pg-it:disabled,
.pg-arrow:disabled {
  color: var(--text4);
  cursor: not-allowed;
}
.pg-gap {
  min-width: 22px;
  text-align: center;
  color: var(--text4);
  user-select: none;
}

.pager .pg-total {
  flex: 0 0 auto;
  color: var(--text3);
  font-size: 12.5px;
  white-space: nowrap;
}
.pager .pg-total b {
  color: var(--text);
  font-weight: 600;
}
.pg-list {
  display: flex;
  align-items: center;
  gap: 2px;
  margin-left: auto; /* 页码靠右：总数在左，页码+每页+跳页一串贴右缘 */
}
.pager .pg-size {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: 14px;
  color: var(--text3);
  font-size: 12.5px;
}
.pager .pg-jump {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--text3);
  font-size: 12.5px;
}
.pager .pg-jump input {
  width: 48px;
  height: 30px;
  text-align: center;
  font-size: 13px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--card);
  color: var(--text);
  padding: 0 4px;
  outline: none;
  transition: border-color 0.15s;
}
.pager .pg-jump input:focus {
  border-color: var(--primary);
}
.pager select {
  height: 28px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--card);
  color: var(--text2);
  font-size: 12.5px;
  padding: 0 4px;
  outline: none;
  cursor: pointer;
}

/* ---- 手机（<768px）：一行式分页 ---- */
@media (max-width: 767px) {
  .pager {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    padding: 10px 14px calc(10px + var(--sab));
  }
  .pg-nav {
    display: flex;
    align-items: center;
    gap: 2px;
  }
  .pg-arrow {
    width: 36px;
    height: 36px;
    min-width: 36px;
    border-radius: 10px;
    font-size: 20px;
  }
  .pg-arrow:active:not(:disabled) {
    background: var(--surface-3);
  }
  .pg-indicator {
    min-width: 52px;
    text-align: center;
    font-size: 13.5px;
    font-weight: 600;
    color: var(--text);
    font-variant-numeric: tabular-nums;
  }
  .pg-indicator i {
    font-style: normal;
    color: var(--text4);
    margin: 0 3px;
    font-weight: 400;
  }
  .pg-meta {
    display: flex;
    align-items: center;
    gap: 12px;
    min-width: 0;
  }
  .pager .pg-total {
    font-size: 12px;
  }
  .pager .pg-size {
    margin-left: 0;
  }
  .pager select {
    height: 32px;
    max-width: 96px;
  }
}
</style>
