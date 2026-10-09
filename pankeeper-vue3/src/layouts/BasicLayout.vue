<script setup lang="ts">
/* 主框架：侧栏（分组折叠菜单 + 用户脚）+ 顶栏（标题 + 夜间切换）+ 内容区 + 队列浮标。
 * 分组折叠状态存 localStorage `pk-nav`（记分组 key，结构变动不错位）；
 * 路由高亮时只强制展开所在分组，不写存储（用户手动收起的手感优先）。 */
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  HomeOutlined,
  SwapOutlined,
  SearchOutlined,
  FileTextOutlined,
  FolderOutlined,
  ClockCircleOutlined,
  HistoryOutlined,
  CloudOutlined,
  DatabaseOutlined,
  FolderOpenOutlined,
  FieldTimeOutlined,
  SettingOutlined,
  AppstoreOutlined,
  BarChartOutlined,
  LogoutOutlined,
  ProfileOutlined,
  NotificationOutlined,
} from '@ant-design/icons-vue'
import { useAuthStore } from '@/store/auth'
import { useThemeStore } from '@/store/theme'
import { AUTO_TITLE, type AutoType } from '@/router'
import QueueBadge from '@/queue/QueueBadge.vue'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const theme = useThemeStore()

const pageTitle = computed(() => {
  if (route.name === 'auto') return AUTO_TITLE[route.params.type as AutoType] || '自动转存'
  return (route.meta.title as string) || ''
})

interface MenuItem {
  key: string
  label: string
  icon: any
  color?: string
}

const groups: { key: string; label: string; icon: any; children: MenuItem[] }[] = [
  {
    key: 'transfer',
    label: '转存中心',
    icon: SwapOutlined,
    children: [
      { key: 'search', label: '搜索转存', icon: SearchOutlined },
      { key: 'default-dir', label: '转存配置', icon: FolderOutlined },
    ],
  },
  {
    key: 'auto',
    label: '自动转存',
    icon: ClockCircleOutlined,
    children: [
      { key: 'auto-baidu', label: '百度网盘', icon: CloudOutlined, color: '#1677ff' },
      { key: 'auto-quark', label: '夸克网盘', icon: CloudOutlined, color: '#13c2c2' },
      { key: 'auto-115', label: '115 网盘', icon: CloudOutlined, color: '#722ed1' },
    ],
  },
  {
    key: 'logs',
    label: '日志管理',
    icon: ProfileOutlined,
    children: [
      { key: 'records', label: '搜索历史', icon: FileTextOutlined },
      { key: 'auto-history', label: '转存历史', icon: HistoryOutlined },
      { key: 'push-logs', label: '推送历史', icon: NotificationOutlined },
      { key: 'drive-logs', label: '请求日志', icon: BarChartOutlined },
    ],
  },
  {
    key: 'sys',
    label: '系统管理',
    icon: SettingOutlined,
    children: [
      { key: 'accounts', label: '网盘连接', icon: DatabaseOutlined },
      { key: 'cache-config', label: '缓存配置', icon: FolderOpenOutlined },
      { key: 'queue-config', label: '队列配置', icon: FieldTimeOutlined },
      { key: 'settings', label: '系统设置', icon: SettingOutlined },
    ],
  },
]

const activeKey = computed(() => {
  if (route.name === 'auto') return 'auto-' + route.params.type
  return (route.name as string) || 'dashboard'
})

// 分组折叠：从 localStorage 恢复，默认全展开
const openGroups = ref<Set<string>>(new Set(['transfer', 'auto', 'logs', 'sys']))
try {
  const saved = JSON.parse(localStorage.getItem('pk-nav') || 'null')
  if (saved && saved.length) openGroups.value = new Set(saved as string[])
} catch {
  /* ignore */
}

function toggleGroup(key: string) {
  const s = new Set(openGroups.value)
  if (s.has(key)) s.delete(key)
  else s.add(key)
  openGroups.value = s
  try {
    localStorage.setItem('pk-nav', JSON.stringify([...s]))
  } catch {
    /* ignore */
  }
}

