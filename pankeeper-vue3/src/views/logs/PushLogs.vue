<script setup lang="ts">
/* 推送历史页（侧边栏：日志管理 → 推送历史）
 * Server 酱/Webhook 每次投递一行：几点推的、成功/失败、推送标题、失败原因。
 * 行内「详情」/点卡片 → 抽屉展示 Server酱实际收到的完整正文（push_logs.content 快照）。
 * 数据源 GET /notify/history（notify.push 落库的 push_logs 快照，倒序取最近 100 条）。
 * 表格口径与转存历史页一致：全局基础样式 + 本页只收横向内边距（pl- 前缀）。 */
import { computed, onMounted, ref } from 'vue'
import ClearHistoryButton, { type ClearRange } from '@/components/ClearHistoryButton.vue'
import { useBackGuard } from '@/composables/useBackGuard'
import { message } from 'ant-design-vue'
import { getPushLogs, getPushLogDetail, clearPushLogs, deletePushLog, type PushLogRow, type PushLogDetail } from '@/api/modules/settings'
import { useIsMobile } from '@/composables/useIsMobile'
import MarkdownIt from 'markdown-it'
import { DeleteOutlined, EyeOutlined, ReloadOutlined } from '@ant-design/icons-vue'

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

/* ===== 详情抽屉：拉该条推送的完整正文快照 ===== */
const detailOpen = ref(false)
useBackGuard(detailOpen)
const md = new MarkdownIt({ breaks: true, linkify: true })
/** TMDB 图走本机代理：正文里存的是 image.tmdb.org 直链，墙内浏览器拉不到、图是空白。
 *  改成 /img?u=...（后端代拉+缓存），内外网都能显示；只动图片，不动正文其它内容。 */
function proxifyImages(text: string) {
  return text.replace(/https:\/\/image\.tmdb\.org\/[^\s)]+/g, (u) => `/img?u=${encodeURIComponent(u)}`)
}
/** 正文按 Markdown 渲染（与 Server酱展示同观感：标题/加粗/图片/段落） */
const detailHtml = computed(() => (detail.value?.content ? md.render(proxifyImages(detail.value.content)) : ''))
/** 失败原因说人话：库里仍存原始报文（排障要用），只在展示层翻译。
 *  新增通道错误文案时往这里补映射，别把英文原始报文糊到脸上；表格里 title 仍挂原文。 */
const ERROR_HUMAN: Array<[RegExp, string]> = [
  [/device token/i, 'Bark 服务器不认识这个设备：Device Key 对不上，去 Bark App 里重新复制填到设置里'],
  [/BadStatusLine|SSH-2\.0/i, 'Bark 地址连到 SSH 端口上了：服务器地址或端口填错了'],
  [/连接超时|timed out|timeout/i, '推送服务器响应超时：网络不稳或服务忙，稍后重试'],
  [/HTTP 401|Unauthorized/i, '推送服务器拒绝登录：地址或密钥不对（401）'],
  [/HTTP 404/i, '推送地址不存在：服务器地址填错了（404）'],
  [/Connection refused|拒绝连接/i, '推送服务器没在跑，或端口不通'],
  [/Name or service not known|getaddrinfo|nodename nor servname/i, '推送服务器域名解析失败：检查地址'],
  [/SendKey 未配置/i, 'Server酱 SendKey 还没填'],
  [/DeviceKey 未配置|服务器地址或 DeviceKey 未配置/i, 'Bark 服务器地址或 Device Key 还没填'],
  [/地址未配置/i, 'Webhook 地址还没填'],
]
function friendlyError(err?: string) {
  const raw = (err || '').trim()
  if (!raw) return ''
  for (const [re, txt] of ERROR_HUMAN) if (re.test(raw)) return txt
  return raw
}
const detailLoading = ref(false)
const detail = ref<PushLogDetail | null>(null)
async function openDetail(row: PushLogRow) {
  detailOpen.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await getPushLogDetail(row.id)
  } catch {
    message.error('推送详情加载失败')
    detailOpen.value = false
  } finally {
    detailLoading.value = false
  }
}

