import { computed, onUnmounted, watch, type Ref } from 'vue'

/**
 * 移动端侧滑返回护栏（全局单例管理器版，2026-10-09 重构）。
 *
 * 背景：每个弹层打开时压一个占位历史栈，浏览器侧滑/返回先弹占位栈——关掉对应弹层
 * 而不是退出页面（2026-10-08 用户实锤）。
 *
 * ⚠️ 为什么是全局单例而不是每实例一个监听：弹层会嵌套（快速转存弹窗里再开识别候选
 * 卡片）。每实例各自监听 popstate 时，内层关闭触发的 history.back() 会被**所有**
 * 外层实例听到——外层误判"我的占位被弹了"→ 把转存弹窗也关了（2026-10-09 用户实锤：
 * 选完候选两个弹窗瞬间全关）。单例统一管栈：popstate 时只关**栈顶那一层**。
 *
 * pendingPop 标记：主动关闭（点 X/取消/选候选）也要 history.back() 弹掉自己的占位，
 * 那次 popstate 不应该关任何层——用标记核销。
 */
interface GuardEntry {
  id: number
  setOpen: (v: boolean) => void
  onClose?: () => void
}

const stack: GuardEntry[] = []   // 占位栈：index 0 = 最外层，末位 = 栈顶
let pendingPop = false           // 我们主动 back() 后等待核销的 popstate
let seq = 0
let styleInjected = false
let listenersReady = false

function injectNoAnimStyle() {
  if (styleInjected) return
  styleInjected = true
  const st = document.createElement('style')
  st.id = 'pk-backguard-style'
  st.textContent =
    '.pk-no-anim .ant-drawer-content-wrapper,.pk-no-anim .ant-drawer-mask,' +
    '.pk-no-anim .ant-drawer { transition: none !important; animation: none !important; }'
  document.head.appendChild(st)
}

function ensureListeners() {
  if (listenersReady) return
  listenersReady = true
  window.addEventListener('popstate', () => {
    if (pendingPop) {
      pendingPop = false            // 我们主动 back() 的那次：占位已核销，不关任何层
      return
    }
    if (stack.length) {
      // 用户返回/侧滑：只关栈顶那一层，其余层（外层）不动
      const top = stack.pop()!
      injectNoAnimStyle()
      document.documentElement.classList.add('pk-no-anim')
      setTimeout(() => document.documentElement.classList.remove('pk-no-anim'), 400)
      top.setOpen(false)
      top.onClose?.()
    }
    // 栈已空的返回：真实离开页面，不拦
  })
}

export function useBackGuard(open: Ref<boolean> | (() => boolean), onClose?: () => void) {
  // getter 入参（组件 v-model:open 场景）经 computed 包装，写回走 emit——这里统一按可写 Ref 用
  const isOpen = (typeof open === 'function' ? computed(open) : open) as Ref<boolean>
  const myId = ++seq
  const myIndex = () => stack.findIndex(e => e.id === myId)

  const unregister = () => {
    const i = myIndex()
    if (i >= 0) stack.splice(i, 1)
  }

  onUnmounted(() => {
    // 组件卸载（destroy-on-close）时还开着：摘掉占位登记，避免历史栈里留孤儿
    unregister()
  })

  watch(isOpen, (v) => {
    if (v && myIndex() < 0) {
      ensureListeners()
      stack.push({ id: myId, setOpen: vv => (isOpen.value = vv), onClose })
      history.pushState({ ...history.state, pkBackGuard: myId }, '')
    } else if (!v && myIndex() >= 0) {
      const i = myIndex()
      const isTop = i === stack.length - 1
      stack.splice(i, 1)
      if (isTop) {
        pendingPop = true          // 核销这次 back() 的 popstate：不关任何层
        history.back()
      }
      // 非栈顶的主动关闭（理论上被内层遮罩挡住不会发生）：直接关，不动历史
    }
  })
}
