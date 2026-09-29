<script setup lang="ts">
/* 登录页 —— W1「霓虹暗夜」（2026-09-28 用户定稿）：黑底紫/青/粉光斑 + 暗玻璃卡。
 * 单卡版式 PC/手机同一结构（手机近全宽）；页面自身固定深色玻璃风，
 * antd 输入框做了页内玻璃化覆盖，不随全局主题翻面。
 * mock 登录走 auth store（任意输入可登录），真实模式 401/网络错误就地提示。 */
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { useAuthStore } from '@/store/auth'
import { useThemeStore } from '@/store/theme'
import { hydrateAll } from '@/api/bootstrap'

const router = useRouter()
const auth = useAuthStore()
const theme = useThemeStore()

/* 默认账号 admin / admin#123（首启由后端生成，登录后请修改） */
const username = ref('admin')
const password = ref('admin#123')
const loading = ref(false)

async function onLogin() {
  if (loading.value) return
  loading.value = true
  try {
    await auth.login(username.value, password.value)
    hydrateAll() // 启动期未登录时灌注会 401，登录成功这里必须补一次
    message.success('欢迎回来，' + auth.username)
    router.push('/dashboard')
  } catch (e: unknown) {
    // 真实模式：401/网络错误在此提示（mock 模式不会抛）
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(detail || '登录失败，请检查用户名或密码')
  } finally {
    loading.value = false
  }
}

/* 持久化主题目前没有统一的应用入口（store 只有 toggle 时才 apply），
 * 登录页是应用第一入口，这里兜底同步一次（幂等），保证刷新后暗色仍生效。 */
onMounted(() => theme.apply())
</script>

<template>
  <div class="login">
    <!-- 右上角浮动主题切换：玻璃化独立配色，不随页面主题变量翻面 -->
    <button
      class="login-theme-btn"
      :title="theme.isDark ? '切到日间模式' : '切到夜间模式'"
      @click="theme.toggle()"
    >
      {{ theme.isDark ? '☀ 日间' : '☾ 夜间' }}
    </button>

    <!-- 霓虹光斑背景（纯 CSS，零图片资源） -->
    <div class="neon-bg" aria-hidden="true"></div>

    <!-- 居中暗玻璃卡 -->
    <div class="card">
      <div class="brand"><i></i>PanKeeper</div>
      <h1>登录</h1>
      <div class="sub">搜得到，就该存得下。 · 单管理员账号</div>
      <div class="field">
        <label>用户名</label>
        <a-input v-model:value="username" placeholder="admin" @press-enter="onLogin" />
      </div>
      <div class="field">
        <label>密码</label>
        <a-input-password v-model:value="password" placeholder="请输入密码" @press-enter="onLogin" />
      </div>
      <a-button type="primary" class="login-btn" :loading="loading" @click="onLogin">登 录</a-button>
      <div class="hintbox">忘记密码时，家用场景建议直接在服务端重置，不做邮箱找回——为一个人做整套找回链路属于自找麻烦。</div>
    </div>
  </div>
</template>

<style scoped>
.login {
  position: relative;
  min-height: 100vh;
  min-height: 100dvh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px 16px;
  overflow: hidden;
}

