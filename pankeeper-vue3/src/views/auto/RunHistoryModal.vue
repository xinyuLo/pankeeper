<script setup lang="ts">
/* 转存日志弹窗（bdsavePro 风格）：任务的历史执行卡片（成功/失败 + 起止时间 + 统计 chips），
 * 点「详情」展开第二层：执行信息 / 统计块 / 排除与转存文件清单 / 完整日志。 */
import { ref, watch } from 'vue'
import { FolderOpenOutlined, FileTextOutlined } from '@ant-design/icons-vue'
import LogBox from '@/components/LogBox.vue'
import { getPaRunDetail, getPaRuns, type PaRunDetail, type PaRunRow } from '@/api/modules/tasks'
import type { PaTask } from '@/types/model'

const props = defineProps<{ open: boolean; task: PaTask | null }>()
const emit = defineEmits<{ (e: 'update:open', v: boolean): void }>()

const rows = ref<PaRunRow[]>([])
const loading = ref(false)
const detail = ref<PaRunDetail | null>(null)
const detailOpen = ref(false)
const detailLoading = ref(false)

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

async function openDetail(id: number) {
  detailLoading.value = true
  detailOpen.value = true
  try {
    detail.value = await getPaRunDetail(id)
  } finally {
    detailLoading.value = false
  }
}

