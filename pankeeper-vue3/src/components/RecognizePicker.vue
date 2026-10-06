<script setup lang="ts">
/* 识别候选选择弹窗（转存弹窗「识别」按钮的歧义解法，2026-10-06）：
 * 名字有歧义时（同名剧/电影、别名）不再让算法赌热度，候选卡片按可信度排序，
 * 用户点哪张回填哪个「标题 (年份)」。两个转存弹窗共用这一份。
 * 视觉：深色科技面板（自带配色体系，明暗主题下都成立）——玻璃拟态卡片、
 * 品牌渐变描边发光、等宽字体点缀、海报序号徽标（2026-10-06 用户要求科技感）。 */
import { computed } from 'vue'
import { message } from 'ant-design-vue'
import { SearchOutlined } from '@ant-design/icons-vue'
import type { RecognizeCandidate } from '@/api/modules/recognize'

const props = defineProps<{
  open: boolean
  candidates: RecognizeCandidate[]
  /** 识别源名字（弹窗标题里显示上下文） */
  sourceName?: string
}>()
const emit = defineEmits<{ (e: 'update:open', v: boolean): void; (e: 'pick', c: RecognizeCandidate): void }>()

const TYPE_TXT: Record<string, string> = { movie: 'MOVIE', tv: 'TV SHOW' }

function fullName(c: RecognizeCandidate) {
  return c.year ? `${c.title} (${c.year})` : c.title
}
function pick(c: RecognizeCandidate) {
  emit('pick', c)
  emit('update:open', false)
  message.success(`已选择：${fullName(c)}`)
}
const hint = computed(() => props.sourceName ? `识别源「${props.sourceName}」匹配到多条条目，选择实际要保存的` : '匹配到多条条目，选择实际要保存的')
</script>

<template>
  <a-modal
    :open="open"
    wrap-class-name="rp-tech-modal"
    :footer="null"
    :width="540"
    @cancel="emit('update:open', false)"
  >
    <template #title>
      <div class="rp-title">
        <span class="rp-title-bar"></span>
        <span class="rp-title-text">AI MATCH · 识别候选</span>
        <span class="rp-title-sub">// 选择实际要保存的条目</span>
      </div>
    </template>

    <div class="rp-hint">
      <span class="rp-hint-label">SOURCE</span>
      <span class="rp-hint-val">{{ sourceName || '未命名资源' }}</span>
    </div>

    <div class="rp-list">
      <div v-for="(c, i) in candidates" :key="`${c.media_type}-${c.tmdb_id}`" class="rp-card" @click="pick(c)">
        <span class="rp-rank">#{{ String(i + 1).padStart(2, '0') }}</span>
        <div class="rp-poster-wrap">
          <img v-if="c.poster" :src="c.poster" class="rp-poster" loading="lazy" />
          <div v-else class="rp-poster rp-nopic"><SearchOutlined /></div>
        </div>
        <div class="rp-info">
          <div class="rp-line1">
            <span class="rp-name">{{ c.title }}</span>
            <span class="rp-type" :class="c.media_type === 'tv' ? 'is-tv' : 'is-movie'">{{ TYPE_TXT[c.media_type] || c.media_type }}</span>
          </div>
          <div class="rp-meta">
            <span class="rp-year">{{ c.year || '----' }}</span>
            <span v-if="c.original_title && c.original_title !== c.title" class="rp-orig">{{ c.original_title }}</span>
          </div>
          <div v-if="c.overview" class="rp-overview">{{ c.overview }}</div>
          <div class="rp-fill"><span class="rp-fill-arrow">-&gt;</span> {{ fullName(c) }}</div>
        </div>
      </div>
      <div v-if="!candidates.length" class="rp-empty">NO MATCH · 没有候选结果</div>
    </div>
  </a-modal>
</template>

