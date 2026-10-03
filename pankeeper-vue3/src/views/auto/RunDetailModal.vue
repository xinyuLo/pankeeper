<script setup lang="ts">
/* 转存记录详情弹窗（第二层）：执行信息 / 统计 / 正则命中 / 排除与转存清单 / 完整日志。
 * 任务行的「转存日志」弹窗与「转存历史」页共用这里——详情只有一份，别在页面里再抄一遍。 */
import { ref, watch } from 'vue'
import { FileTextOutlined } from '@ant-design/icons-vue'
import LogBox from '@/components/LogBox.vue'
import { getPaRunDetail, type PaRunDetail } from '@/api/modules/tasks'

const props = defineProps<{ open: boolean; runId: number | null }>()
const emit = defineEmits<{ (e: 'update:open', v: boolean): void }>()

const detail = ref<PaRunDetail | null>(null)
const loading = ref(false)

watch(
  () => [props.open, props.runId] as const,
  async ([open, id]) => {
    if (!open || !id) return
    loading.value = true
    detail.value = null
    try {
      detail.value = await getPaRunDetail(id)
    } finally {
      loading.value = false
    }
  },
)

function durTxt(started: string, finished: string): string {
  const a = new Date(started.replace('-', '/')).getTime()
  const b = new Date(finished.replace('-', '/')).getTime()
  if (isNaN(a) || isNaN(b)) return '—'
  const s = Math.max(0, Math.round((b - a) / 1000))
  if (s < 60) return `${s} 秒`
  return `${Math.floor(s / 60)} 分 ${s % 60} 秒`
}
</script>

<template>
  <a-modal
    :open="open"
    :width="760"
    title="转存记录详情"
    :footer="null"
    destroy-on-close
    :body-style="{ 'max-height': '70vh', 'overflow-y': 'auto' }"
    @update:open="(v: boolean) => emit('update:open', v)"
  >
    <div v-if="loading" class="rd-loading">加载中…</div>
    <template v-else-if="detail">
      <div class="rd-section">
        <div class="rd-title">执行信息</div>
        <div class="rd-grid">
          <span class="rd-k">任务</span><span class="rd-v">{{ detail.task_name || '—' }}</span>
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

      <!-- 三段恒常显示：没数据时给空态说明，别让「旧记录没落这个字段」看着像功能没做 -->
      <div class="rd-section">
        <div class="rd-title">正则过滤后的文件（{{ detail.regex_hit.length }}）</div>
        <div v-if="detail.regex_hit.length" class="rd-files">
          <div v-for="n in detail.regex_hit" :key="n" class="rd-file"><FileTextOutlined style="color: #1677ff" /> {{ n }}</div>
        </div>
        <div v-else class="rd-empty">
          {{ detail.regex_pattern ? '该次执行未记录过滤明细（早于本功能上线的旧运行）' : '该任务没配置正则过滤' }}
        </div>
      </div>

      <div class="rd-section">
        <div class="rd-title">排除文件（{{ detail.excluded.length }}，本次不转存）</div>
        <div v-if="detail.excluded.length" class="rd-files">
          <div v-for="n in detail.excluded" :key="n" class="rd-file"><FileTextOutlined style="color: #d48806" /> {{ n }}</div>
        </div>
        <div v-else class="rd-empty">
          {{ detail.excl ? `统计显示排除了 ${detail.excl} 项，但明细未记录（早于本功能上线的旧运行）` : '本次没有命中排除清单的文件' }}
        </div>
      </div>

      <div class="rd-section">
        <div class="rd-title">本次实际转存（{{ detail.transferred.length }}）</div>
        <div v-if="detail.transferred.length" class="rd-files">
          <div v-for="n in detail.transferred" :key="n" class="rd-file"><FileTextOutlined style="color: #52c41a" /> {{ n }}</div>
        </div>
        <div v-else class="rd-empty">{{ detail.status === 'success' ? '本次没有新增文件（都在库，全部跳过）' : '失败执行没有转存明细' }}</div>
      </div>

      <div class="rd-section">
        <div class="rd-title">执行日志（{{ detail.logs.length }} 行）</div>
        <LogBox :lines="detail.logs" />
      </div>
    </template>
  </a-modal>
</template>

<style scoped>
.rd-loading { padding: 40px 0; text-align: center; color: var(--text3); }
.rh-tag { font-size: 12px; padding: 1px 8px; border-radius: 5px; border: 1px solid; }
.rh-tag.ok { color: #389e0d; background: #f6ffed; border-color: #b7eb8f; }
.rh-tag.bad { color: #cf1322; background: #fff1f0; border-color: #ffa39e; }
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
/* 文件清单：与日志框同款「限高 + 滚动」——清单动辄上百条，不设限会把弹窗撑到天上去 */
.rd-files { display: flex; flex-direction: column; gap: 6px; max-height: 210px; overflow-y: auto; padding-right: 4px; }
.rd-file { border: 1px solid var(--split); border-radius: 8px; padding: 8px 12px; font-size: 13px; font-family: var(--font-mono); flex: none; }
.rd-empty { border: 1px dashed var(--split); border-radius: 8px; padding: 10px 12px; font-size: 12.5px; color: var(--text3); }
</style>
