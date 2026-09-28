<script setup lang="ts">
/* =====================================================================
 * 系统设置页 —— 原型 _shell.html 设置段（4 个胶囊 tab）的 Vue 移植。
 * - tab1 搜索源 / tab2 推送通知：改完静默保存（原型即改即存内存，无保存按钮）
 * - tab3 QMS 联动：只保留连接参数（目录关联已迁到「转存配置」与自动转存任务弹窗），
 *   带 保存/放弃 两个按钮
 * - tab4 账号安全：改密码（前端先校验）+ 会话有效期
 * ⚠️ 交互契约：推送只服务自动转存，手动转存不接 Server 酱（docs/01）。
 * ===================================================================== */
import { onMounted, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import {
  getSettings,
  saveNotify,
  saveQms,
  saveSearchSrc,
  saveSecurity,
  pushHistory,
  testPansou,
  testQms,
  testSendkey,
} from '@/api/modules/settings'
import type { NotifyCfg, QmsCfg, SearchSrcCfg, SecurityCfg } from '@/api/mock/settings'

/* ===== 胶囊 tab ===== */
const TABS = [
  { k: 'tb1', label: '搜索源' },
  { k: 'tb2', label: '推送通知' },
  { k: 'tb3', label: 'QMS 联动' },
  { k: 'tb4', label: '账号安全' },
] as const
type TabKey = (typeof TABS)[number]['k']
const tab = ref<TabKey>('tb1')

/* ===== 四份表单的本地副本（进页面从 mock store 拷一份，保存时写回） ===== */
const search = reactive<SearchSrcCfg>({
  pansou_url: '',
  timeout: 30,
  cache_mode: 'on',
  def_dir_baidu: '',
  def_dir_quark: '',
})
const notify = reactive<NotifyCfg>({
  enabled: false,
  sendkey: '',
  webhook: '',
  on_done: true,
  on_fail: true,
  on_part: true,
  on_cred: true,
})
const qms = reactive<QmsCfg>({ enabled: true, url: '', apikey: '', act_strm: true, act_emby: true })
const security = reactive<SecurityCfg>({ username: 'admin', session_days: 7 })

onMounted(async () => {
  const d = await getSettings()
  Object.assign(search, d.search)
  Object.assign(notify, d.notify)
  Object.assign(qms, d.qms)
  Object.assign(security, d.security)
})

/* tab1/tab2 没有保存按钮（原型即改即生效），静默写回 mock store */
watch(search, (v) => {
  void saveSearchSrc({ ...v })
})
watch(notify, (v) => {
  void saveNotify({ ...v })
})

/* ===== tab1 搜索源 ===== */
const CACHE_OPTS = [
  { value: 'on', label: '开启 · 30 分钟' },
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
    message.success(`连通正常，响应 ${r.ms} ms`)
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
  await testSendkey(notify.sendkey)
  message.success('测试消息已发送')
}
async function onPushHistory() {
  const h = await pushHistory()
  // 有失败条目，按原型用警示色提示而不是成功色
  message.warning(`最近 50 条推送：已投递 ${h.delivered} 条 / 失败 ${h.failed} 条`)
}

/* ===== tab3 QMS 联动 ===== */
const ONOFF_OPTS = [
  { value: 'on', label: '开启' },
  { value: 'off', label: '关闭' },
]
function onQmsEnabled(v: unknown) {
  qms.enabled = v === 'on'
}
const qmsTesting = ref(false)
async function onTestQms() {
  if (!qms.url) {
    message.warning('请先填写 QMS 地址')
    return
  }
  qmsTesting.value = true
  try {
    await testQms(qms.url)
    message.success('QMS 连接正常')
  } finally {
    qmsTesting.value = false
  }
}
async function onSaveQms() {
  await saveQms({ ...qms })
  message.success('QMS 设置已保存')
}
async function onResetQms() {
  // 放弃修改：从 store 重新拷一份（真实系统为重新 GET）
  const d = await getSettings()
  Object.assign(qms, d.qms)
  message.info('已放弃本次修改')
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
  await saveSecurity({ old_password: pw.old, new_password: pw.next, session_days: security.session_days })
  message.success('密码已更新')
  pw.old = ''
  pw.next = ''
  pw.confirm = ''
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
              <a-input v-model:value="search.pansou_url" style="width: 300px" placeholder="http://" />
              <a-button :loading="testing" @click="onTestPansou">测试连通</a-button>
            </div>
            <div class="desc">改完记得重新测试；后端会用它转发所有搜索请求。</div>
          </div>
        </div>
        <div class="formrow">
          <label>请求超时</label>
          <div class="ctl">
            <a-input-number v-model:value="search.timeout" :min="1" :max="300" style="width: 110px" />
            <span class="muted">秒</span>
          </div>
        </div>
        <div class="formrow">
          <label>结果缓存</label>
          <div class="ctl">
            <a-select v-model:value="search.cache_mode" :options="CACHE_OPTS" style="width: 180px" />
            <span class="muted small">同样的关键词短时间内不重复打 PanSou</span>
          </div>
        </div>
        <div class="formrow">
          <label>默认目标目录</label>
          <div class="ctl">
            <a-input v-model:value="search.def_dir_baidu" style="width: 260px" placeholder="百度网盘" />
            <a-input v-model:value="search.def_dir_quark" style="width: 260px" placeholder="夸克网盘" />
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
              <a-input-password v-model:value="notify.sendkey" style="width: 320px" placeholder="SCT…" />
              <a-button @click="onTestSendkey">发送测试</a-button>
            </div>
            <div class="desc">
              <span class="st-warn">⚠️ 推送只服务<b>自动转存</b>（手动转存不推送，弹窗里没有该项）。</span>
              仅通知自动转存的任务完成 / 失败 / 凭据过期。
            </div>
          </div>
        </div>
        <div class="formrow">
          <label>自定义 Webhook</label>
          <div class="ctl">
            <a-input v-model:value="notify.webhook" style="width: 360px" placeholder="https://你的机器人地址" />
          </div>
        </div>
        <div class="formrow">
          <label>推送时机</label>
          <div class="ctl st-gap18">
            <a-checkbox v-model:checked="notify.on_done">转存完成</a-checkbox>
            <a-checkbox v-model:checked="notify.on_fail">转存失败</a-checkbox>
            <a-checkbox v-model:checked="notify.on_part">部分失败</a-checkbox>
            <a-checkbox v-model:checked="notify.on_cred">凭据过期告警</a-checkbox>
          </div>
        </div>
        <div class="formrow">
          <label>推送历史</label>
          <div class="ctl">
            <a-button size="small" @click="onPushHistory">查看最近 50 条</a-button>
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
          </div>
        </div>
        <div class="formrow">
          <label>QMS 地址</label>
          <div class="ctl">
            <a-input v-model:value="qms.url" style="width: 300px" placeholder="http://" />
            <a-button :loading="qmsTesting" @click="onTestQms">测试</a-button>
          </div>
        </div>
        <div class="formrow">
          <label>API Key</label>
          <div class="ctl">
            <a-input-password v-model:value="qms.apikey" style="width: 280px" />
          </div>
        </div>
        <div class="formrow">
          <label>触发动作</label>
          <div class="ctl st-gap18">
            <a-checkbox v-model:checked="qms.act_strm">刮削后生成 STRM</a-checkbox>
            <a-checkbox v-model:checked="qms.act_emby">完成后刷新 Emby</a-checkbox>
          </div>
        </div>
        <div class="formrow">
          <label></label>
          <div class="ctl">
            <a-button type="primary" @click="onSaveQms">保存设置</a-button>
            <a-button @click="onResetQms">放弃修改</a-button>
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
    </div>

    <div class="note-box">
      <b>设计说明</b>
      <ul>
        <li>
          QMS 这一页就是把在 bdsavepro 里跑通的 <code>trigger_after_transfer</code> 搬过来：
          延迟 10 秒 → 触发刮削 → 轮询状态 → 触发 STRM → 通知 Emby。
        </li>
        <li>每个带「测试」按钮的配置项都真发一次请求并回显结果，比写一堆前端校验管用。</li>
      </ul>
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
/* 推送契约警示（⚠️ 手动转存不推送）——warning 变量浅暗同色，无需另补 dark 覆盖 */
.st-warn {
  color: var(--warning);
}
</style>