// 路由变化：激活项所在分组强制展开（不写存储）
watch(activeKey, (key) => {
  const g = groups.find((x) => x.children.some((c) => c.key === key))
  if (g && !openGroups.value.has(g.key)) {
    const s = new Set(openGroups.value)
    s.add(g.key)
    openGroups.value = s
  }
})

function go(item: MenuItem) {
  if (item.key.startsWith('auto-')) {
    router.push('/auto/' + item.key.replace('auto-', ''))
  } else {
    router.push('/' + item.key)
  }
}

function logout() {
  auth.logout()
  router.push('/login')
}

/* ===== 移动端（<768px）：底部标签栏 + 「更多」面板 =====
 * PC 上这两个组件 display:none，桌面布局一根毛都不动。
 * 高频页（首页/搜索/记录/自动）进底栏，低频页收进「更多」底部面板。 */
const moreOpen = ref(false)
interface TabSub {
  label: string
  icon: any
  to: string
}
interface TabItem {
  key: string
  label: string
  icon: any
  to?: string
  /** 子菜单（2026-10-08 用户定稿）：点击弹出多项选择，而不是直接跳单页 */
  sub?: TabSub[]
}
const tabs: TabItem[] = [
  { key: 'dashboard', label: '首页', icon: HomeOutlined, to: '/dashboard' },
  { key: 'search', label: '搜索', icon: SearchOutlined, to: '/search' },
  {
    key: 'records',
    label: '记录',
    icon: FileTextOutlined,
    sub: [
      { label: '搜索历史', icon: FileTextOutlined, to: '/records' },
      { label: '转存历史', icon: HistoryOutlined, to: '/auto/history' },
      { label: '推送历史', icon: NotificationOutlined, to: '/push-logs' },
    ],
  },
  {
    key: 'auto',
    label: '自动',
    icon: ClockCircleOutlined,
    sub: [
      { label: '百度网盘', icon: CloudOutlined, to: '/auto/baidu' },
      { label: '夸克网盘', icon: CloudOutlined, to: '/auto/quark' },
      { label: '115 网盘', icon: CloudOutlined, to: '/auto/115' },
    ],
  },
  { key: 'more', label: '更多', icon: AppstoreOutlined },
]
const activeTab = computed(() => {
  if (moreOpen.value) return 'more'
  // 子菜单型 tab：当前路由落在它的子项里就点亮它（如 /push-logs 点亮「记录」）
  const withSub = tabs.find((x) => x.sub?.some((sub) => sub.to === route.path))
  if (withSub) return withSub.key
  if (route.name === 'auto') return 'auto'
  return (route.name as string) || 'dashboard'
})

/** 子菜单展开中的 tab key（点同 tab 收起；点其他 tab 切走） */
const subOpen = ref('')

function tapTab(t: TabItem) {
  if (t.key === 'more') {
    moreOpen.value = !moreOpen.value
    subOpen.value = ''
    return
  }
  moreOpen.value = false
  if (t.sub) {
    subOpen.value = subOpen.value === t.key ? '' : t.key
    return
  }
  subOpen.value = ''
  if (route.name !== t.key) router.push(t.to!)
}

function goSub(sub: TabSub) {
  subOpen.value = ''
  moreOpen.value = false
  router.push(sub.to)
}

