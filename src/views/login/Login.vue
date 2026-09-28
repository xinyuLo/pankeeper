<script setup lang="ts">
/* 登录页 —— 原型 _shell.html #login 区块的 Vue 移植（login-/lform/feats 类名原样保留）。
 * 左侧品牌渐变区 + 右侧登录卡；mock 登录走 auth store（任意输入可登录），
 * 成功后由路由守卫与 router.push 落到 /dashboard。 */
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { useAuthStore } from '@/store/auth'
import { useThemeStore } from '@/store/theme'

const router = useRouter()
const auth = useAuthStore()
const theme = useThemeStore()

/* 默认账号与原型一致（admin / 12345678），真实系统由后端校验 */
const username = ref('admin')
const password = ref('12345678')
const loading = ref(false)

async function onLogin() {
  if (loading.value) return
  loading.value = true
  try {
    auth.login(username.value, password.value)
    message.success('欢迎回来，' + auth.username)
    router.push('/dashboard')
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
    <!-- 右上角浮动主题切换：独立于登录逻辑，写 localStorage 跨页共享 -->
    <button
      class="login-theme-btn"
      :title="theme.isDark ? '切到日间模式' : '切到夜间模式'"
      @click="theme.toggle()"
    >
      {{ theme.isDark ? '☀ 日间' : '☾ 夜间' }}
    </button>

    <!-- 左侧品牌区：圆形装饰靠 ::before/::after -->
    <div class="login-left">
      <div class="brand"><i></i> PanKeeper</div>
      <h2>搜得到，<br />就该存得下。</h2>
      <p>把散布在各处的网盘资源一键收进自己的网盘，转存完成自动整理、生成 STRM、刷新媒体库。</p>
      <div class="feats">
        <div class="feat"><span>✓</span> 聚合搜索，结果自动标注网盘来源</div>
        <div class="feat"><span>✓</span> 按资源类型自动落到对应网盘的目录树</div>
        <div class="feat"><span>✓</span> 后台异步转存，实时进度与分级日志</div>
        <div class="feat"><span>✓</span> 完成后打通 QMS → STRM → Emby 全链路</div>
      </div>
    </div>

    <!-- 右侧登录卡 -->
    <div class="login-right">
      <div class="lform">
        <h1>登录</h1>
        <div class="sub">单管理员账号 · 不开放注册</div>
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
  </div>
</template>

<style scoped>
.login { display: flex; min-height: 100vh; }

/* ---------- 左侧品牌区 ---------- */
.login-left {
  flex: 1;
  background: linear-gradient(150deg, #1668dc 0%, #1677ff 45%, #4dabf7 100%);
  position: relative;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 0 64px;
  color: #fff;
}
/* 两枚半透明大圆装饰，压在文字层下面 */
.login-left::before {
  content: '';
  position: absolute;
  width: 520px;
  height: 520px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.07);
  top: -160px;
  right: -160px;
}
.login-left::after {
  content: '';
  position: absolute;
  width: 340px;
  height: 340px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.05);
  bottom: -120px;
  left: -100px;
}
.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 24px;
  font-weight: 600;
  margin-bottom: 28px;
  position: relative;
}
/* 品牌小方块：半透明白盒 + 白色描边小方框（网盘容器的意象） */
.brand i {
  width: 34px;
  height: 34px;
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.22);
  border: 1px solid rgba(255, 255, 255, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
}
.brand i::after {
  content: '';
  width: 14px;
  height: 14px;
  border: 2.5px solid #fff;
  border-radius: 3px;
}
.login-left h2 {
  font-size: 36px;
  font-weight: 600;
  line-height: 1.3;
  margin-bottom: 16px;
  position: relative;
  letter-spacing: 0.5px;
}
.login-left p {
  font-size: 15px;
  opacity: 0.86;
  line-height: 1.9;
  max-width: 440px;
  position: relative;
}
.feats { margin-top: 36px; display: flex; flex-direction: column; gap: 14px; position: relative; }
.feat { display: flex; align-items: center; gap: 11px; font-size: 14.5px; opacity: 0.94; }
.feat span {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.2);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  flex: none;
}

/* ---------- 右侧登录卡 ---------- */
.login-right {
  width: 460px;
  background: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px;
}
.lform { width: 320px; }
.lform h1 { font-size: 22px; font-weight: 600; margin-bottom: 6px; }
.lform .sub { color: var(--text3); font-size: 13.5px; margin-bottom: 32px; }
.field { margin-bottom: 20px; }
.field label {
  display: block;
  margin-bottom: 7px;
  font-size: 13px;
  color: var(--text2);
  font-weight: 500;
}
.login-btn { width: 100%; height: 40px; font-size: 15px; margin-top: 6px; }
/* 忘记密码说明：家用场景不做找回链路 */
.hintbox {
  background: var(--surface-2);
  border: 1px solid var(--split);
  border-radius: var(--r-sm);
  padding: 10px 12px;
  font-size: 12.5px;
  color: var(--text3);
  line-height: 1.7;
  margin-top: 22px;
}

/* ---------- 右上角主题切换（对应原型 #themeBtn，btn/btn-sm 样式内联在此） ---------- */
.login-theme-btn {
  position: fixed;
  top: 16px;
  right: 16px;
  z-index: 20;
  height: 28px;
  padding: 0 12px;
  font-size: 13px;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: var(--card);
  color: var(--text);
  cursor: pointer;
  font-family: inherit;
  transition: border-color 0.15s, color 0.15s;
}
.login-theme-btn:hover { border-color: var(--primary-h); color: var(--primary-h); }

/* ---------- 暗色（对齐原型 THEME_CSS：右侧换卡片底、渐变换深蓝） ---------- */
html[data-theme='dark'] .login-right { background: var(--card); }
html[data-theme='dark'] .login-left {
  background: linear-gradient(150deg, #0b1220 0%, #12203a 55%, #16304f 100%);
}

/* ---------- 窄屏：原型无登录页断点，仅保证小屏不横向溢出 ---------- */
@media (max-width: 860px) {
  .login-left { padding: 48px 32px; }
  .login-right { width: 100%; }
}
</style>
