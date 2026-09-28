<script setup lang="ts">
/* 分享内容树（转存弹窗左树）：带勾选框，勾选子文件夹可只转存部分内容。
 * 递归自引用组件；勾选状态由父级（TransferModal）通过 provide 注入，
 * 勾/取消一个节点会级联到它的全部子孙。 */
import { inject, ref } from 'vue'
import type { TreeNode } from '@/types/model'

/** 父级注入的勾选能力（key 规则与级联收集都在父级，保证计数一致） */
interface ShareCheckApi {
  checked: { value: Set<string> }
  keyOf: (base: string, node: TreeNode) => string
  collectKeys: (node: TreeNode, base: string) => string[]
  toggle: (keys: string[], val: boolean) => void
}

const props = defineProps<{
  nodes: TreeNode[]
  /** 父链 key（根为 ''），保证同名节点在不同层级不串 */
  baseKey: string
}>()

const api = inject<ShareCheckApi>('shareCheck')!

function keyOf(n: TreeNode): string {
  return api.keyOf(props.baseKey, n)
}
function hasKids(n: TreeNode): boolean {
  return !!(n.kids && n.kids.length)
}
function isChecked(n: TreeNode): boolean {
  return api.checked.value.has(keyOf(n))
}
/** 半选：自己没勾但子孙里有勾的（提示「只转了部分」） */
function isIndet(n: TreeNode): boolean {
  if (isChecked(n) || !hasKids(n)) return false
  return api.collectKeys(n, props.baseKey).slice(1).some((k) => api.checked.value.has(k))
}
function onCheck(n: TreeNode, e: { target: { checked: boolean } }) {
  api.toggle(api.collectKeys(n, props.baseKey), e.target.checked)
}

/* 展开/收起：默认全展开，点箭头收起（kids 里 v-show） */
const closed = ref(new Set<string>())
function isOpen(n: TreeNode): boolean {
  return hasKids(n) && !closed.value.has(keyOf(n))
}
function toggleOpen(n: TreeNode) {
  const k = keyOf(n)
  const s = new Set(closed.value)
  if (s.has(k)) s.delete(k)
  else s.add(k)
  closed.value = s
}
</script>

<template>
  <ul class="tree">
    <li v-for="n in nodes" :key="keyOf(n)">
      <div class="tnode">
        <span class="caret" :class="{ open: isOpen(n) }" @click.stop="toggleOpen(n)">{{ hasKids(n) ? '▶' : '' }}</span>
        <a-checkbox
          :checked="isChecked(n)"
          :indeterminate="isIndet(n)"
          @change="(e: any) => onCheck(n, e)"
        />
        <span class="sname" :title="n.name">{{ n.name }}</span>
        <span v-if="n.size" class="small muted ssize">{{ n.size }}</span>
      </div>
      <ul v-if="hasKids(n)" v-show="isOpen(n)" class="kids open">
        <ShareTree :nodes="n.kids!" :base-key="keyOf(n)" />
      </ul>
    </li>
  </ul>
</template>

<style scoped>
.tnode :deep(.ant-checkbox-wrapper) { margin-right: 2px; }
.sname {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ssize { margin-left: auto; padding-left: 12px; flex: none; }
</style>
