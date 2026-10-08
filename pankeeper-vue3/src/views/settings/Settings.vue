<script setup lang="ts">
/* =====================================================================
 * 系统设置页 —— 原型 _shell.html 设置段（胶囊 tab）的 Vue 移植。
 * - tab1 搜索源：改完静默保存（原型即改即存内存，无保存按钮）
 * - tab2 代理配置：TMDB API Key + 连通模式（代理 / host 直连），TMDB 字段从搜索源 tab 迁来
 * - tab3 推送通知：改完静默保存（原型即改即存内存，无保存按钮）
 * - tab4 联动后端：顶部选 qms/litepan，下面按所选显示 QMS 连接参数或 LitePan Webhook 参数
 *   （目录关联在「转存配置」与自动转存任务弹窗），与 tab1/tab3 一致走防抖自动保存
 * - tab5 账号安全：改密码（前端先校验）+ 会话有效期
 * - tab6 头像管理：上传/移除头像（前端压缩后存后端）
 * ⚠️ 交互契约：推送只服务自动转存，手动转存不接 Server 酱（docs/01）。
 * ===================================================================== */
import { nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { DeleteOutlined } from '@ant-design/icons-vue'
import { useAuthStore } from '@/store/auth'
import {
  getLitePanHealth,
  getQmsHealth,
  getSettings,
  saveNotify,
  saveLitePan,
  saveMediaBackend,
  saveQms,
  saveSearchSrc,
  saveSecurity,
  testLitePan,
  testPansou,
  testQms,
  testSendkey,
  testTmdb,
} from '@/api/modules/settings'
import { getEngineHealth } from '@/api/modules/search'
import type { LitePanCfg, NotifyCfg, QmsCfg, SearchSrcCfg, SecurityCfg } from '@/api/mock/settings'

const router = useRouter()
const auth = useAuthStore()

/* ===== 胶囊 tab ===== */
const TABS = [
  { k: 'tb1', label: '搜索源' },
  { k: 'tb2', label: '代理配置' },
  { k: 'tb3', label: '推送通知' },
  { k: 'tb4', label: '联动后端' },
  { k: 'tb5', label: '账号安全' },
  { k: 'tb6', label: '头像管理' },
] as const
type TabKey = (typeof TABS)[number]['k']
const tab = ref<TabKey>('tb1')

/* ===== 四份表单的本地副本（进页面从 mock store 拷一份，保存时写回） ===== */
const search = reactive<SearchSrcCfg>({
  pansou_url: '',
  timeout: 30,
  cache_mode: 'on',
  channels: [],
})
const notify = reactive<NotifyCfg>({
  enabled: false,
  sendkey: '',
  webhook: '',
  on_auto: true,
  on_search: true,
  on_cred: true,
})
const qms = reactive<QmsCfg>({ enabled: true, url: '', apikey: '', tmdb_api_key: '', tmdb_mode: 'proxy', tmdb_proxy: '', tmdb_hosts: [], tmdb_skip_tls: false, act_strm: true, act_emby: true })
const litepan = reactive<LitePanCfg>({ enabled: false, webhook_url: '', apikey: '', source: '' })
const security = reactive<SecurityCfg>({ username: 'admin', session_days: 7 })

/** 初始数据灌入完成前关闭自动保存：Object.assign 本身会触发 watch，不能让「进页面」变成一次保存 */
const ready = ref(false)

onMounted(async () => {
  const d = await getSettings()
  Object.assign(search, d.search)
  Object.assign(notify, d.notify)
  Object.assign(qms, d.qms)
  Object.assign(litepan, d.litepan || { enabled: false, webhook_url: '', apikey: '', source: '' })
  Object.assign(security, d.security)
  mediaBackend.value = d.media?.backend || 'qms'
  // QMS 引擎状态胶囊（语义同搜索页的 PanSou 在线/离线）
  getQmsHealth().then((h) => (qmsHealth.value = h)).catch(() => (qmsHealth.value = { ok: false, message: '检测失败' }))
  refreshPansouHealth()
  if (mediaBackend.value === 'litepan') refreshLpHealth()
  // watch 回调不是同步执行的（flush: 'pre' 排队等当前同步代码跑完），
  // 必须等这一拍过去再放行，否则灌初值会触发「已自动保存」
  await nextTick()
  ready.value = true
  // 灌入的初值就是「已保存状态」：登记快照，之后没实际改动不会触发保存+toast
  _savedSnapshots.search = JSON.stringify(search)
  _savedSnapshots.notify = JSON.stringify(notify)
  _savedSnapshots.qms = JSON.stringify(qms)
  _savedSnapshots.litepan = JSON.stringify(litepan)
})

/* ===== 三个配置 tab 统一防抖自动保存 =====
 * 停止输入 2s 后写库；失焦立即写库（flushSave）。自动保存是静默的——弹「成功」
 * 反而打扰（手动输入地址一路弹），只有失败才提示。
 * watch 在 onMounted 灌入初值时也会触发，所以用 ready 挡住首跑。 */
const _saveTimers: Record<string, number> = {}
/** 每组配置最近一次成功保存的内容快照，变了才保存 */
const _savedSnapshots: Record<string, string> = {}
/** 待保存的动作（key → 快照+fn）：失焦 flush 时取最新一个执行 */
const _pending: Record<string, { snap: string; doSave: () => Promise<unknown> }> = {}
function debouncedSave(key: string, val: unknown, doSave: () => Promise<unknown>) {
  const snap = JSON.stringify(val)
  if (snap === _savedSnapshots[key]) return
  _pending[key] = { snap, doSave }
  window.clearTimeout(_saveTimers[key])
  _saveTimers[key] = window.setTimeout(() => void flushSave(key), 2000)
}

/** 立刻保存某组待保存的配置（输入框 @blur 调；没待保存就是空操作） */
async function flushSave(key: string) {
  window.clearTimeout(_saveTimers[key])
  const p = _pending[key]
  if (!p) return
  delete _pending[key]
  try {
    await p.doSave()
    _savedSnapshots[key] = p.snap
  } catch {
    message.error('自动保存失败，请重试')
  }
}

watch(search, (v) => {
  if (ready.value) debouncedSave('search', v, () => saveSearchSrc({ ...v }))
})
watch(notify, (v) => {
  if (ready.value) debouncedSave('notify', v, () => saveNotify({ ...v }))
})
watch(qms, (v) => {
  // hosts 空行（ip/host 全空）不入库：占位行只是输入辅助，落库只留填了内容的
  if (ready.value) debouncedSave('qms', v, () => saveQms({ ...v, tmdb_hosts: v.tmdb_hosts.filter((h) => h.ip.trim() || h.host.trim()) }))
})
watch(litepan, (v) => {
  if (ready.value) debouncedSave('litepan', v, () => saveLitePan({ ...v }))
})

/* ===== tab1 搜索源 ===== */
/** PanSou 在线胶囊（语义同 QMS/LitePan 引擎胶囊）：进页拉一次，点「测试连通」后同步 */
const pansouHealth = ref<{ ok: boolean; message?: string } | null>(null)
function refreshPansouHealth() {
  getEngineHealth()
    .then((h) => (pansouHealth.value = { ok: h.ok, message: h.ok ? `${h.ms ?? 0} ms · ${h.plugins ?? 0} 插件` : h.message }))
    .catch(() => (pansouHealth.value = { ok: false, message: '检测失败' }))
}
/* 缓存时长固定 30 分钟，不开放给用户选——选项只留开/关，时长写进旁边说明 */
const CACHE_OPTS = [
  { value: 'on', label: '开启' },
  { value: 'off', label: '关闭' },
]
const testing = ref(false)
async function onTestPansou() {
  if (!search.pansou_url) {
    message.warning('请先填写 PanSou 地址')
    return
  }
  testing.value = true
  try {
    const r = await testPansou(search.pansou_url)
    // 后端连通失败也是 200 + {ok:false}，必须看 ok 字段，不能 promise 不抛就当成功
    pansouHealth.value = { ok: r.ok, message: r.ok ? `${r.ms} ms` : (r.message || '离线') }
    if (r.ok) message.success(`连通正常，响应 ${r.ms} ms`)
    else message.error(`连通失败：${r.message || '请检查地址'}`, 5)
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(detail || '连通失败，请检查地址', 5)
  } finally {
    testing.value = false
  }
}

/* ===== tab3 推送通知 ===== */
function onNotifySw(v: boolean | string | number) {
  message.success(v ? '推送已开启' : '推送已关闭')
}
async function onTestSendkey() {
  if (!notify.sendkey) {
    message.warning('请先填写 SendKey')
    return
  }
  try {
    // 后端发送失败也是 200 + {ok:false,message}，不抛异常，必须看 ok
    const r = await testSendkey(notify.sendkey)
    if (r.ok) message.success(r.message || '测试消息已发送')
    else message.error(r.message || '发送失败，请检查 SendKey', 5)
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(detail || '发送失败，请检查 SendKey', 5)
  }
}

/* ===== tab4 联动后端 ===== */
const ONOFF_OPTS = [
  { value: 'on', label: '开启' },
  { value: 'off', label: '关闭' },
]
/** 联动后端选择（qms/litepan）：切 litepan 后转存完成只发 Webhook，整理由 LitePan 规则自理 */
const mediaBackend = ref<'qms' | 'litepan'>('qms')
const MEDIA_OPTS = [
  { value: 'qms', label: 'QMS 全流程（刮削 / STRM，由 PanKeeper 跟踪结果）' },
  { value: 'litepan', label: 'LitePan（转存完 Webhook 通知，后续整理由 LitePan 自理）' },
]
function onMediaBackend(v: 'qms' | 'litepan') {
  // select 是 :value 绑定不是 v-model：本地值必须自己更新，否则下拉显示与下方表单都不切
  mediaBackend.value = v
  saveMediaBackend(v)
  message.info(v === 'qms' ? '已切换到 QMS 全流程联动' : '已切换到 LitePan 模式：转存完成后仅 Webhook 通知，QMS/STRM 流程停用')
}

/** LitePan 启用开关：地址没填不让开；填了要测连通，不通弹回（对齐 QMS 门槛语义） */
async function onLitePanEnabled(v: unknown) {
  if (v !== 'on') {
    litepan.enabled = false
    return
  }
  if (!litepan.webhook_url.trim()) {
    litepan.enabled = false
    message.warning('还没填写 Webhook 地址，开启不了联动——先填地址再测连通', 5)
    return
  }
  litepanTesting.value = true
  try {
    const r = await testLitePan(litepan.webhook_url, litepan.apikey)
    if (!r.ok) {
      litepan.enabled = false
      message.error(`LitePan 连接不通（${r.message || '检查地址或 API Key'}），未开启联动`, 5)
      return
    }
    litepan.enabled = true
    message.success('LitePan 连通正常，联动已开启')
  } catch {
    litepan.enabled = false
    message.error('LitePan 连接失败，未开启联动', 5)
  } finally {
    litepanTesting.value = false
  }
}

/** LitePan 在线胶囊（语义同 QMS 引擎胶囊）：进页拉一次，点「测试」后同步 */
const lpHealth = ref<{ ok: boolean; message?: string } | null>(null)
function refreshLpHealth() {
  getLitePanHealth().then((h) => (lpHealth.value = h)).catch(() => (lpHealth.value = { ok: false, message: '检测失败' }))
}
const litepanTesting = ref(false)
async function onTestLitePan() {
  if (!litepan.webhook_url) {
    message.warning('请先填写 Webhook 地址')
    return
  }
  litepanTesting.value = true
  try {
    // 传「输入框正在编辑的值」——不等自动保存，点测试就测当前填的；掩码 key 后端自己回落
    const r = await testLitePan(litepan.webhook_url, litepan.apikey)
    lpHealth.value = { ok: r.ok, message: r.ok ? '在线' : (r.message || '离线') }
    if (r.ok) message.success(r.message || 'LitePan 连通正常')
    else message.error(r.message || 'LitePan 连接失败', 5)
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(detail || 'LitePan 连接失败', 5)
  } finally {
    litepanTesting.value = false
  }
}

const qmsHealth = ref<{ ok: boolean; message?: string } | null>(null)
async function onQmsEnabled(v: unknown) {
  // 开关有门槛：地址没填不让开；填了也要先实测连通，不通照样拒绝（显示值自动弹回）
  if (v !== 'on') {
    qms.enabled = false
    return
  }
  if (!qms.url.trim()) {
    message.warning('还没填写 QMS 地址，开启不了联动——先填地址再测连通', 5)
    return
  }
  qmsTesting.value = true
  try {
    const r = await testQms(qms.url, qms.apikey)
    if (!r.ok) {
      message.error(`QMS 连接不通（${r.message || '检查地址或 API Key'}），未开启联动`, 5)
      return
    }
    qms.enabled = true
    message.success('QMS 连通正常，联动已开启')
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(detail || 'QMS 连接不通，未开启联动', 5)
  } finally {
    qmsTesting.value = false
  }
}
const qmsTesting = ref(false)
async function onTestQms() {
  if (!qms.url) {
    message.warning('请先填写 QMS 地址')
    return
  }
  qmsTesting.value = true
  try {
    // url 与 apikey 都传「输入框正在编辑的值」——不等自动保存，点测试就测当前填的
    const r = await testQms(qms.url, qms.apikey)
    // 后端 200 也代表"测完"，连通与否看 ok 字段，不能无条件报成功
    if (r.ok) message.success(r.message || 'QMS 连接正常')
    else message.error(r.message || 'QMS 连接失败')
    // 状态胶囊同步：测的就是草稿值，比胶囊自己的定时探测更即时
    qmsHealth.value = { ok: r.ok, message: r.ok ? '在线' : r.message }
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(detail || 'QMS 连接失败', 5)
    qmsHealth.value = { ok: false, message: detail || '离线' }
  } finally {
    qmsTesting.value = false
  }
}

/* ===== tab2 代理配置（TMDB，字段从搜索源 tab 迁来） ===== */
const TMDB_MODE_OPTS = [
  { value: 'proxy', label: '代理模式' },
  { value: 'host', label: 'Host 模式' },
]
function addHost() {
  qms.tmdb_hosts.push({ ip: '', host: '' })
}
function removeHost(i: number) {
  qms.tmdb_hosts.splice(i, 1)
  flushSave('qms')
}
const tmdbTesting = ref(false)
async function onTestTmdb() {
  // mode/proxy/hosts/skip_tls 传「输入框正在编辑的值」不等自动保存；掩码 key 原样传，后端回落已保存配置
  tmdbTesting.value = true
  try {
    const r = await testTmdb({
      mode: qms.tmdb_mode,
      proxy: qms.tmdb_proxy,
      hosts: qms.tmdb_hosts.filter((h) => h.ip.trim() && h.host.trim()),
      skip_tls: qms.tmdb_skip_tls,
      api_key: qms.tmdb_api_key,
    })
    // 后端连通失败也是 200 + {ok:false}，必须看 ok 字段；多候选时 message 已含逐 IP 耗时
    if (r.ok) {
      message.success(r.results && r.results.length > 1 ? r.message || 'TMDB 连通正常' : `${r.message || 'TMDB 连通正常'}${r.ms != null ? ` · ${r.ms} ms` : ''}`)
      // host 模式：把最快的 IP 置顶为生效行（运行时按行序生效、失败自动换下一行）
      if (qms.tmdb_mode === 'host' && r.winner) {
        const i = qms.tmdb_hosts.findIndex((h) => h.ip.trim() === r.winner)
        if (i > 0) {
          const [row] = qms.tmdb_hosts.splice(i, 1)
          qms.tmdb_hosts.unshift(row)
          flushSave('qms')
          message.info(`已把最快的 ${r.winner} 置顶为生效行`)
        }
      }
    } else {
      message.error(r.message || 'TMDB 连通失败', 5)
    }
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(detail || 'TMDB 连通失败', 5)
  } finally {
    tmdbTesting.value = false
  }
}

/* ===== tab5 账号安全 ===== */
const SESSION_OPTS = [
  { value: 7, label: '7 天' },
  { value: 30, label: '30 天' },
  { value: 1, label: '1 天' },
]
const pw = reactive({ old: '', next: '', confirm: '' })
async function onSaveSecurity() {
  // 填了新密码才校验密码三件套；只改会话有效期可直接保存
  if (pw.next || pw.confirm) {
    if (!pw.old) {
      message.warning('请输入当前密码')
      return
    }
    if (pw.next.length < 8) {
      message.warning('新密码至少 8 位')
      return
    }
    if (pw.next !== pw.confirm) {
      message.warning('两次输入的新密码不一致')
      return
    }
  }
  try {
    await saveSecurity({
      username: security.username,
      old_password: pw.old,
      new_password: pw.next,
      session_days: security.session_days,
    })
  } catch (e: unknown) {
    // 当前密码错误、用户名冲突等：后端 400 + detail，表单原样保留让用户改
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(detail || '保存失败')
    return
  }
  pw.old = ''
  pw.next = ''
  pw.confirm = ''
  // 账号凭证已变更：清掉本地会话强制回登录页，不拿旧 token 继续待着
  message.success('账号已更新，请重新登录')
  auth.logout()
  router.push({ name: 'login' })
}

/* ===== tab6 头像管理 ===== */
const avatarFile = ref<HTMLInputElement | null>(null)
const avatarBusy = ref(false)

/** 居中裁正方形并压到 256×256 JPEG —— 别把几 MB 的原图塞进数据库 */
async function shrinkImage(file: File): Promise<string> {
  const bitmap = await createImageBitmap(file)
  const SIZE = 256
  const side = Math.min(bitmap.width, bitmap.height)
  const canvas = document.createElement('canvas')
  canvas.width = SIZE
  canvas.height = SIZE
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('canvas 不可用')
  ctx.drawImage(bitmap, (bitmap.width - side) / 2, (bitmap.height - side) / 2, side, side, 0, 0, SIZE, SIZE)
  bitmap.close?.()
  return canvas.toDataURL('image/jpeg', 0.85)
}

async function onPickAvatar(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = '' // 清空，允许连续选同一个文件
  if (!file) return
  if (!file.type.startsWith('image/')) {
    message.warning('请选择图片文件')
    return
  }
  avatarBusy.value = true
  try {
    await auth.saveAvatar(await shrinkImage(file))
    message.success('头像已更新')
  } catch (err: unknown) {
    const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(detail || '头像上传失败')
  } finally {
    avatarBusy.value = false
  }
}

async function onRemoveAvatar() {
  avatarBusy.value = true
  try {
    await auth.removeAvatar()
    message.success('已移除头像')
  } catch {
    message.error('移除失败')
  } finally {
    avatarBusy.value = false
  }
}
</script>

<template>
  <div>
    <div class="card st-card">
      <!-- 胶囊 tab：与全站唯一一套 .tabs 长相一致 -->
      <div class="tabs">
        <div v-for="t in TABS" :key="t.k" :class="{ on: tab === t.k }" @click="tab = t.k">{{ t.label }}</div>
      </div>

      <!-- ===== tab1 搜索源 ===== -->
      <div v-show="tab === 'tb1'">
        <div class="formrow">
          <label>PanSou 地址</label>
          <div>
            <div class="ctl">
              <a-input v-model:value="search.pansou_url" style="width: 300px" placeholder="如 http://127.0.0.1:8000" @blur="flushSave('search')" />
              <a-button :loading="testing" @click="onTestPansou">测试连通</a-button>
              <span class="qms-pill" :class="pansouHealth?.ok ? 'ok' : 'bad'"><i></i>{{ pansouHealth === null ? 'PanSou 状态检测中…' : pansouHealth.ok ? `PanSou 在线 · ${pansouHealth.message}` : `PanSou 离线${pansouHealth.message ? ' · ' + pansouHealth.message : ''}` }}</span>
            </div>
          </div>
        </div>
        <div class="formrow">
          <label>请求超时</label>
          <div class="ctl">
            <a-input-number v-model:value="search.timeout" :min="1" :max="300" style="width: 110px" @blur="flushSave('search')" />
            <span class="muted">秒</span>
          </div>
        </div>
        <div class="formrow">
          <label>结果缓存</label>
          <div class="ctl">
            <a-select v-model:value="search.cache_mode" :options="CACHE_OPTS" style="width: 180px" />
          </div>
        </div>
      </div>

      <!-- ===== tab2 代理配置：TMDB API Key + 连通模式（代理 / host 直连） ===== -->
      <div v-show="tab === 'tb2'">
        <div class="formrow">
          <label>TMDB API Key</label>
          <div class="ctl">
            <a-input-password v-model:value="qms.tmdb_api_key" style="width: 280px" @blur="flushSave('qms')" />
            <a-button :loading="tmdbTesting" @click="onTestTmdb">测试连通</a-button>
            <span class="muted small">转存完成的推送通知用它查封面/剧照（themoviedb.org 免费申请）</span>
          </div>
        </div>
        <div class="formrow">
          <label>连通模式</label>
          <div class="ctl">
            <a-select v-model:value="qms.tmdb_mode" :options="TMDB_MODE_OPTS" style="width: 180px" @change="flushSave('qms')" />
            <span class="muted small">只影响服务端调 TMDB 接口；推送里的图片链接由手机端自己拉取</span>
          </div>
        </div>
        <template v-if="qms.tmdb_mode === 'proxy'">
          <div class="formrow">
            <label>TMDB 代理</label>
            <div class="ctl">
              <a-input v-model:value="qms.tmdb_proxy" style="width: 280px" placeholder="http://127.0.0.1:7890" @blur="flushSave('qms')" />
              <span class="muted small">支持 http / socks5 协议（如 socks5://127.0.0.1:7891），留空直连</span>
            </div>
          </div>
        </template>
        <template v-else>
          <div class="formrow">
            <label>Hosts 映射</label>
            <div>
              <div v-for="(h, i) in qms.tmdb_hosts" :key="i" class="host-row">
                <a-input v-model:value="h.ip" style="width: 180px" placeholder="IP，如 127.0.0.1" @blur="flushSave('qms')" />
                <a-input v-model:value="h.host" style="width: 240px" placeholder="域名，如 api.themoviedb.org" @blur="flushSave('qms')" />
                <a-button danger @click="removeHost(i)"><template #icon><DeleteOutlined /></template>删除</a-button>
              </div>
              <div class="ctl" style="margin-top: 8px">
                <a-button @click="addHost">添加一行</a-button>
                <span class="muted small">左 IP 右域名，TMDB 只查 api.themoviedb.org；同域名多行按顺序生效、连不上自动换下一行，点「测试连通」会把最快的 IP 置顶</span>
              </div>
            </div>
          </div>
          <div class="formrow">
            <label>跳过证书校验</label>
            <div class="ctl">
              <a-switch v-model:checked="qms.tmdb_skip_tls" @change="flushSave('qms')" />
              <span class="muted small">自建反代/中转的证书和域名对不上时打开；公共 CDN 优选 IP 别开</span>
            </div>
          </div>
        </template>
      </div>

      <!-- ===== tab3 推送通知（只服务自动转存） ===== -->
      <div v-show="tab === 'tb3'">
        <div class="formrow">
          <label>启用推送</label>
          <div class="ctl">
            <a-switch v-model:checked="notify.enabled" @change="onNotifySw" />
            <span class="muted small">总开关，关闭后所有推送静默</span>
          </div>
        </div>
        <div class="formrow">
          <label>Server 酱 SendKey</label>
          <div>
            <div class="ctl">
              <a-input-password v-model:value="notify.sendkey" style="width: 320px" placeholder="SCT…" @blur="flushSave('notify')" />
              <a-button @click="onTestSendkey">发送测试</a-button>
              <span class="muted small">按下方「推送时机」开关通知自动/搜索转存与凭据告警</span>
            </div>
            <div class="sc-help">
              <a href="https://sc3.ft07.com/" target="_blank" rel="noopener noreferrer">配置说明</a>
              <span class="muted small">还没配过？先从官网拿到 SendKey 再填这里</span>
            </div>
          </div>
        </div>
        <div class="formrow">
          <label>自定义 Webhook</label>
          <div class="ctl">
            <a-input v-model:value="notify.webhook" style="width: 360px" placeholder="https://你的机器人地址" @blur="flushSave('notify')" />
          </div>
        </div>
        <div class="formrow">
          <label>推送时机</label>
          <div class="ctl st-gap18">
            <a-checkbox v-model:checked="notify.on_auto">自动转存</a-checkbox>
            <a-checkbox v-model:checked="notify.on_search">搜索转存</a-checkbox>
            <a-checkbox v-model:checked="notify.on_cred">凭据过期告警</a-checkbox>
          </div>
        </div>
      </div>

      <!-- ===== tab4 联动后端：顶部选后端，下面按所选显示对应参数 ===== -->
      <div v-show="tab === 'tb4'">
        <div class="st-mig">
          目录关联已迁到<b>「转存配置」</b>（每条目录配）与<b>自动转存任务弹窗</b>（每个任务配）两处，
          这里只保留连接参数与联动总开关。
        </div>
        <div class="formrow">
          <label>联动后端</label>
          <div class="ctl">
            <a-select :value="mediaBackend" :options="MEDIA_OPTS" style="width: 460px" @change="onMediaBackend" />
          </div>
        </div>
        <template v-if="mediaBackend === 'qms'">
          <div class="formrow">
            <label>启用联动</label>
            <div class="ctl">
              <a-select :value="qms.enabled ? 'on' : 'off'" :options="ONOFF_OPTS" style="width: 120px" @change="onQmsEnabled" />
              <span class="qms-pill" :class="qmsHealth?.ok ? 'ok' : 'bad'"><i></i>{{ qmsHealth === null ? 'QMS 状态检测中…' : qmsHealth.ok ? 'QMS 引擎 在线' : `QMS 引擎 离线${qmsHealth.message ? ' · ' + qmsHealth.message : ''}` }}</span>
            </div>
          </div>
          <div class="formrow">
            <label>QMS 地址</label>
            <div class="ctl">
              <a-input v-model:value="qms.url" style="width: 300px" placeholder="请输入 QMS 服务地址" @blur="flushSave('qms')" />
              <a-button :loading="qmsTesting" @click="onTestQms">测试</a-button>
            </div>
          </div>
          <div class="formrow">
            <label>API Key</label>
            <div class="ctl">
              <a-input-password v-model:value="qms.apikey" style="width: 280px" @blur="flushSave('qms')" />
            </div>
          </div>
          <div class="formrow">
            <label>触发动作</label>
            <div class="ctl st-gap18">
              <a-checkbox v-model:checked="qms.act_strm">刮削后生成 STRM</a-checkbox>
              <a-checkbox v-model:checked="qms.act_emby">完成后刷新 Emby</a-checkbox>
            </div>
          </div>
        </template>
        <template v-else>
          <div class="formrow">
            <label>启用联动</label>
            <div class="ctl">
              <a-select :value="litepan.enabled ? 'on' : 'off'" :options="ONOFF_OPTS" style="width: 120px" @change="onLitePanEnabled" />
              <span class="qms-pill" :class="lpHealth?.ok ? 'ok' : 'bad'"><i></i>{{ lpHealth === null ? 'LitePan 状态检测中…' : lpHealth.ok ? 'LitePan 在线' : `LitePan 离线${lpHealth.message ? ' · ' + lpHealth.message : ''}` }}</span>
              <!-- 说明必须待在 .ctl 里：formrow 是 132px+1fr 两列 grid，塞第三列会被挤成竖排 -->
              <span class="muted small">总闸关闭时所有目录的 LitePan 联动都不推送</span>
            </div>
          </div>
          <div class="formrow">
            <label>Webhook 地址</label>
            <div class="ctl">
              <a-input v-model:value="litepan.webhook_url" style="width: 360px" placeholder="http://127.0.0.1:端口/api/open/automation/events" @blur="flushSave('litepan')" />
              <a-button :loading="litepanTesting" @click="onTestLitePan">测试</a-button>
            </div>
          </div>
          <div class="formrow">
            <label>API Key</label>
            <div class="ctl">
              <a-input-password v-model:value="litepan.apikey" style="width: 280px" @blur="flushSave('litepan')" />
              <span class="muted small">LitePan「API Key」页生成</span>
            </div>
          </div>
          <div class="formrow">
            <label>通知来源</label>
            <div class="ctl">
              <a-input v-model:value="litepan.source" style="width: 280px" placeholder="如 PanKeeper" @blur="flushSave('litepan')" />
              <span class="muted small">选填。LitePan 规则按来源精确匹配（区分大小写）；留空则事件不带来源</span>
            </div>
          </div>
          <div class="formrow">
            <label></label>
            <div class="muted small" style="line-height: 1.8">
              转存完成后 PanKeeper 发 Webhook（事件 + 转存目标路径），LitePan 按匹配到的规则自动整理。
              事件名在「转存配置」目录和任务弹窗里按需配，<b>没填事件名就不联动</b>；<br />
              规则的「路径前缀」按转存目标目录配。刮削/STRM 状态由 LitePan 自理，PanKeeper 只推自识别结果。
            </div>
          </div>
        </template>
      </div>

      <!-- ===== tab5 账号安全 ===== -->
      <div v-show="tab === 'tb5'">
        <div class="formrow">
          <label>用户名</label>
          <div class="ctl">
            <a-input v-model:value="security.username" style="width: 260px" />
          </div>
        </div>
        <div class="formrow">
          <label>当前密码</label>
          <div class="ctl">
            <a-input-password v-model:value="pw.old" style="width: 260px" autocomplete="current-password" />
          </div>
        </div>
        <div class="formrow">
          <label>新密码</label>
          <div class="ctl">
            <a-input-password v-model:value="pw.next" style="width: 260px" autocomplete="new-password" />
            <span class="muted small">至少 8 位</span>
          </div>
        </div>
        <div class="formrow">
          <label>确认新密码</label>
          <div class="ctl">
            <a-input-password v-model:value="pw.confirm" style="width: 260px" autocomplete="new-password" />
          </div>
        </div>
        <div class="formrow">
          <label>会话有效期</label>
          <div class="ctl">
            <a-select v-model:value="security.session_days" :options="SESSION_OPTS" style="width: 160px" />
          </div>
        </div>
        <div class="formrow">
          <label></label>
          <div class="ctl">
            <a-button type="primary" @click="onSaveSecurity">保存修改</a-button>
          </div>
        </div>
      </div>

      <!-- ===== tab6 头像管理 ===== -->
      <div v-show="tab === 'tb6'">
        <div class="formrow">
          <label>当前头像</label>
          <div>
            <div class="av-box">
              <img v-if="auth.avatar" :src="auth.avatar" alt="头像" />
              <span v-else>{{ auth.initial }}</span>
            </div>
          </div>
        </div>
        <div class="formrow">
          <label>上传图片</label>
          <div>
            <div class="ctl">
              <input ref="avatarFile" type="file" accept="image/*" class="av-file" @change="onPickAvatar" />
              <a-button :loading="avatarBusy" @click="avatarFile?.click()">选择图片</a-button>
              <a-button v-if="auth.avatar" danger :disabled="avatarBusy" @click="onRemoveAvatar">移除头像</a-button>
            </div>
            <div class="desc">jpg / png / webp 都行。会自动居中裁成正方形、压到 256×256 再存，不占空间。</div>
          </div>
        </div>
      </div>
    </div>


  </div>
</template>

<style scoped>
/* 卡片底部留白收一点：表单行自带 13px 上下 padding，默认 24px 会显得空 */
.st-card {
  padding-bottom: 14px;
}
/* 推送时机 / 触发动作的勾选间距（原型 ctl 内联 gap:18px） */
.st-gap18 {
  gap: 18px;
}
/* QMS tab 顶部的迁移说明条 */
.st-mig {
  font-size: 12.5px;
  color: var(--text3);
  background: var(--surface-2);
  border-radius: var(--r-sm);
  padding: 10px 14px;
  line-height: 1.7;
  margin-bottom: 6px;
}
.st-mig b {
  color: var(--text2);
  font-weight: 500;
}
/* Server 酱「配置说明」外链 */
.sc-help {
  display: flex;
  align-items: center;
  gap: 8px;  margin-top: 6px;
  font-size: 12.5px;
}
.sc-help a {
  color: var(--primary);
  text-decoration: none;
}
.sc-help a:hover { text-decoration: underline; }

/* QMS 引擎状态胶囊（语义同搜索页的 PanSou 在线/离线） */
.qms-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text3);
}
.qms-pill i { width: 7px; height: 7px; border-radius: 50%; flex: none; }
.qms-pill.ok i { background: var(--success); box-shadow: 0 0 6px var(--success); }
.qms-pill.ok { color: var(--text2); }
.qms-pill.bad i { background: var(--error); }

