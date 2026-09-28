<script setup lang="ts">
import { computed } from 'vue'
import { ConfigProvider, theme as antTheme } from 'ant-design-vue'
import zhCN from 'ant-design-vue/es/locale/zh_CN'
import { useThemeStore } from '@/store/theme'

const themeStore = useThemeStore()

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
