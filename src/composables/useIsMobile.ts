/* 移动端断点（<768px = 手机布局）。断点数值与 BasicLayout/pk.css 的媒体查询保持同一常数：
 * CSS 里改了这里必须跟着改。表格页用它决定渲染「表格」还是「卡片列表」——
 * 纯 CSS 折叠在 antd 表格/自绘表格上做不出卡片观感，JS 断点更干净。 */
import { onBeforeUnmount, onMounted, ref, type Ref } from 'vue'

export const MOBILE_MAX = 767

export function useIsMobile(): Ref<boolean> {
  const isMobile = ref(false)
  let mq: MediaQueryList | null = null
  const update = () => {
    isMobile.value = !!mq?.matches
  }
  onMounted(() => {
    mq = window.matchMedia(`(max-width: ${MOBILE_MAX}px)`)
    update()
    mq.addEventListener('change', update)
  })
  onBeforeUnmount(() => mq?.removeEventListener('change', update))
  return isMobile
}
