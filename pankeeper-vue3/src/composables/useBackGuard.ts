import { onUnmounted, watch, type Ref } from 'vue'

/**
 * 移动端侧滑返回护栏：弹层（抽屉/弹窗）打开时推一个**占位历史栈**。
 * 浏览器边缘侧滑返回（iOS/Android 手势）会先弹占位栈——把弹层关掉，
 * 而不是退出当前页面（2026-10-08 用户实锤：记录详情抽屉侧滑直接退回上一页）。
 *
 * 用法：const open = ref(false); useBackGuard(open)
 * 用户点关闭按钮/遮罩时 open 变 false，自动把占位栈弹掉（popstate 无副作用）。
 */
export function useBackGuard(open: Ref<boolean>, onClose?: () => void) {
  let guard = false
  const onPop = () => {
    if (guard) {
      guard = false
      open.value = false
      onClose?.()
    }
  }
  window.addEventListener('popstate', onPop)
  onUnmounted(() => window.removeEventListener('popstate', onPop))

  watch(open, (v) => {
    if (v && !guard) {
      guard = true
      // ⚠️ 必须克隆 vue-router 自己的历史 state 再加标记：推一个"外来" state 的话，
      // popstate 时 router 认不出 current 会触发同路径重导航 → 页面闪刷、弹层闪一下
      // 又缩回（2026-10-08 用户实测）。克隆后 router 看到的是同路由，静默处理。
      history.pushState({ ...history.state, pkBackGuard: 1 }, '')
    } else if (!v && guard) {
      guard = false
      history.back() // 弹掉占位栈；popstate 时 open 已是 false，无副作用
    }
  })
}