<style scoped>
/* ===== 识别源条 ===== */
.rp-hint {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
  padding: 8px 12px;
  border: 1px solid rgba(110, 140, 255, 0.22);
  border-radius: 8px;
  background: rgba(80, 110, 255, 0.08);
}
.rp-hint-label {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 10px;
  letter-spacing: 2px;
  color: #6e8cff;
}
.rp-hint-val { font-size: 12.5px; color: #dfe6f5; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* ===== 候选列表 ===== */
.rp-list { display: flex; flex-direction: column; gap: 10px; max-height: 62vh; overflow: auto; padding-right: 2px; }
.rp-card {
  position: relative;
  display: flex;
  gap: 14px;
  padding: 12px;
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: linear-gradient(160deg, rgba(255, 255, 255, 0.05), rgba(255, 255, 255, 0.015));
  cursor: pointer;
  transition: border-color 0.18s, box-shadow 0.18s, transform 0.18s, background 0.18s;
}
.rp-card:hover {
  border-color: rgba(110, 140, 255, 0.65);
  background: linear-gradient(160deg, rgba(110, 140, 255, 0.1), rgba(255, 255, 255, 0.02));
  box-shadow: 0 0 0 1px rgba(110, 140, 255, 0.35), 0 8px 28px rgba(60, 100, 255, 0.25);
  transform: translateY(-2px);
}
/* 序号徽标：#01 最可信 */
.rp-rank {
  position: absolute;
  top: -8px;
  left: 10px;
  padding: 1px 8px;
  border-radius: 6px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 10px;
  letter-spacing: 1px;
  color: #aebdff;
  background: #131b33;
  border: 1px solid rgba(110, 140, 255, 0.35);
}

/* ===== 海报：渐变描边框 ===== */
.rp-poster-wrap {
  flex: none;
  padding: 2px;
  border-radius: 10px;
  background: linear-gradient(150deg, rgba(124, 92, 246, 0.9), rgba(59, 110, 246, 0.55), rgba(29, 58, 216, 0.85));
}
.rp-poster {
  display: block;
  width: 62px;
  height: 90px;
  border-radius: 8px;
  object-fit: cover;
  background: #0a0f1e;
}
.rp-nopic {
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  color: rgba(255, 255, 255, 0.25);
  background: repeating-linear-gradient(45deg, #101830, #101830 6px, #0d1326 6px, #0d1326 12px);
}

/* ===== 信息区 ===== */
.rp-info { flex: 1; min-width: 0; }
.rp-line1 { display: flex; align-items: center; gap: 8px; }
.rp-name { font-size: 14px; font-weight: 600; color: #eef2fb; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* 类型徽标：等宽大写 + 霓虹描边（movie 青 / tv 紫） */
.rp-type {
  flex: none;
  padding: 0 7px;
  border-radius: 4px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 9.5px;
  letter-spacing: 1.5px;
  line-height: 18px;
}
.rp-type.is-movie { color: #5cdbd3; border: 1px solid rgba(92, 219, 211, 0.45); background: rgba(92, 219, 211, 0.08); }
.rp-type.is-tv { color: #b39cf8; border: 1px solid rgba(179, 156, 248, 0.45); background: rgba(179, 156, 248, 0.08); }

.rp-meta { display: flex; align-items: center; gap: 8px; margin-top: 4px; font-size: 12px; color: #8a93a8; }
.rp-year { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; color: #aebdff; }
.rp-orig { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rp-overview {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.6;
  color: #8a93a8;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
/* 回填预览：等宽 + 渐变文字，科技感点睛 */
.rp-fill {
  margin-top: 8px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  background: linear-gradient(90deg, #8fb0ff, #b39cf8);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
.rp-fill-arrow { letter-spacing: -1px; }
.rp-empty { padding: 30px 0; text-align: center; font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; letter-spacing: 1px; color: #8a93a8; }
</style>

<!-- 弹窗外壳样式：wrapClassName 挂在 body 层，需非 scoped 覆盖 antd 默认白底 -->
<style>
.rp-tech-modal .ant-modal-content {
  padding: 0;
  background: linear-gradient(165deg, #101729 0%, #0a0f1e 70%);
  border: 1px solid rgba(110, 140, 255, 0.28);
  box-shadow: 0 24px 80px rgba(0, 0, 0, 0.6), 0 0 60px rgba(70, 100, 255, 0.12) inset;
  border-radius: 14px;
  overflow: hidden;
}
.rp-tech-modal .ant-modal-header {
  background: transparent;
  border-bottom: 1px solid rgba(255, 255, 255, 0.07);
  padding: 14px 20px;
}
.rp-tech-modal .ant-modal-close { color: #8a93a8; top: 12px; }
.rp-tech-modal .ant-modal-close:hover { color: #dfe6f5; }
.rp-tech-modal .ant-modal-body { padding: 16px 20px 20px; }
/* 标题行：渐变光条 + 等宽点缀 */
.rp-title { display: flex; align-items: center; gap: 10px; }
.rp-title-bar { width: 4px; height: 16px; border-radius: 2px; background: linear-gradient(180deg, #7c5cf6, #3b6ef6); box-shadow: 0 0 8px rgba(99, 120, 255, 0.8); }
.rp-title-text { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 14px; font-weight: 600; letter-spacing: 1px; color: #eef2fb; }
.rp-title-sub { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 11px; color: #5f6a85; }
</style>