/* 「更多」面板只收底栏没有的入口（转存配置/三网盘自动/系统管理四页），跳转复用 go() */
const moreMenu: { key: string; label: string; items: MenuItem[] }[] = [
  {
    key: 'transfer',
    label: '转存中心',
    items: [{ key: 'default-dir', label: '转存配置', icon: FolderOutlined }],
  },
  {
    key: 'auto',
    label: '自动转存',
    items: [
      { key: 'auto-baidu', label: '百度网盘', icon: CloudOutlined, color: '#1677ff' },
      { key: 'auto-quark', label: '夸克网盘', icon: CloudOutlined, color: '#13c2c2' },
      { key: 'auto-115', label: '115 网盘', icon: CloudOutlined, color: '#722ed1' },
    ],
  },
  {
    key: 'logs',
    label: '日志管理',
    items: [
      { key: 'records', label: '搜索历史', icon: FileTextOutlined },
      { key: 'auto-history', label: '转存历史', icon: HistoryOutlined },
      { key: 'push-logs', label: '推送历史', icon: NotificationOutlined },
      { key: 'drive-logs', label: '请求日志', icon: BarChartOutlined },
    ],
  },
  {
    key: 'sys',
    label: '系统管理',
    items: [
      { key: 'accounts', label: '网盘连接', icon: DatabaseOutlined },
      { key: 'cache-config', label: '缓存配置', icon: FolderOpenOutlined },
      { key: 'queue-config', label: '队列配置', icon: FieldTimeOutlined },
      { key: 'settings', label: '系统设置', icon: SettingOutlined },
    ],
  },
]

// 路由一变就收面板（从面板跳页后面板不能盖在新页面上）
watch(
  () => route.fullPath,
  () => {
    moreOpen.value = false
  },
)
</script>

<template>
  <div class="layout">
    <aside class="sidebar">
      <div class="logo"><i></i> PanKeeper</div>
      <nav class="menu">
        <a class="menu-item" :class="{ on: activeKey === 'dashboard' }" @click.prevent="router.push('/dashboard')">
          <HomeOutlined class="lico" />首页
        </a>

        <div v-for="g in groups" :key="g.key" class="nav-group" :class="{ open: openGroups.has(g.key) }">
          <button class="nav-hd" type="button" @click="toggleGroup(g.key)">
            <component :is="g.icon" class="lico" />{{ g.label }}
            <svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6" /></svg>
          </button>
          <div class="nav-sub"><div>
            <a
              v-for="c in g.children"
              :key="c.key"
              class="menu-item"
              :class="{ on: activeKey === c.key }"
              @click.prevent="go(c)"
            >
              <component :is="c.icon" class="lico" :style="c.color ? { color: c.color } : undefined" />{{ c.label }}
            </a>
          </div></div>
        </div>
      </nav>
      <div class="side-foot">
        <div class="who">
          <div class="avatar"><img v-if="auth.avatar" :src="auth.avatar" alt="头像" /><span v-else>{{ auth.initial }}</span></div>
          <div style="flex: 1; min-width: 0">
            <div style="font-size: 13.5px; font-weight: 500">{{ auth.username }}</div>
            <div class="small muted">已登录</div>
          </div>
        </div>
        <button class="logout-btn" type="button" @click="logout"><LogoutOutlined /> 退出登录</button>
      </div>
    </aside>

    <div class="pagehead">
      <h2>{{ pageTitle }}</h2>
      <div style="display: flex; align-items: center; gap: 16px">
        <button
          class="theme-btn"
          :title="theme.isDark ? '切到日间模式' : '切到夜间模式'"
          @click="theme.toggle()"
        >
          {{ theme.isDark ? '☀ 日间' : '☾ 夜间' }}
        </button>
        <span class="small muted pc-ver">v0.1.0</span>
      </div>
    </div>

    <div class="content">
      <div class="view">
        <router-view />
      </div>
    </div>

    <QueueBadge />

    <!-- ===== 以下为移动端专用（<768px 才显示，PC display:none） ===== -->
    <nav class="m-tabbar">
      <div v-for="t in tabs" :key="t.key" class="m-tab-wrap">
        <Transition name="msheet">
          <div v-if="t.sub && subOpen === t.key" class="m-submenu">
            <button
              v-for="sub in t.sub"
              :key="sub.to"
              type="button"
              class="m-submenu-item"
              :class="{ on: route.path === sub.to }"
              @click="goSub(sub)"
            >
              <component :is="sub.icon" class="m-submenu-ico" />
              <span>{{ sub.label }}</span>
            </button>
          </div>
        </Transition>
        <button
          type="button"
          class="m-tab"
          :class="{ on: activeTab === t.key }"
          @click="tapTab(t)"
        >
          <component :is="t.icon" class="m-tab-ico" />
          <span>{{ t.label }}</span>
        </button>
      </div>
    </nav>

    <Transition name="msheet">
      <div v-if="moreOpen" class="m-mask" @click.self="moreOpen = false">
        <div class="m-sheet">
          <div class="m-sheet-grab"></div>
          <div class="m-user">
            <div class="avatar"><img v-if="auth.avatar" :src="auth.avatar" alt="头像" /><span v-else>{{ auth.initial }}</span></div>
            <div style="flex: 1; min-width: 0">
              <div style="font-size: 13.5px; font-weight: 500">{{ auth.username }}</div>
              <div class="small muted">已登录</div>
            </div>
            <button class="m-logout" type="button" @click="logout">退出登录</button>
          </div>
          <div v-for="g in moreMenu" :key="g.key" class="m-group">
            <div class="m-group-hd">{{ g.label }}</div>
            <button v-for="c in g.items" :key="c.key" type="button" class="m-item" @click="go(c)">
              <component :is="c.icon" class="m-item-ico" :style="c.color ? { color: c.color } : undefined" />
              {{ c.label }}
              <svg class="m-item-chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18l6-6-6-6" /></svg>
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.layout { height: 100vh; overflow: hidden; }

