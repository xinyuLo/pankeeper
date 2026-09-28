<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { ConfigProvider, theme as antTheme } from 'ant-design-vue'
import zhCN from 'ant-design-vue/es/locale/zh_CN'
import { useThemeStore } from '@/store/theme'
import { USE_MOCK } from '@/api/http'
import { listDdItems } from '@/api/modules/dd'
import { listAccounts } from '@/api/modules/accounts'
import { listPaTasks } from '@/api/modules/tasks'
import { pkQueueCfgGet } from '@/queue/engine'

const themeStore = useThemeStore()

// 真实模式启动时把后端数据灌进各 reactive store（视图层照旧读 store，零改动）
onMounted(() => {
  themeStore.apply()
  if (USE_MOCK) return
  listDdItems().catch(() => {})
  listAccounts().catch(() => {})
  listPaTasks().catch(() => {})
  pkQueueCfgGet()
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
