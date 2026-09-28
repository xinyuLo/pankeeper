<script setup lang="ts">
/* 网盘目录选择弹窗（原型 mtDirMask，480px）。
 * 从任务弹窗的「浏览」叠加打开（允许两层弹窗）；mock 目录树按网盘区分，
 * 单选、选中显示完整路径、确定回填。树用「拍平 + 展开集」渲染，免递归组件。 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { DRIVE_META } from '@/api/mock/meta'
import { getDirTree } from '@/api/modules/tasks'
import type { MainDriveType, TreeNode } from '@/types/model'

const props = defineProps<{ open: boolean; type: MainDriveType; initial: string }>()
const emit = defineEmits<{ (e: 'update:open', v: boolean): void; (e: 'picked', path: string): void }>()

/** 拍平后的可见行：path 已在 mock 里标注完整路径（根 = /） */
interface FlatNode {
  name: string
  path: string
  depth: number
  hasKids: boolean
}

const meta = computed(() => DRIVE_META[props.type])
const tree = ref<TreeNode[]>([])
const expanded = ref(new Set<string>())
const selected = ref('')

/** 按「祖先全展开才可见」的规则拍平整棵树 */
function flatten(list: TreeNode[], depth: number, out: FlatNode[]) {
  for (const nd of list) {
    const hasKids = !!nd.kids && nd.kids.length > 0
    out.push({ name: nd.name, path: nd.path || nd.name, depth, hasKids })
    if (hasKids && expanded.value.has(nd.path || nd.name)) flatten(nd.kids!, depth + 1, out)
  }
}
const visible = computed(() => {
  const out: FlatNode[] = []
  flatten(tree.value, 0, out)
  return out
})

/** 收集整棵树全部路径（预选/展开祖先时用） */
function allPaths(list: TreeNode[], out: string[]) {
  for (const nd of list) {
    const p = nd.path || nd.name
    out.push(p)
    if (nd.kids) allPaths(nd.kids, out)
  }
  return out
}

watch(
  () => props.open,
  async (v) => {
    if (!v) return
    tree.value = await getDirTree(props.type)
    selected.value = props.initial || ''
    // 默认展开根层；若带了已选路径，把它沿途的祖先全部展开让选中项露出来
    const paths = allPaths(tree.value, [])
    const keep = new Set<string>(['/'])
    for (const p of paths) {
      if (p !== '/' && selected.value.startsWith(p + '/')) keep.add(p)
    }
    // 顶层目录默认摊开，减少一次点击
    for (const nd of tree.value[0]?.kids || []) keep.add(nd.path || nd.name)
    expanded.value = keep
  },
)

function toggle(node: FlatNode) {
  const s = new Set(expanded.value)
  if (s.has(node.path)) s.delete(node.path)
  else s.add(node.path)
  expanded.value = s
}

function pick(node: FlatNode) {
  selected.value = node.path
}

function close() {
  emit('update:open', false)
}

function ok() {
  if (selected.value) emit('picked', selected.value)
  close()
}

/* Esc 只关自己（顶层是目录弹窗时）；任务弹窗的 Esc 处理器会先判断谁在最上面 */
function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') close()
}
watch(
  () => props.open,
  (v) => {
    if (v) window.addEventListener('keydown', onKey)
    else window.removeEventListener('keydown', onKey)
  },
)
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <teleport to="body">
    <div v-if="open" class="mt-mask" style="z-index: 1002" @click.self="close">
      <div class="mt-dialog" style="width: 480px">
        <div class="mt-dialog-head">
          <span class="mt-color-dot" :style="{ background: meta.color }"></span>
          <span class="mt-dialog-title">选择网盘目录 · {{ meta.full }}</span>
          <button class="mt-close" title="关闭" @click="close">×</button>
        </div>
        <div class="mt-dialog-body">
          <!-- 目录清单 mock 一次拉全：真实版是懒加载根层 + 1 小时分层缓存（原型 mtDirCache） -->
          <div class="mt-dir-bar">○ 首次加载 · 拉取后将缓存 60 分钟</div>
          <div class="mt-dir-path">{{ selected || '尚未选择目录' }}</div>
          <div class="mt-tree">
            <template v-for="node in visible" :key="node.path">
              <div class="mt-tree-row" :class="{ 'mt-selected': node.path === selected }" @click="pick(node)">
                <span
                  v-if="node.hasKids"
                  class="mt-tree-toggle"
                  :class="{ 'mt-open': expanded.has(node.path) }"
                  @click.stop="toggle(node)"
                >▶</span>
                <span v-else class="mt-tree-toggle"></span>
                <span class="mt-tree-name">{{ node.name }}</span>
              </div>
            </template>
          </div>
          <div class="mt-hint">当前选择：<span class="mt-hint-strong">{{ selected || '未选择' }}</span></div>
        </div>
        <div class="mt-dialog-foot">
          <button class="mt-btn" @click="close">取消</button>
          <button class="mt-btn mt-btn-primary" @click="ok">确定</button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<style scoped src="./mt-modal.css"></style>
