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
  CloudOutlined,
  DatabaseOutlined,
  FolderOpenOutlined,
  FieldTimeOutlined,
  SettingOutlined,
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
      { key: 'records', label: '转存记录', icon: FileTextOutlined },
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
const openGroups = ref<Set<string>>(new Set(['transfer', 'auto', 'sys']))
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
</script>

<template>
  <div class="layout">
    <aside class="sidebar">
      <div class="logo"><i></i> PanKeeper</div>
      <nav class="menu">
        <a class="menu-item" :class="{ on: activeKey === 'dashboard' }" @click.prevent="router.push('/dashboard')">
          <HomeOutlined class="lico" />首页驾驶舱
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
          <div class="avatar">管</div>
          <div style="flex: 1; min-width: 0">
            <div style="font-size: 13.5px; font-weight: 500">{{ auth.username }}</div>
            <div class="small muted">已登录</div>
          </div>
        </div>
        <button class="btn-link" style="margin-top: 8px; padding-left: 0; color: var(--primary); cursor: pointer" @click="logout">退出登录</button>
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
        <span class="small muted">v0.1.0</span>
      </div>
    </div>

    <div class="content">
      <div class="view">
        <router-view />
      </div>
    </div>

    <QueueBadge />
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
  background: var(--card);
  border-right: 1px solid var(--split);
  display: flex;
  flex-direction: column;
}
.logo {
  padding: 0 20px;
  height: 60px;
  font-size: 16.5px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 10px;
  border-bottom: 1px solid var(--split);
  flex-shrink: 0;
}
.logo i {
  width: 26px;
  height: 26px;
  border-radius: 7px;
  background: var(--primary);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.logo i::after {
  content: '';
  width: 11px;
  height: 11px;
  border: 2.5px solid #fff;
  border-radius: 3px;
}

.menu { padding: 10px; flex: 1; overflow: auto; }
.menu-item,
.menu .nav-hd {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 0 12px;
  height: 38px;
  border-radius: var(--r-sm);
  color: var(--text2);
  text-decoration: none;
  font-size: 13.5px;
  border: none;
  background: none;
  cursor: pointer;
  font-family: inherit;
  text-align: left;
  transition: background 0.18s, color 0.18s;
}
.menu .nav-hd { font-weight: 500; color: var(--text); margin-top: 6px; }
.menu-item :deep(.lico),
.menu .nav-hd .lico { font-size: 16px; opacity: 0.85; }
.menu-item:hover,
.menu .nav-hd:hover { background: var(--hover); color: var(--text); }
.menu-item.on { background: var(--primary-bg); color: var(--primary); font-weight: 500; }
.menu .nav-hd .chev { margin-left: auto; width: 14px; height: 14px; opacity: 0.4; transition: transform 0.22s; }
.nav-group.open .nav-hd .chev { transform: rotate(180deg); }
.menu .nav-sub { display: grid; grid-template-rows: 0fr; transition: grid-template-rows 0.24s ease; }
.nav-group.open .nav-sub { grid-template-rows: 1fr; }
.menu .nav-sub > div { overflow: hidden; min-height: 0; }
.menu .nav-sub .menu-item { padding-left: 26px; height: 34px; font-size: 13px; }
.menu .nav-sub .menu-item :deep(.lico) { font-size: 15px; }

.side-foot { padding: 14px 16px; border-top: 1px solid var(--split); flex-shrink: 0; }
.who { display: flex; align-items: center; gap: 10px; }
.avatar {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: linear-gradient(135deg, #1677ff, #69c0ff);
  color: #fff;
  font-size: 12.5px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 500;
  flex-shrink: 0;
}

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
</style>
