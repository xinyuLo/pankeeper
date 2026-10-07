<script setup lang="ts">
/* 转存日志弹窗（bdsavePro 风格）：单个任务的历史执行卡片（成功/失败 + 起止时间 + 统计 chips），
 * 点「详情」展开第二层（共用 RunDetailModal——与「转存历史」页同一份详情）。
 * 全任务视角的历史请看「转存历史」页（侧边栏自动转存组）。 */
import { ref, watch } from 'vue'
import { FolderOpenOutlined } from '@ant-design/icons-vue'
import RunDetailModal from './RunDetailModal.vue'
import { getPaRuns, type PaRunRow } from '@/api/modules/tasks'
import type { PaTask } from '@/types/model'

import { useBackGuard } from '@/composables/useBackGuard'
const props = defineProps<{ open: boolean; task: PaTask | null }>()
const emit = defineEmits<{ (e: 'update:open', v: boolean): void }>()
useBackGuard(() => props.open, () => emit('update:open', false))

const rows = ref<PaRunRow[]>([])
const loading = ref(false)
const detailOpen = ref(false)
const detailId = ref<number | null>(null)

async function load() {
  if (!props.task) return
  loading.value = true
  try {
    rows.value = await getPaRuns(props.task.id)
  } finally {
    loading.value = false
  }
}

watch(
  () => props.open,
  (v) => {
    if (v) {
      rows.value = []
      detailOpen.value = false
      load()
    }
  },
)

function openDetail(id: number) {
  detailId.value = id
  detailOpen.value = true
}

/** 整单结果 → 样式类（后端 overall.cls 是 t-ok/t-warn/t-bad；t-warn = 部分失败） */
function ovCls(r: PaRunRow): string {
  const c = r.overall?.cls
  return c === 't-warn' ? 'warn' : c === 't-bad' ? 'bad' : 'ok'
}

function close() {
  emit('update:open', false)
}
</script>

<template>
  <a-modal :open="open" :width="720" :title="`转存日志 · ${task?.name || ''}`" :footer="null" @update:open="(v: boolean) => emit('update:open', v)">
    <div v-if="loading" class="rh-loading">加载中…</div>
    <div v-else-if="!rows.length" class="rh-loading">还没有执行记录（点一次「执行」就会生成）</div>
    <div v-else class="rh-list">
      <div v-for="r in rows" :key="r.id" class="rh-card">
        <div class="rh-head">
          <!-- 整单结果：转存成功但 QMS 有失败 = 部分失败（橙），别只报转存那一半 -->
          <span class="rh-tag" :class="ovCls(r)">{{ r.overall?.st || (r.status === 'success' ? '成功' : '失败') }}</span>
          <span class="rh-time">{{ r.started }} → {{ r.finished }}</span>
          <a class="rh-detail" @click="openDetail(r.id)">详情</a>
        </div>
        <div class="rh-chips">
          <span class="rh-chip" :class="r.add ? 'ok' : ''">转存 {{ r.add }} 个</span>
          <span class="rh-chip">分享 {{ r.total_share }} 个</span>
          <span class="rh-chip">排除 {{ r.excl }}</span>
          <span class="rh-chip">正则未命中 {{ r.regex_miss }}</span>
          <span class="rh-chip">MD5 跳过 {{ r.skip_md5 }}</span>
        </div>
        <div class="rh-dir"><FolderOpenOutlined /> 转存到：{{ task?.save_dir || '—' }}</div>
        <div class="rh-msg" :class="r.status === 'success' ? 'ok' : 'bad'">{{ r.message || '—' }}</div>
      </div>
    </div>

    <!-- 详情第二层（与「转存历史」页共用组件） -->
    <RunDetailModal v-model:open="detailOpen" :run-id="detailId" />
  </a-modal>
</template>

<style scoped>
.rh-loading { padding: 40px 0; text-align: center; color: var(--text3); }
.rh-list { max-height: 66vh; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; }
.rh-card { border: 1px solid var(--split); border-radius: 10px; padding: 12px 14px; }
.rh-head { display: flex; align-items: center; gap: 10px; }
.rh-time { font-family: var(--font-mono); font-size: 12.5px; color: var(--text2); flex: 1; }
.rh-detail { color: var(--primary); font-size: 13px; cursor: pointer; }
.rh-tag { font-size: 12px; padding: 1px 8px; border-radius: 5px; border: 1px solid; }
.rh-tag.ok { color: #389e0d; background: #f6ffed; border-color: #b7eb8f; }
.rh-tag.warn { color: #d48806; background: #fffbe6; border-color: #ffe58f; }
.rh-tag.bad { color: #cf1322; background: #fff1f0; border-color: #ffa39e; }
.rh-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.rh-chip { font-size: 12px; padding: 2px 10px; border-radius: 999px; background: var(--surface-2); color: var(--text2); border: 1px solid var(--split); }
.rh-chip.ok { color: #389e0d; background: #f6ffed; border-color: #b7eb8f; }
.rh-dir { margin-top: 8px; font-size: 12.5px; color: var(--text3); }
.rh-dir svg { color: var(--primary); margin-right: 4px; }
.rh-msg { margin-top: 6px; font-size: 13px; }
/* 说明文案按成败染色（用户要求：像结果 tag 一样一眼分好坏） */
.rh-msg.ok { color: #389e0d; }
.rh-msg.bad { color: #cf1322; }
html[data-theme='dark'] .rh-msg.ok { color: #95de64; }
html[data-theme='dark'] .rh-msg.bad { color: #ff9c9c; }
html[data-theme='dark'] .rh-tag.ok { color: #95de64; background: rgba(82, 196, 26, 0.16); }
html[data-theme='dark'] .rh-tag.warn { color: #ffc53d; background: rgba(250, 173, 20, 0.16); }
html[data-theme='dark'] .rh-tag.bad { color: #ff9c9c; background: rgba(255, 77, 79, 0.16); }
</style>
