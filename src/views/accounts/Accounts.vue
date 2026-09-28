<script setup lang="ts">
/* =====================================================================
 * 网盘连接 —— 原型 _shell.html data-view="accounts" 的 Vue3 还原。
 * 三张网盘卡：只展示连接状态，绝不回填凭据明文（设计红线）。
 * 状态读写走 accountStore（内存 mock），动作走 api/modules/accounts.ts。
 * ===================================================================== */
import { computed, ref } from 'vue'
import { message } from 'ant-design-vue'
import { accountStore, ACCOUNT_STATUS_VIEW, type AccountRow } from '@/api/mock/accounts'
import { MAIN_ORDER, DRIVE_META } from '@/api/mock/meta'
import { checkAccount, clearAccount, configAccount } from '@/api/modules/accounts'
import type { MainDriveType } from '@/types/model'

/** 卡片按 baidu/quark/115 固定顺序（MAIN_ORDER）铺开，store 变了视图自动跟 */
const rows = computed<AccountRow[]>(() => MAIN_ORDER.map((t) => accountStore.accounts[t]))

/** 状态 → tag 类名/文案/圆点（connected/expired/unset 三态映射） */
function view(status: string) {
  return ACCOUNT_STATUS_VIEW[status]
}

/* ===== 卡片动作 ===== */
const checking = ref<MainDriveType | null>(null)

async function onCheck(a: AccountRow) {
  checking.value = a.type
  try {
    const res = await checkAccount(a.type)
    // 检测结果回写：last_check 已在 api 里更新，status 以返回为准（真实后端可能探成过期）
    accountStore.accounts[a.type].status = res.status
    if (res.kind === 'success') message.success(res.message)
    else if (res.kind === 'warning') message.warning(res.message)
    else message.error(res.message)
  } finally {
    checking.value = null
  }
}

async function onConfig(a: AccountRow) {
  await configAccount(a.type)
  // 原型占位：真实实现里凭据表单只写不读，接口永远不回明文
  message.info(`（原型）弹出「${DRIVE_META[a.type].full}」凭据配置`)
}

async function onClear(a: AccountRow) {
  await clearAccount(a.type)
  message.success(`已清空「${DRIVE_META[a.type].full}」凭据，状态置为未配置`)
}
</script>

<template>
  <div>
    <!-- 网盘卡片网格：未配置的卡整体半透明（.off） -->
    <div class="accgrid">
      <div v-for="a in rows" :key="a.type" class="acc" :class="{ off: a.status === 'unset' }">
        <div class="acchead">
          <div class="accname">
            <span class="chip" :style="{ background: a.color }">{{ a.short }}</span>
            {{ DRIVE_META[a.type].full }}
          </div>
          <span class="tag acc-tag" :class="view(a.status).cls">
            <span class="dot" :class="view(a.status).dot"></span>{{ view(a.status).label }}
          </span>
        </div>
        <div class="kv"><span>凭据类型</span><b>{{ a.cred_kind }}</b></div>
        <div class="kv"><span>上次检测</span><b>{{ a.last_check }}</b></div>
        <div class="kv"><span>默认目标目录</span><b>{{ a.base }}</b></div>
        <p class="accnote">{{ a.note }}</p>
        <div class="accbtns">
          <a-button type="primary" size="small" @click="onConfig(a)">配置凭据</a-button>
          <a-button size="small" :loading="checking === a.type" @click="onCheck(a)">检测连通</a-button>
          <a-popconfirm
            title="确定清空该网盘的凭据？清空后状态变为未配置，需重新绑定。"
            ok-text="清空"
            cancel-text="取消"
            @confirm="onClear(a)"
          >
            <a-button size="small" danger>清空</a-button>
          </a-popconfirm>
        </div>
      </div>
    </div>

    <!-- 设计说明（原型 note-box 原文） -->
    <div class="note-box">
      <b>设计说明</b>
      <ul>
        <li>凭据（cookie / token）在数据库里<b>加密存储</b>，接口只回状态、绝不回填明文——等同于网盘账号密码。</li>
        <li>「检测连通」就是 adapter 的第 5 件事：凭据有效性自检。建议再加每日定时自检，过期在页面上标红。</li>
        <li>115 因为有 p115client 的自动续 cookie 能力，可以做成扫码登录而不是手填。</li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
/* 卡片网格/卡片本体的视觉在 pk.css 共享段（.accgrid/.acc/.acchead/...），这里只补页面私有微调 */
/* 状态 tag 挤在卡头右侧，去掉共享 .tag 的右边距避免顶着卡片边 */
.acchead .tag {
  margin-right: 0;
}
</style>
