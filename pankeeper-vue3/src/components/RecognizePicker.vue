<script setup lang="ts">
import { computed } from 'vue';
import { message } from 'ant-design-vue';
import type { RecognizeCandidate } from '@/api/modules/recognize';
import { useBackGuard } from '@/composables/useBackGuard';
const props = defineProps<{
    open: boolean;
    candidates: RecognizeCandidate[];
    sourceName?: string;
}>();
const emit = defineEmits<{
    (e: 'update:open', v: boolean): void;
    (e: 'pick', c: RecognizeCandidate): void;
}>();
useBackGuard(() => props.open, () => emit('update:open', false));
const TYPE_TXT: Record<string, string> = { movie: '电影', tv: '剧集' };
function pick(c: RecognizeCandidate) {
    const name = c.year ? `${c.title} (${c.year})` : c.title;
    emit('pick', c);
    emit('update:open', false);
    message.success(`已选择：${name}`);
}
function fullName(c: RecognizeCandidate) {
    return c.year ? `${c.title} (${c.year})` : c.title;
}
const hint = computed(() => props.sourceName ? `识别源「${props.sourceName}」有多个匹配结果，请选择实际要保存的条目` : '有多个匹配结果，请选择实际要保存的条目');
</script>

<template>
  <a-modal
    :open="open"
    title="选择识别结果"
    :width="520"
    :footer="null"
    @cancel="emit('update:open', false)"
  >
    <div class="rp-hint">{{ hint }}</div>
    <div class="rp-list">
      <div v-for="c in candidates" :key="`${c.media_type}-${c.tmdb_id}`" class="rp-card" @click="pick(c)">
        <img v-if="c.poster" :src="c.poster" class="rp-poster" loading="lazy" />
        <div v-else class="rp-poster rp-nopic">无图</div>
        <div class="rp-info">
          <div class="rp-title">
            <span>{{ c.title }}</span>
            <span class="tag" :class="c.media_type === 'tv' ? 't-uc' : 't-ok'">{{ TYPE_TXT[c.media_type] || c.media_type }}</span>
          </div>
          <div class="rp-sub small muted">
            {{ c.year || '年份未知' }}<template v-if="c.original_title && c.original_title !== c.title"> · {{ c.original_title }}</template>
          </div>
          <div v-if="c.overview" class="rp-overview small muted">{{ c.overview }}</div>
          <div class="rp-fill small">回填：{{ fullName(c) }}</div>
        </div>
      </div>
      <div v-if="!candidates.length" class="pq-empty">没有候选结果</div>
    </div>
  </a-modal>
</template>

<style scoped>
.rp-hint { font-size: 12.5px; color: var(--text3); margin-bottom: 12px; }
.rp-list { display: flex; flex-direction: column; gap: 10px; max-height: 60vh; overflow: auto; }
.rp-card {
  display: flex;
  gap: 12px;
  padding: 10px;
  border: 1px solid var(--split);
  border-radius: var(--r-sm);
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}
.rp-card:hover { border-color: var(--primary); background: var(--surface-2); }
.rp-poster {
  width: 60px;
  height: 88px;
  border-radius: 6px;
  object-fit: cover;
  flex: none;
  background: var(--surface-2);
}
.rp-nopic { display: flex; align-items: center; justify-content: center; font-size: 11px; color: var(--text3); }
.rp-info { flex: 1; min-width: 0; }
.rp-title { display: flex; align-items: center; gap: 8px; font-size: 13.5px; font-weight: 500; }
.rp-sub { margin-top: 2px; }
.rp-overview {
  margin-top: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.rp-fill { margin-top: 4px; color: var(--primary); }
</style>