.sidebar {
  position: fixed;
  left: 0;
  top: 0;
  bottom: 0;
  width: 212px;
  /* 高级浅侧栏（Linear/Vercel 风）：近白中性底 + 深色选中胶囊，靠字色和胶囊分层 */
  background: linear-gradient(180deg, #fbfbfc 0%, #f7f7f9 100%);
  border-right: 1px solid #ececef;
  color: #1f2329;
  border-right: 1px solid var(--split);
  display: flex;
  flex-direction: column;
}
.logo {
  padding: 0 20px;
  height: 60px;
  font-size: 16.5px;
  font-weight: 600;
  color: #1f2329;
  display: flex;
  transition: color 0.2s;
  align-items: center;
  gap: 10px;
  border-bottom: 1px solid var(--split);
  flex-shrink: 0;
}
.logo i {
  width: 26px;
  height: 26px;
  border-radius: 7px;
  /* 与新图标（靛蓝玻璃 + P 字标）同一套品牌语言 */
  background: linear-gradient(135deg, #7c5cf6, #3b6ef6);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.logo i::after {
  content: 'P';
  color: #fff;
  font-size: 15px;
  font-weight: 700;
  line-height: 1;
  font-family: -apple-system, 'Segoe UI', 'PingFang SC', sans-serif;
}

.menu { padding: 10px; flex: 1; overflow: auto; }
.menu-item,
.menu .nav-hd {
  position: relative; /* 选中态的左侧竖条定位基准 */
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 0 12px;
  height: 38px;
  border-radius: 8px;
  color: #5c6066;
  text-decoration: none;
  font-size: 13.5px;
  border: none;
  background: none;
  cursor: pointer;
  font-family: inherit;
  text-align: left;
  transition: background 0.18s, color 0.18s;
}
.menu .nav-hd { font-weight: 500; color: #8a8f98; margin-top: 6px; font-size: 13px; }
.menu-item :deep(.lico),
.menu .nav-hd .lico { font-size: 16px; opacity: 0.85; }
/* 悬停：与选中同色系的极淡底，明确「比选中轻一档」 */
.menu-item:hover,
.menu .nav-hd:hover { background: rgba(124, 58, 237, 0.06); color: #1f2329; }
/* 按下：再加一档——原来点下去毫无反馈，手感发虚 */
.menu-item:active,
.menu .nav-hd:active { background: rgba(124, 58, 237, 0.14); }

/* 选中：淡紫底 + 左侧竖条。
 * 原先的 1px 内描边 + 外阴影会把整条框成一个「浮起来的盒子」，圆角配描边显得很闷，
 * 也跟这里注释写的设计意图（靠字色和背景分层）不符；改成纯色底 + 竖条后层次更轻。 */
.menu-item::before {
  content: '';
  position: absolute;
  left: 0;
  top: 50%;
  width: 3px;
  height: 18px;
  border-radius: 0 3px 3px 0;
  background: linear-gradient(180deg, #8b5cf6, #6d28d9);
  transform: translateY(-50%) scaleY(0);
  transform-origin: center;
  transition: transform 0.2s cubic-bezier(0.22, 0.61, 0.36, 1);
}
.menu-item.on { background: rgba(124, 58, 237, 0.1); color: #6d28d9; font-weight: 600; }
.menu-item.on::before { transform: translateY(-50%) scaleY(1); }
.menu-item.on :deep(.lico) { opacity: 1; color: #7c3aed; }
html[data-theme='dark'] .menu-item,
html[data-theme='dark'] .menu .nav-hd { color: rgba(255, 255, 255, 0.6); }
html[data-theme='dark'] .menu .nav-hd { color: rgba(255, 255, 255, 0.5); }
html[data-theme='dark'] .menu-item:hover,
html[data-theme='dark'] .menu .nav-hd:hover { background: rgba(139, 92, 246, 0.13); color: #fff; }
html[data-theme='dark'] .menu-item:active,
html[data-theme='dark'] .menu .nav-hd:active { background: rgba(139, 92, 246, 0.2); }
/* 暗色侧栏本身是深色，原来选中项直接铺一块近白 #f7f7f9 过于刺眼，也跟它的 hover 态不属同一体系 */
html[data-theme='dark'] .menu-item.on { background: rgba(139, 92, 246, 0.2); color: #c4b5fd; }
html[data-theme='dark'] .menu-item.on::before { background: linear-gradient(180deg, #a78bfa, #8b5cf6); }
html[data-theme='dark'] .menu-item.on :deep(.lico) { color: #a78bfa; }
.menu .nav-hd .chev { margin-left: auto; width: 14px; height: 14px; opacity: 0.4; transition: transform 0.22s; }
.nav-group.open .nav-hd .chev { transform: rotate(180deg); }
.menu .nav-sub { display: grid; grid-template-rows: 0fr; transition: grid-template-rows 0.24s ease; }
.nav-group.open .nav-sub { grid-template-rows: 1fr; }
.menu .nav-sub > div { overflow: hidden; min-height: 0; }
.menu .nav-sub .menu-item { padding-left: 26px; height: 34px; font-size: 13px; }
.menu .nav-sub .menu-item :deep(.lico) { font-size: 15px; }

.side-foot { padding: 14px 16px; border-top: 1px solid #ececef; flex-shrink: 0; }
.side-foot .small { color: #8a8f98; }
.side-foot .logout-btn {
  background: transparent;
  border-color: #e4e6ea;
  color: #5c6066;
}
html[data-theme='dark'] .side-foot { border-top-color: rgba(255, 255, 255, 0.08); }
html[data-theme='dark'] .side-foot .small { color: rgba(255, 255, 255, 0.4); }
html[data-theme='dark'] .side-foot .logout-btn {
  background: transparent;
  border-color: rgba(255, 255, 255, 0.14);
  color: rgba(255, 255, 255, 0.6);
}
.logout-btn {
  margin-top: 10px;
  width: 100%;
  height: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  font-size: 13px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--card);
  color: var(--text2);
  cursor: pointer;
  transition: all 0.18s;
}
.logout-btn:hover {
  border-color: rgba(255, 77, 79, 0.5);
  color: var(--error);
  background: rgba(255, 77, 79, 0.06);
}
html[data-theme='dark'] .logout-btn { background: transparent; }
.who { display: flex; align-items: center; gap: 10px; }
.avatar {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  overflow: hidden; /* 头像图裁成圆形 */
  background: linear-gradient(135deg, #1677ff, #69c0ff);
  color: #fff;
  font-size: 12.5px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 500;
  flex-shrink: 0;
}
.avatar img { width: 100%; height: 100%; object-fit: cover; display: block; }

.pagehead {
  position: fixed;
  left: 212px;
  right: 0;
  top: 0;
  height: 60px;
  background: rgba(255, 255, 255, 0.86);
  backdrop-filter: blur(8px);
  border-bottom: 1px solid var(--split);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 28px;
  z-index: 5;
}
.pagehead h2 { font-size: 16px; font-weight: 600; letter-spacing: 0.2px; }
html[data-theme='dark'] .pagehead { background: rgba(23, 26, 33, 0.9); }
html[data-theme='dark'] .logo { color: #f2f4fb; }
html[data-theme='dark'] .sidebar {
  background: linear-gradient(180deg, #17191c 0%, #131518 100%);
  border-right-color: rgba(255, 255, 255, 0.06);
  color: rgba(255, 255, 255, 0.88);
}

.theme-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 12px;
  font-size: 13px;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: var(--card);
  color: var(--text);
  cursor: pointer;
}
.theme-btn:hover { border-color: var(--primary-h); color: var(--primary-h); }

.content {
  margin-left: 212px;
  padding: 84px 28px 40px;
  height: 100vh;
  overflow: auto;
  display: flex;
  flex-direction: column;
}
/* ⚠️ 三点缺一不可：display:flex 撑横向、flex:1 0 auto 撑纵向且 shrink=0（否则长页面
   被压回一屏滚动条出不来，2026-09-28 原型踩过）、min-height:0 让内部 flex 子项可滚 */
.view {
  width: 100%;
  display: flex !important;
  flex-direction: column;
  flex: 1 0 auto;
  min-height: 0;
}
@media (min-width: 2400px) {
  .content { padding-left: 56px; padding-right: 56px; }
}

/* =====================================================================
 * 移动端（<768px）—— iPhone 15 Pro / Pro Max 主战场
 * 原则：只在这里覆盖，桌面规则一条不改（页面 ≥768px 时观感与原来 1:1）。
 * 安全区：PWA 全屏下灵动岛/底部横条占位用 env(safe-area-*) 垫开；
 *        Safari 浏览器模式这些值为 0，不受影响。
 * ===================================================================== */

/* 移动组件的「隐藏基态」：PC 不渲染观感 */
.m-tabbar,
.m-mask { display: none; }

@media (max-width: 767px) {
  /* dvh：Safari 地址栏伸缩时按可视高度算，别让底栏被地址栏顶出屏 */
  .layout { height: 100dvh; }

  /* 侧栏让位给底部标签栏 */
  .sidebar { display: none; }

  /* 顶栏变成移动顶栏：垫开灵动岛安全区，标题 + 夜间切换 */
  .pagehead {
    left: 0;
    height: calc(env(safe-area-inset-top, 0px) + 50px);
    padding: env(safe-area-inset-top, 0px) 14px 0;
  }
  .pagehead h2 { font-size: 15.5px; }
  .pc-ver { display: none; }
  .theme-btn { height: 32px; }

  /* 内容区：上让顶栏、下让标签栏（56px 内容高 + 安全区），左右收窄 */
  .content {
    margin-left: 0;
    height: 100dvh;
    padding: calc(env(safe-area-inset-top, 0px) + 60px) 13px calc(env(safe-area-inset-bottom, 0px) + 80px);
  }

  /* ---- 底部标签栏 ---- */
  .m-tabbar {
    display: flex;
    position: fixed;
    left: 0;
    right: 0;
    bottom: 0;
    z-index: 55;
    background: rgba(255, 255, 255, 0.92);
    -webkit-backdrop-filter: blur(16px);
    backdrop-filter: blur(16px);
    border-top: 0.5px solid var(--split);
    padding: 5px 4px calc(env(safe-area-inset-bottom, 0px) + 5px);
  }
  html[data-theme='dark'] .m-tabbar { background: rgba(23, 26, 33, 0.94); }
  .m-tab {
    flex: 1;
    height: 46px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 2px;
    background: none;
    border: none;
    padding: 0;
    cursor: pointer;
    color: var(--text3);
    font-size: 10.5px;
    font-family: inherit;
    -webkit-tap-highlight-color: transparent;
  }
  .m-tab .m-tab-ico { font-size: 20px; line-height: 1; }
  .m-tab.on { color: var(--primary); font-weight: 500; }
  .m-tab-wrap { flex: 1; position: relative; display: flex; }
  .m-tab-wrap .m-tab { flex: 1; }
  /* tab 子菜单浮层：竖排小卡（记录=三个历史 / 自动=三网盘） */
  .m-submenu {
    position: absolute;
    bottom: calc(100% + 10px);
    left: 50%;
    transform: translateX(-50%);
    min-width: 132px;
    padding: 4px;
    border-radius: 12px;
    background: var(--card);
    box-shadow: 0 8px 28px rgba(0, 0, 0, 0.16);
    border: 1px solid var(--split);
    display: flex;
    flex-direction: column;
    z-index: 90;
  }
  html[data-theme='dark'] .m-submenu { background: #1d212b; }
  .m-submenu-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 9px 12px;
    border: none;
    background: none;
    border-radius: 8px;
    font-size: 13px;
    font-family: inherit;
    color: var(--text);
    cursor: pointer;
    white-space: nowrap;
    -webkit-tap-highlight-color: transparent;
  }
  .m-submenu-item + .m-submenu-item { margin-top: 2px; }
  .m-submenu-item .m-submenu-ico { font-size: 14px; color: var(--text3); }
  .m-submenu-item.on { color: var(--primary); background: var(--primary-bg); font-weight: 500; }
  .m-submenu-item.on .m-submenu-ico { color: var(--primary); }
  .m-submenu-item:active { background: var(--surface-3); }

  /* ---- 「更多」底部面板 ---- */
  .m-mask {
    display: block;
    position: fixed;
    inset: 0;
    z-index: 80;
    background: rgba(0, 0, 0, 0.45);
  }
  .m-sheet {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    max-height: 76dvh;
    overflow: auto;
    background: var(--card);
    border-radius: 18px 18px 0 0;
    padding: 8px 16px calc(env(safe-area-inset-bottom, 0px) + 14px);
    box-shadow: var(--shadow-lg);
  }
  .m-sheet-grab {
    width: 38px;
    height: 4.5px;
    border-radius: 3px;
    background: var(--border);
    margin: 4px auto 12px;
  }
  .m-user {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 2px 2px 12px;
    border-bottom: 1px solid var(--split);
  }
  .m-logout {
    border: none;
    background: none;
    color: var(--error);
    font-size: 13px;
    cursor: pointer;
    font-family: inherit;
    padding: 8px 4px;
  }
  .m-group { padding-top: 10px; }
  .m-group-hd { font-size: 12px; color: var(--text3); padding: 4px 2px 6px; }
  .m-item {
    display: flex;
    align-items: center;
    gap: 12px;
    width: 100%;
    height: 46px;
    padding: 0 6px;
    border: none;
    background: none;
    cursor: pointer;
    color: var(--text);
    font-size: 14.5px;
    font-family: inherit;
    text-align: left;
    border-radius: 10px;
    -webkit-tap-highlight-color: transparent;
  }
  .m-item:active { background: var(--hover); }
  .m-item-ico { font-size: 17px; color: var(--text2); }
  .m-item-chev { margin-left: auto; width: 14px; height: 14px; color: var(--text4); }

  /* 面板出场：遮罩淡入 + 面板从底部滑上（iOS 手感） */
  .msheet-enter-active,
  .msheet-leave-active { transition: opacity 0.2s ease; }
  .msheet-enter-active .m-sheet,
  .msheet-leave-active .m-sheet { transition: transform 0.26s cubic-bezier(0.32, 0.72, 0.3, 1); }
  .msheet-enter-from,
  .msheet-leave-to { opacity: 0; }
  .msheet-enter-from .m-sheet,
  .msheet-leave-to .m-sheet { transform: translateY(60%); }
}
</style>
