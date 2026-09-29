<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { ConfigProvider, theme as antTheme } from 'ant-design-vue'
import zhCN from 'ant-design-vue/es/locale/zh_CN'
import { useThemeStore } from '@/store/theme'
import { hydrateAll } from '@/api/bootstrap'
import { useAuthStore } from '@/store/auth'
import { pkQueueCfgGet } from '@/queue/engine'

const themeStore = useThemeStore()
const auth = useAuthStore()

// 真实模式启动时把后端数据灌进各 reactive store（视图层照旧读 store，零改动）
onMounted(() => {
  themeStore.apply()
  pkQueueCfgGet()
  if (auth.logged) {
    hydrateAll() // 未登录时不在启动期灌注（会 401），登录成功后再补
    auth.loadAvatar() // 头像走独立接口（不并进 /settings），刷新后单独拉一次
  }
})

// antd 主题 token 对齐原型视觉令牌（src/styles/pk.css 的 :root / dark）
const themeConfig = computed(() => {
  const token = {
    colorPrimary: '#1677ff',
    borderRadius: 8,
    fontSize: 14,
  }
  if (themeStore.isDark) {
    return {
      algorithm: antTheme.darkAlgorithm,
      token: {
        ...token,
        colorPrimary: '#4096ff',
        colorBgLayout: '#0f1115',
        colorBgContainer: '#171a21',
        colorBgElevated: '#171a21',
        colorBorder: '#3a3f4b',
        colorBorderSecondary: '#262b35',
        colorSplit: '#262b35',
      },
    }
  }
  return {
    token: {
      ...token,
      colorBgLayout: '#f5f7fa',
    },
  }
})
</script>

<template>
  <a-config-provider :locale="zhCN" :theme="themeConfig">
    <router-view />
  </a-config-provider>
</template>
