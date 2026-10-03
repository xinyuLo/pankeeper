<script setup lang="ts">
/* =====================================================================
 * 系统设置页 —— 原型 _shell.html 设置段（5 个胶囊 tab）的 Vue 移植。
 * - tab1 搜索源 / tab2 推送通知：改完静默保存（原型即改即存内存，无保存按钮）
 * - tab3 QMS 联动：只保留连接参数（目录关联已迁到「转存配置」与自动转存任务弹窗），
 *   与 tab1/tab2 一致走防抖自动保存
 * - tab4 账号安全：改密码（前端先校验）+ 会话有效期
 * - tab5 头像管理：上传/移除头像（前端压缩后存后端）
 * ⚠️ 交互契约：推送只服务自动转存，手动转存不接 Server 酱（docs/01）。
 * ===================================================================== */
import { nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { useAuthStore } from '@/store/auth'
import {
  getQmsHealth,
  getSettings,
  saveNotify,
  saveQms,
  saveSearchSrc,
  saveSecurity,
  testPansou,
  testQms,
  testSendkey,
} from '@/api/modules/settings'
import type { NotifyCfg, QmsCfg, SearchSrcCfg, SecurityCfg } from '@/api/mock/settings'

const router = useRouter()
const auth = useAuthStore()

/* ===== 胶囊 tab ===== */
const TABS = [
  { k: 'tb1', label: '搜索源' },
  { k: 'tb2', label: '推送通知' },
  { k: 'tb3', label: 'QMS 联动' },
  { k: 'tb4', label: '账号安全' },
  { k: 'tb5', label: '头像管理' },
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
const qms = reactive<QmsCfg>({ enabled: true, url: '', apikey: '', tmdb_api_key: '', tmdb_proxy: '', act_strm: true, act_emby: true })
const security = reactive<SecurityCfg>({ username: 'admin', session_days: 7 })

/** 初始数据灌入完成前关闭自动保存：Object.assign 本身会触发 watch，不能让「进页面」变成一次保存 */
const ready = ref(false)

onMounted(async () => {
  const d = await getSettings()
  Object.assign(search, d.search)
  Object.assign(notify, d.notify)
  Object.assign(qms, d.qms)
  Object.assign(security, d.security)
  // QMS 引擎状态胶囊（语义同搜索页的 PanSou 在线/离线）
  getQmsHealth().then((h) => (qmsHealth.value = h)).catch(() => (qmsHealth.value = { ok: false, message: '检测失败' }))
  // watch 回调不是同步执行的（flush: 'pre' 排队等当前同步代码跑完），
  // 必须等这一拍过去再放行，否则灌初值会触发「已自动保存」
  await nextTick()
  ready.value = true
  // 灌入的初值就是「已保存状态」：登记快照，之后没实际改动不会触发保存+toast
  _savedSnapshots.search = JSON.stringify(search)
  _savedSnapshots.notify = JSON.stringify(notify)
  _savedSnapshots.qms = JSON.stringify(qms)
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
  if (ready.value) debouncedSave('qms', v, () => saveQms({ ...v }))
})

/* ===== tab1 搜索源 ===== */
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
    if (r.ok) message.success(`连通正常，响应 ${r.ms} ms`)
    else message.error(`连通失败：${r.message || '请检查地址'}`, 5)
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(detail || '连通失败，请检查地址', 5)
  } finally {
    testing.value = false
  }
}

/* ===== tab2 推送通知 ===== */
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

/* ===== tab3 QMS 联动 ===== */
const ONOFF_OPTS = [
  { value: 'on', label: '开启' },
  { value: 'off', label: '关闭' },
]
/** QMS 引擎状态（进页拉一次；点「测试」成功/失败后同步） */
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

/* ===== tab4 账号安全 ===== */
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

/* ===== tab5 头像管理 ===== */
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
            <span class="muted small">开启后，同样的关键词 30 分钟内不重复打 PanSou</span>
          </div>
        </div>
      </div>

      <!-- ===== tab2 推送通知（只服务自动转存） ===== -->
      <div v-show="tab === 'tb2'">
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

      <!-- ===== tab3 QMS 联动（仅连接参数；目录关联在别处配） ===== -->
      <div v-show="tab === 'tb3'">
        <div class="st-mig">
          目录关联已迁到<b>「转存配置」</b>（每条目录配）与<b>自动转存任务弹窗</b>（每个任务配）两处，
          这里只保留 QMS 连接参数。
        </div>
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
          <label>TMDB API Key</label>
          <div class="ctl">
            <a-input-password v-model:value="qms.tmdb_api_key" style="width: 280px" @blur="flushSave('qms')" />
            <span class="muted small">转存完成的推送通知用它查封面/剧照（themoviedb.org 免费申请）</span>
          </div>
        </div>
        <div class="formrow">
          <label>TMDB 代理</label>
          <div class="ctl">
            <a-input v-model:value="qms.tmdb_proxy" style="width: 280px" placeholder="http://192.168.2.77:7890" @blur="flushSave('qms')" />
            <span class="muted small">服务端连不上 TMDB 时填，留空直连</span>
          </div>
        </div>
        <div class="formrow">
          <label>触发动作</label>
          <div class="ctl st-gap18">
            <a-checkbox v-model:checked="qms.act_strm">刮削后生成 STRM</a-checkbox>
            <a-checkbox v-model:checked="qms.act_emby">完成后刷新 Emby</a-checkbox>
          </div>
        </div>
      </div>

      <!-- ===== tab4 账号安全 ===== -->
      <div v-show="tab === 'tb4'">
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

      <!-- ===== tab5 头像管理 ===== -->
      <div v-show="tab === 'tb5'">
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
}
</style>