/* ===== 霓虹光斑背景：紫 / 青 / 粉三团 + 深空底（W1 定稿，纯 CSS 零资源） ===== */
.neon-bg {
  position: fixed;
  inset: 0;
  z-index: 0;
  pointer-events: none;
  background:
    radial-gradient(42% 55% at 16% 22%, rgba(124, 58, 237, 0.5), transparent 62%),
    radial-gradient(36% 48% at 84% 14%, rgba(34, 211, 238, 0.34), transparent 60%),
    radial-gradient(50% 60% at 66% 96%, rgba(236, 72, 153, 0.36), transparent 62%),
    linear-gradient(158deg, #06060e 8%, #0e1124 55%, #06060f);
}

/* ===== 暗玻璃卡 ===== */
.card {
  position: relative;
  z-index: 1;
  width: min(400px, 100%);
  background: rgba(255, 255, 255, 0.055);
  border: 1px solid rgba(255, 255, 255, 0.13);
  border-radius: 20px;
  padding: 34px 34px 26px;
  box-shadow:
    0 30px 80px rgba(0, 0, 0, 0.55),
    inset 0 1px 0 rgba(255, 255, 255, 0.16);
  backdrop-filter: blur(18px);
  -webkit-backdrop-filter: blur(18px);
  color: #f2f4fb;
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 16px;
  font-weight: 700;
  letter-spacing: 0.3px;
  margin-bottom: 22px;
}
.brand i {
  width: 26px;
  height: 26px;
  border-radius: 8px;
  background: linear-gradient(135deg, #a78bfa, #22d3ee);
  box-shadow: 0 4px 16px rgba(124, 58, 237, 0.5);
}
.card h1 {
  font-size: 24px;
  font-weight: 600;
  margin-bottom: 6px;
}
.sub {
  color: rgba(242, 244, 251, 0.55);
  font-size: 13px;
  margin-bottom: 26px;
}
.field { margin-bottom: 18px; }
.field label {
  display: block;
  margin-bottom: 7px;
  font-size: 13px;
  color: rgba(242, 244, 251, 0.65);
  font-weight: 500;
}

/* antd 输入框玻璃化：登录页是独立视觉世界，浅暗主题都不翻面 */
.card :deep(.ant-input-affix-wrapper),
.card :deep(.ant-input) {
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.16);
  color: #f2f4fb;
  border-radius: 10px;
  transition: border-color 0.2s, box-shadow 0.2s, background 0.2s;
}
/* iOS 聚焦防缩放：真实 input 字号 ≥16px */
.card :deep(.ant-input) {
  font-size: 16px;
  height: 40px;
}
.card :deep(.ant-input-affix-wrapper) { padding: 0 12px; }
.card :deep(.ant-input-affix-wrapper .ant-input) { height: 40px; background: transparent; border: none; }
.card :deep(input::placeholder) { color: rgba(242, 244, 251, 0.35); }
.card :deep(.ant-input-password-icon) { color: rgba(242, 244, 251, 0.45); }
.card :deep(.ant-input-affix-wrapper:hover),
.card :deep(.ant-input:hover) {
  border-color: rgba(255, 255, 255, 0.3);
  background: rgba(255, 255, 255, 0.08);
}
.card :deep(.ant-input-affix-wrapper-focused),
.card :deep(.ant-input-affix-wrapper:focus-within),
.card :deep(.ant-input:focus) {
  border-color: #8b7cf8;
  box-shadow: 0 0 0 3px rgba(124, 58, 237, 0.25);
  background: rgba(255, 255, 255, 0.08);
}

/* 渐变发光登录按钮（W1 定稿：紫→蓝→青） */
.login-btn.ant-btn-primary {
  width: 100%;
  height: 42px;
  font-size: 15px;
  margin-top: 8px;
  border: none;
  border-radius: 10px;
  background: linear-gradient(90deg, #7c3aed, #2563eb 55%, #0891b2);
  box-shadow: 0 8px 26px rgba(124, 58, 237, 0.42);
  transition: filter 0.15s, transform 0.1s;
}
.login-btn.ant-btn-primary:hover { filter: brightness(1.12); }
.login-btn.ant-btn-primary:active { transform: translateY(1px); }

/* 忘记密码说明：玻璃浅条 */
.hintbox {
  margin-top: 20px;
  background: rgba(255, 255, 255, 0.045);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 10px;
  padding: 10px 12px;
  font-size: 12px;
  color: rgba(242, 244, 251, 0.45);
  line-height: 1.7;
}

/* ===== 右上角主题切换：玻璃化 ===== */
.login-theme-btn {
  position: fixed;
  top: calc(env(safe-area-inset-top, 0px) + 14px);
  right: 16px;
  z-index: 20;
  height: 32px;
  padding: 0 12px;
  font-size: 13px;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.16);
  background: rgba(255, 255, 255, 0.07);
  color: #f2f4fb;
  cursor: pointer;
  font-family: inherit;
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  transition: border-color 0.15s, background 0.15s;
}
.login-theme-btn:hover {
  border-color: rgba(255, 255, 255, 0.35);
  background: rgba(255, 255, 255, 0.12);
}

/* ===== 手机（<768px）：卡片近全宽、垫开灵动岛 ===== */
@media (max-width: 767px) {
  .login {
    padding: calc(env(safe-area-inset-top, 0px) + 16px) 16px calc(env(safe-area-inset-bottom, 0px) + 24px);
  }
  .card { padding: 26px 22px 20px; border-radius: 18px; }
}
</style>