/* Host 模式映射行：卡片框住一左一右 IP/域名（浅底+细边框，随暗色主题自动翻转） */
.host-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  margin-bottom: 10px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
}

/* 头像管理：预览圆（没传图时用与 logo 同套的品牌渐变，不至于难看） */
.av-box {
  width: 84px;
  height: 84px;
  border-radius: 50%;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #7c5cf6, #3b6ef6);
  color: #fff;
  font-size: 34px;
  font-weight: 600;
  box-shadow: 0 2px 10px rgba(60, 80, 200, 0.18);
}
.av-box img { width: 100%; height: 100%; object-fit: cover; display: block; }
/* 原生 file input 藏起来，用按钮触发 */
.av-file { display: none; }

/* ---- 移动端（<768px）：inline 宽度的输入框不许撑破屏；PC 一条不动 ---- */
@media (max-width: 767px) {
  .st-card :deep(.ctl > *) {
    max-width: 100%;
  }
  /* 多控件行（如 PanSou 地址 + 测试按钮）铺满，别撑破屏 */
  .st-card :deep(.ctl) { width: 100%; }
  /* Hosts 映射行窄屏重排：IP/域名各自独占一整行（宽度一致，别 180/240 参差），删除键单独靠右 */
  .host-row { flex-wrap: wrap; row-gap: 8px; }
  .st-card .host-row > :deep(.ant-input) { flex: 1 1 100%; }
  .st-card .host-row > :deep(.ant-btn) { margin-left: auto; }
  /* 6 个胶囊 tab 手机端 3 个一排（拉宽 + 文字居中），别 4+2 参差换行；
     覆盖 pk.css 移动端的 flex: 0 0 auto（那条是给横向滚动场景的） */
  .st-card .tabs { width: 100%; }
  .st-card .tabs > div { flex: 1 1 calc((100% - 8px) / 3); text-align: center; }
}
</style>