function durTxt(started: string, finished: string): string {
  const a = new Date(started.replace('-', '/')).getTime()
  const b = new Date(finished.replace('-', '/')).getTime()
  if (isNaN(a) || isNaN(b)) return '—'
  const s = Math.max(0, Math.round((b - a) / 1000))
  if (s < 60) return `${s} 秒`
  return `${Math.floor(s / 60)} 分 ${s % 60} 秒`
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
          <span class="rh-tag" :class="r.status === 'success' ? 'ok' : 'bad'">{{ r.status === 'success' ? '成功' : '失败' }}</span>
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
        <div class="rh-msg">{{ r.message || '—' }}</div>
      </div>
    </div>

    <!-- 详情第二层 -->
    <a-modal
      v-model:open="detailOpen"
      :width="760"
      title="转存记录详情"
      :footer="null"
      destroy-on-close
      :body-style="{ 'max-height': '70vh', 'overflow-y': 'auto' }"
    >
      <template v-if="detail">
        <div class="rd-section">
          <div class="rd-title">执行信息</div>
          <div class="rd-grid">
            <span class="rd-k">开始时间</span><span class="rd-v mono">{{ detail.started }}</span>
            <span class="rd-k">结束时间</span><span class="rd-v mono">{{ detail.finished }}</span>
            <span class="rd-k">耗时</span><span class="rd-v mono">{{ durTxt(detail.started, detail.finished) }}</span>
            <span class="rd-k">执行结果</span><span class="rd-v"><span class="rh-tag" :class="detail.status === 'success' ? 'ok' : 'bad'">{{ detail.status === 'success' ? '成功' : '失败' }}</span></span>
            <span class="rd-k">转存路径</span><span class="rd-v mono link">{{ detail.save_dir || '—' }}</span>
            <span class="rd-k">对比路径</span><span class="rd-v mono link">{{ detail.compare_path || '—' }}</span>
            <span class="rd-k">包含子目录</span><span class="rd-v">{{ detail.include_subdirs ? '是（连子文件夹一起存）' : '否（只存里面的内容）' }}</span>
            <span class="rd-k">文件过滤</span><span class="rd-v mono link">{{ detail.regex_pattern || '—' }}</span>
          </div>
        </div>

        <div class="rd-section">
          <div class="rd-title">执行结果</div>
          <div class="rd-msgbox">{{ detail.message || '—' }}</div>
          <div class="rd-stats">
            <div class="rd-stat"><b>{{ detail.total_share }}</b><span>分享文件</span></div>
            <div class="rd-stat"><b class="warn">{{ detail.excl }}</b><span>排除清单跳过</span></div>
            <div class="rd-stat"><b>{{ detail.regex_miss }}</b><span>正则未命中</span></div>
            <div class="rd-stat"><b>{{ detail.skip_md5 }}</b><span>MD5 命中跳过</span></div>
            <div class="rd-stat"><b class="ok">{{ detail.add }}</b><span>本次转存</span></div>
          </div>
        </div>

        <div v-if="detail.regex_hit?.length" class="rd-section">
          <div class="rd-title">正则命中（{{ detail.regex_hit.length }}，通过过滤参与本轮）</div>
          <div class="rd-files">
            <div v-for="n in detail.regex_hit" :key="n" class="rd-file"><FileTextOutlined style="color: #1677ff" /> {{ n }}</div>
          </div>
        </div>

        <div v-if="detail.excluded.length" class="rd-section">
          <div class="rd-title">排除文件（{{ detail.excluded.length }}，本次不转存）</div>
          <div class="rd-files">
            <div v-for="n in detail.excluded" :key="n" class="rd-file"><FileTextOutlined style="color: #d48806" /> {{ n }}</div>
          </div>
        </div>

        <div v-if="detail.transferred.length" class="rd-section">
          <div class="rd-title">本次实际转存（{{ detail.transferred.length }}）</div>
          <div class="rd-files">
            <div v-for="n in detail.transferred" :key="n" class="rd-file"><FileTextOutlined style="color: #52c41a" /> {{ n }}</div>
          </div>
        </div>

        <div class="rd-section">
          <div class="rd-title">执行日志（{{ detail.logs.length }} 行）</div>
          <LogBox :lines="detail.logs" />
        </div>
      </template>
    </a-modal>
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
.rh-tag.bad { color: #cf1322; background: #fff1f0; border-color: #ffa39e; }
.rh-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.rh-chip { font-size: 12px; padding: 2px 10px; border-radius: 999px; background: var(--surface-2); color: var(--text2); border: 1px solid var(--split); }
.rh-chip.ok { color: #389e0d; background: #f6ffed; border-color: #b7eb8f; }
.rh-dir { margin-top: 8px; font-size: 12.5px; color: var(--text3); }
.rh-dir svg { color: var(--primary); margin-right: 4px; }
.rh-msg { margin-top: 6px; font-size: 13px; color: var(--text); }
html[data-theme='dark'] .rh-tag.ok { color: #95de64; background: rgba(82, 196, 26, 0.16); }
html[data-theme='dark'] .rh-tag.bad { color: #ff9c9c; background: rgba(255, 77, 79, 0.16); }

.rd-section { margin-bottom: 18px; }
.rd-title { font-size: 13.5px; font-weight: 600; margin-bottom: 10px; }
.rd-grid { display: grid; grid-template-columns: 76px 1fr; gap: 9px 12px; font-size: 13px; background: var(--surface-2); border-radius: 10px; padding: 12px 14px; }
.rd-k { color: var(--text3); }
.rd-v { color: var(--text); min-width: 0; overflow-wrap: anywhere; }
.rd-v.mono, .mono { font-family: var(--font-mono); }
.rd-v.link { color: var(--primary); }
.rd-msgbox { border: 1px solid var(--split); border-radius: 8px; padding: 10px 12px; font-size: 13px; margin-bottom: 10px; }
.rd-stats { display: flex; gap: 10px; flex-wrap: wrap; }
.rd-stat { flex: 1 1 100px; background: var(--surface-2); border-radius: 8px; padding: 10px 8px; text-align: center; }
.rd-stat b { display: block; font-size: 20px; color: var(--text); }
.rd-stat b.ok { color: #52c41a; }
.rd-stat b.warn { color: #faad14; }
.rd-stat span { font-size: 11.5px; color: var(--text3); }
.rd-files { display: flex; flex-direction: column; gap: 6px; }
.rd-file { border: 1px solid var(--split); border-radius: 8px; padding: 8px 12px; font-size: 13px; font-family: var(--font-mono); }
</style>
