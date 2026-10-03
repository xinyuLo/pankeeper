<script setup lang="ts">
/* 推送历史页（侧边栏：日志管理 → 推送历史）
 * Server 酱/Webhook 每次投递一行：几点推的、成功/失败、推送标题、失败原因。
 * 数据源 GET /notify/history（notify.push 落库的 push_logs 快照，倒序取最近 100 条）。
 * 表格口径与转存历史页一致：全局基础样式 + 本页只收横向内边距（pl- 前缀）。 */
import { onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { ReloadOutlined } from '@ant-design/icons-vue'
import { getPushLogs, type PushLogRow } from '@/api/modules/settings'
import { useIsMobile } from '@/composables/useIsMobile'

const isMobile = useIsMobile()

const STATUS_OPTS = [
  { value: '', label: '全部状态' },
  { value: 'success', label: '成功' },
  { value: 'fail', label: '失败' },
]
const fStatus = ref('')

const rows = ref<PushLogRow[]>([])
const delivered = ref(0)
const failed = ref(0)
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    const r = await getPushLogs(100)
    rows.value = r.items
    delivered.value = r.delivered
    failed.value = r.failed
  } catch {
    message.error('推送历史加载失败')
  } finally {
    loading.value = false
  }
}
onMounted(load)

/* kind → 中文（标题下的次级说明） */
const KIND_TXT: Record<string, string> = {
  search_done: '搜索转存',
  search_fail: '搜索转存',
  auto_done: '自动转存',
  auto_fail: '自动转存',
  cred: '凭据过期告警',
  info: '通知',
}
function kindTxt(k: string) {
  return KIND_TXT[k] || k || '通知'
}
</script>

<template>
  <div>
    <div class="card pl-flush">
      <div class="filterbar fb-head">
        <a-select v-model:value="fStatus" :options="STATUS_OPTS" style="width: 110px" />
        <span class="small muted">最近 100 条 · 成功 {{ delivered }} / 失败 {{ failed }}</span>
        <span class="pl-flex1"></span>
        <a-button :loading="loading" type="primary" ghost @click="load">
          <template #icon><ReloadOutlined /></template>
          刷新
        </a-button>
      </div>

      <table v-if="!isMobile" class="pl-table">
        <thead>
          <tr>
            <th style="width: 168px">推送时间</th>
            <th style="width: 84px">状态</th>
            <th>推送内容</th>
            <th style="width: 30%">失败原因</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id" v-show="!fStatus || r.status === fStatus">
            <td class="small muted pl-nowrap">{{ r.ts }}</td>
            <td>
              <span class="tag" :class="r.status === 'success' ? 't-ok' : 't-bad'">{{ r.status === 'success' ? '成功' : '失败' }}</span>
            </td>
            <td>
              <div class="pl-title">{{ r.title }}</div>
              <span class="pl-sub">{{ kindTxt(r.kind) }}</span>
            </td>
            <td class="small pl-err" :class="r.error ? 'bad-text' : 'muted'">{{ r.error || '—' }}</td>
          </tr>
          <tr v-if="!rows.length">
            <td colspan="4" class="pq-empty">{{ loading ? '加载中…' : '还没有推送记录 · 转存完成或发生告警时这里就有记录' }}</td>
          </tr>
          <tr v-if="rows.length && !rows.some((r) => !fStatus || r.status === fStatus)">
            <td colspan="4" class="pq-empty">没有符合筛选的推送</td>
          </tr>
        </tbody>
      </table>

      <!-- 手机端：一行一条卡片 -->
      <div v-else class="pl-cards">
        <template v-for="r in rows" :key="r.id">
          <div v-if="!fStatus || r.status === fStatus" class="pl-card">
            <div class="pl-card-top">
              <span class="pl-title">{{ r.title }}</span>
              <span class="tag" :class="r.status === 'success' ? 't-ok' : 't-bad'">{{ r.status === 'success' ? '成功' : '失败' }}</span>
            </div>
            <div class="pl-card-meta small muted">{{ r.ts }} · {{ kindTxt(r.kind) }}</div>
            <div v-if="r.error" class="pl-err bad-text small">{{ r.error }}</div>
          </div>
        </template>
        <div v-if="!rows.length" class="pq-empty">{{ loading ? '加载中…' : '还没有推送记录' }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 卡片去内边距：筛选条接表头、表格连成一张卡（转存历史页同款） */
.pl-flush { padding: 0; overflow: hidden; }
.pl-flex1 { flex: 1; }

/* 表格走全局基础样式，只收窄横向内边距 + 状态列不换行（别再压字号/字重） */
.pl-table { table-layout: fixed; }
.pl-table th,
.pl-table td { padding-left: 12px; padding-right: 12px; }
.pl-nowrap { white-space: nowrap; }
.pl-title { font-size: 13.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pl-sub { font-size: 12px; color: var(--text3); }
.pl-err { overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; word-break: break-all; }
.bad-text { color: var(--error); }

/* 手机卡片 */
.pl-cards { display: block; }
.pl-card { padding: 12px 14px; border-bottom: 1px solid var(--split); }
.pl-card:last-child { border-bottom: none; }
.pl-card-top { display: flex; align-items: center; gap: 8px; }
.pl-card-top .pl-title { flex: 1; min-width: 0; }
.pl-card-meta { margin-top: 3px; }
.pl-card .pl-err { margin-top: 5px; }

@media (max-width: 767px) {
  .filterbar { flex-wrap: wrap; }
}
</style>
