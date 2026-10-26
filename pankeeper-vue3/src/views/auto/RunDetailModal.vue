<script setup lang="ts">
/* 转存记录详情弹窗（第二层）：执行信息 / 统计 / 正则命中 / 排除与转存清单 / 完整日志。
 * 任务行的「转存日志」弹窗与「转存历史」页共用这里——详情只有一份，别在页面里再抄一遍。 */
import { computed, ref, watch } from 'vue'
import { FileTextOutlined } from '@ant-design/icons-vue'
import LogBox from '@/components/LogBox.vue'
import { getPaRunDetail, type PaRunDetail } from '@/api/modules/tasks'

import { useBackGuard } from '@/composables/useBackGuard'
const props = defineProps<{ open: boolean; runId: number | null }>()
const emit = defineEmits<{ (e: 'update:open', v: boolean): void }>()
useBackGuard(() => props.open, () => emit('update:open', false))

const detail = ref<PaRunDetail | null>(null)
const loading = ref(false)

/** 跳过三路的差额（T-017-F2）：勾选清单过滤（auto 任务也能配）/建壳等计入 skip 但不入三路。
 * 不新增库列，差额 = 跳过合计 - 台账 - MD5 - 同名。 */
const skipGap = computed(() => {
  const d = detail.value
  if (!d) return 0
  return Math.max(0, (d.skip || 0) - (d.skip_ledger || 0) - (d.skip_md5 || 0) - (d.name_skipped || []).length)
})
/** 旧记录（本功能上线前）：三路名单全空但跳过不止 MD5 一路 → 差额其实是「没拆过账」 */
const gapIsLegacy = computed(() => {
  const d = detail.value
  if (!d) return false
  return !(d.skip_ledger || 0) && !(d.name_skipped || []).length && (d.skip || 0) > (d.skip_md5 || 0)
})

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

/** 联动快照 cls（t-ok/t-bad/t-off）→ 本弹窗 tag 类（ok/bad/off） */
function clsOf(cls: string): string {
  return cls === 't-ok' ? 'ok' : cls === 't-bad' ? 'bad' : 'off'
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
          <span class="rd-k">执行结果</span>
          <span class="rd-v rd-result">
            <span class="rh-tag" :class="detail.status === 'success' ? 'ok' : 'bad'">转存{{ detail.status === 'success' ? '成功' : '失败' }}</span>
            <template v-if="detail.qms">
              <span class="rd-sep">·</span> QMS <span class="rh-tag" :class="clsOf(detail.qms.cls)">{{ detail.qms.st }}</span>
            </template>
            <template v-if="detail.strm">
              <span class="rd-sep">·</span> STRM <span class="rh-tag" :class="clsOf(detail.strm.cls)">{{ detail.strm.st }}</span>
            </template>
          </span>
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
          <div class="rd-stat"><b>{{ detail.skip_ledger || 0 }}</b><span>台账跳过</span></div>
          <div class="rd-stat"><b>{{ detail.skip_md5 }}</b><span>MD5 命中跳过</span></div>
          <div class="rd-stat"><b>{{ (detail.name_skipped || []).length }}</b><span>同名跳过</span></div>
          <div class="rd-stat"><b>{{ detail.skip }}</b><span>跳过合计</span></div>
          <div class="rd-stat"><b class="ok">{{ detail.add }}</b><span>本次转存</span></div>
        </div>
        <div v-if="skipGap > 0" class="rd-gapfoot">
          {{ gapIsLegacy ? `旧记录未拆分（本功能上线前）：另有 ${skipGap} 项跳过未分台账 / MD5 / 同名` : `另有 ${skipGap} 项：勾选清单/其他过滤（未计入三路拆分）` }}
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
        <div class="rd-title">MD5 去重跳过的文件（{{ detail.md5_skipped.length }}）</div>
        <div v-if="detail.md5_skipped.length" class="rd-files">
          <div v-for="n in detail.md5_skipped" :key="n" class="rd-file"><FileTextOutlined style="color: #722ed1" /> {{ n }}</div>
        </div>
        <div v-else class="rd-empty">
          {{ detail.skip_md5 ? `统计显示 MD5 命中跳过 ${detail.skip_md5} 项，但明细未记录（早于本功能上线的旧运行）` : '本次没有因 MD5 重复被跳过的文件' }}
        </div>
      </div>

      <div class="rd-section">
        <div class="rd-title">台账命中的文件（{{ (detail.ledger_skipped || []).length }}）</div>
        <div v-if="(detail.ledger_skipped || []).length" class="rd-files">
          <div v-for="(e, i) in detail.ledger_skipped || []" :key="i" class="rd-file" :title="e.md5 ? `MD5 ${e.md5}` : ''">
            <FileTextOutlined style="color: #13c2c2" /> {{ e.name }}
          </div>
        </div>
        <div v-else class="rd-empty">本次没有台账命中的文件（台账 = 本地已转存账本拦下、连网盘基线都不查的那部分；早于本功能上线的旧运行没有明细）</div>
      </div>

      <div class="rd-section">
        <div class="rd-title">同名命中的文件（{{ (detail.name_skipped || []).length }}）</div>
        <div v-if="(detail.name_skipped || []).length" class="rd-files">
          <div v-for="(e, i) in detail.name_skipped || []" :key="i" class="rd-file" :class="{ 'rd-diff': e.md5_diff }">
            <FileTextOutlined :style="{ color: e.md5_diff ? '#fa8c16' : '#722ed1' }" /> {{ e.name }}
            <span v-if="e.md5_uncomparable" class="rd-diff-tag rd-diff-unk">同名（无法比对 MD5）</span>
            <span v-else-if="e.md5_diff" class="rd-diff-tag">同名但 MD5 不同，内容可能不同</span>
          </div>
        </div>
        <div v-else class="rd-empty">本次没有同名命中的文件（早于本功能上线的旧运行没有明细）</div>
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
/* 联动未配置/未执行：灰色中性 tag */
.rh-tag.off { color: var(--text3); background: var(--surface-2); border-color: var(--split); }
.rd-result { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.rd-sep { color: var(--text3); }
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
/* 同名但 MD5 不同（T-017 用户定稿：跳过但标黄提示「内容可能不同」，不改转存行为） */
.rd-file.rd-diff { border-color: #faad14; background: rgba(250, 173, 20, 0.08); }
.rd-diff-tag { margin-left: 8px; font-size: 11.5px; color: #fa8c16; }
/* 无法比对 MD5（基线/文件任一侧缺）：中性灰，不冒充「不同」 */
.rd-diff-tag.rd-diff-unk { color: var(--text3); }
/* 三路拆账差额脚注（勾选过滤/旧记录未拆分，T-017-F2） */
.rd-gapfoot { margin-top: 8px; font-size: 12px; color: var(--text3); }
html[data-theme='dark'] .rd-file.rd-diff { border-color: rgba(250, 173, 20, 0.55); background: rgba(250, 173, 20, 0.12); }
html[data-theme='dark'] .rd-diff-tag { color: #ffa940; }
</style>