/** 列表预览：正文压平换行截 60 字（全文走详情抽屉；不压平会把行撑爆错位） */
function preview(c: string) {
  return (c || '').replace(/\s+/g, ' ').trim().slice(0, 60)
}

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
/** 清空推送历史（下拉选范围） */
async function onClear(range: ClearRange) {
  let before = ''
  if (range !== 'all') {
    const d = new Date()
    d.setMonth(d.getMonth() - { '1m': 1, '3m': 3, '6m': 6 }[range])
    before = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} 00:00:00`
  }
  const n = await clearPushLogs(before)
  message.success(n > 0 ? `已清空 ${n} 条推送记录` : '没有符合条件的记录')
  await load()
}

/** 删除单条推送记录；带 Bark id 的行联动下发删除指令，手机端通知/历史一并删 */
async function onDeleteRow(r: PushLogRow) {
  try {
    const res = await deletePushLog(r.id)
    if (res.bark?.sent) message.success('记录已删除，手机端删除指令已下发')
    else if (res.bark?.message) message.warning(`记录已删除；手机端删除未成功：${res.bark.message}`, 5)
    else message.success('已删除')
    await load()
  } catch {
    message.error('删除失败')
  }
}
</script>

<template>
  <div>
    <div class="card pl-flush">
      <div class="filterbar fb-head">
        <a-select v-model:value="fStatus" :options="STATUS_OPTS" style="width: 110px" />
        <span class="small muted">最近 100 条 · 成功 {{ delivered }} / 失败 {{ failed }}</span>
        <span class="pl-flex1"></span>
        <ClearHistoryButton @clear="onClear" />
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
            <th style="width: 26%">失败原因</th>
            <th style="width: 118px; text-align: left">操作</th>
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
              <span class="pl-sub">{{ kindTxt(r.kind) }}<template v-if="r.content"> · {{ preview(r.content) }}</template></span>
            </td>
            <!-- ⚠️ pl-err（display:-webkit-box）绝不能挂在 td 上：会覆盖 table-cell 布局，
                 td 不随行拉伸、border 错位（2026-10-06 行错位实锤根因）。挂内部 span。 -->
            <td class="small">
              <span class="pl-err" :class="r.error ? 'bad-text' : 'muted'" :title="r.error || ''">{{ friendlyError(r.error) || '—' }}</span>
            </td>
            <td>
              <span class="pl-acts">
                <a class="pl-detail" @click="openDetail(r)"><EyeOutlined /> 详情</a>
                <a-popconfirm
                  :title="r.has_bark ? '确认删除这条推送？手机端 Bark 的对应通知与历史会一并删除' : '确认删除这条推送记录？'"
                  ok-text="删除" cancel-text="取消" :ok-button-props="{ danger: true }"
                  @confirm="onDeleteRow(r)"
                >
                  <a class="pl-del"><DeleteOutlined /> 删除</a>
                </a-popconfirm>
              </span>
            </td>
          </tr>
          <tr v-if="!rows.length">
            <td colspan="5" class="pq-empty">{{ loading ? '加载中…' : '还没有推送记录 · 转存完成或发生告警时这里就有记录' }}</td>
          </tr>
          <tr v-if="rows.length && !rows.some((r) => !fStatus || r.status === fStatus)">
            <td colspan="5" class="pq-empty">没有符合筛选的推送</td>
          </tr>
        </tbody>
      </table>

      <!-- 手机端：一行一条卡片，点卡片开详情 -->
      <div v-else class="pl-cards">
        <template v-for="r in rows" :key="r.id">
          <div v-if="!fStatus || r.status === fStatus" class="pl-card" @click="openDetail(r)">
            <div class="pl-card-top">
              <span class="pl-title">{{ r.title }}</span>
              <span class="tag" :class="r.status === 'success' ? 't-ok' : 't-bad'">{{ r.status === 'success' ? '成功' : '失败' }}</span>
              <a-popconfirm
                :title="r.has_bark ? '确认删除这条推送？手机端 Bark 的对应通知与历史会一并删除' : '确认删除这条推送记录？'"
                ok-text="删除" cancel-text="取消" :ok-button-props="{ danger: true }"
                @confirm="onDeleteRow(r)"
              >
                <a class="pl-card-del" @click.stop><DeleteOutlined /></a>
              </a-popconfirm>
            </div>
            <div class="pl-card-meta small muted">{{ r.ts }} · {{ kindTxt(r.kind) }}</div>
            <div v-if="r.error" class="pl-err bad-text small">{{ friendlyError(r.error) }}</div>
          </div>
        </template>
        <div v-if="!rows.length" class="pq-empty">{{ loading ? '加载中…' : '还没有推送记录' }}</div>
      </div>
    </div>

    <!-- 详情抽屉：Server酱实际收到的完整正文（Markdown 原样 pre-wrap 展示） -->
    <a-drawer v-model:open="detailOpen" title="推送详情" :width="isMobile ? '100%' : 560" placement="right">
      <a-spin :spinning="detailLoading">
        <template v-if="detail">
          <div class="pd-meta">
            <span class="tag" :class="detail.status === 'success' ? 't-ok' : 't-bad'">{{ detail.status === 'success' ? '成功' : '失败' }}</span>
            <span class="small muted">{{ detail.ts }} · {{ kindTxt(detail.kind) }}</span>
          </div>
          <div class="pd-block">
            <div class="pd-label">标题</div>
            <div>{{ detail.title }}</div>
          </div>
          <div v-if="detail.error" class="pd-block">
            <div class="pd-label">失败原因</div>
            <div class="bad-text">{{ friendlyError(detail.error) }}</div>
            <div v-if="friendlyError(detail.error) !== detail.error" class="small muted" style="margin-top: 4px">
              原始报错：{{ detail.error }}
            </div>
          </div>
          <div class="pd-block">
            <div class="pd-label">推送正文（Server酱实际收到）</div>
            <div v-if="detail.content" class="md-body" v-html="detailHtml"></div>
            <div v-else class="small muted">（无正文快照——本条产生于详情功能上线前）</div>
          </div>
        </template>
      </a-spin>
    </a-drawer>
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
.pl-sub { font-size: 12px; color: var(--text3); display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pl-err { overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; word-break: break-all; }
.bad-text { color: var(--error); }

/* 手机卡片 */
.pl-cards { display: block; }
.pl-card { padding: 12px 14px; border-bottom: 1px solid var(--split); cursor: pointer; }
.pl-card:last-child { border-bottom: none; }
.pl-card-top { display: flex; align-items: center; gap: 8px; }
.pl-card-top .pl-title { flex: 1; min-width: 0; }
.pl-card-meta { margin-top: 3px; }
.pl-card .pl-err { margin-top: 5px; }

/* 操作列：详情 / 删除并排（删除红色，confirm 走 a-popconfirm） */
.pl-acts { display: inline-flex; align-items: center; gap: 14px; white-space: nowrap; }
.pl-detail {
  font-size: 12.5px;
  color: var(--primary);
  white-space: nowrap;
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 3px;
  text-decoration-color: rgba(22, 119, 255, 0.35);
}
.pl-detail:hover { text-decoration-color: var(--primary); }
.pl-del {
  font-size: 12.5px;
  color: var(--error);
  white-space: nowrap;
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 3px;
  text-decoration-color: rgba(255, 77, 79, 0.35);
}
.pl-del:hover { text-decoration-color: var(--error); }
/* 手机卡片右上角删除图标（@click.stop 防止触发卡片整行的详情） */
.pl-card-del { color: var(--text3); cursor: pointer; font-size: 13px; padding: 2px; }
.pl-card-del:hover { color: var(--error); }
.pd-meta { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }
.pd-block { margin-bottom: 16px; }
.pd-label { font-size: 12px; color: var(--text3); margin-bottom: 6px; }

@media (max-width: 767px) {
  .filterbar { flex-wrap: wrap; }
}
/* Markdown 正文（Server酱同观感）：固定高度浅灰框，内部滚动 */
.md-body {
  max-height: 60vh;
  overflow-y: auto;
  padding: 12px 14px;
  background: var(--surface-2);
  border-radius: var(--r-sm, 8px);
  font-size: 13.5px;
  line-height: 1.8;
  color: var(--text);
  overflow-wrap: anywhere;
}
.md-body :deep(p) { margin: 8px 0; }
.md-body :deep(h2) {
  font-size: 15.5px;
  font-weight: 600;
  margin: 16px 0 8px;
  padding-bottom: 5px;
  border-bottom: 1px solid var(--split);
}
.md-body :deep(h3) { font-size: 14px; font-weight: 600; margin: 12px 0 6px; }
.md-body :deep(img) {
  display: block;
  max-width: 100%;
  border-radius: 8px;
  margin: 6px 0;
}
.md-body :deep(strong) { font-weight: 600; }
.md-body :deep(a) { color: var(--primary); }
.md-body :deep(hr) { border: none; border-top: 1px dashed var(--split); margin: 10px 0; }
</style>
