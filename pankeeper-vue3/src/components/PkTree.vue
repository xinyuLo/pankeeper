<script setup lang="ts">
import { ref } from 'vue';
import type { TreeNode } from '@/types/model';
const props = withDefaults(defineProps<{
    nodes: TreeNode[];
    selectable?: boolean;
    defaultExpandDepth?: number;
}>(), { selectable: true, defaultExpandDepth: 2 });
const emit = defineEmits<{
    (e: 'select', node: TreeNode): void;
}>();
const selectedPath = ref('');
const opened = ref(new Set<string>());
function keyOf(n: TreeNode, depth: number): string {
    return (n.path || n.name) + '@' + depth;
}
function hasKids(n: TreeNode): boolean {
    return !!(n.kids && n.kids.length);
}
function isOpen(n: TreeNode, depth: number): boolean {
    return opened.value.has(keyOf(n, depth));
}
function toggle(n: TreeNode, depth: number) {
    const k = keyOf(n, depth);
    const s = new Set(opened.value);
    if (s.has(k))
        s.delete(k);
    else
        s.add(k);
    opened.value = s;
}
function clickNode(n: TreeNode, depth: number) {
    if (props.selectable && n.path)
        selectedPath.value = n.path;
    emit('select', n);
    if (hasKids(n))
        toggle(n, depth);
}
;
function initOpen(nodes: TreeNode[], depth: number) {
    for (const n of nodes) {
        if (hasKids(n) && depth < props.defaultExpandDepth) {
            opened.value.add(keyOf(n, depth));
            initOpen(n.kids!, depth + 1);
        }
    }
}
initOpen(props.nodes, 0);
</script>

<template>
  <ul class="tree">
    <li v-for="(n, i) in nodes" :key="i">
      <div class="tnode" :class="{ sel: selectable && n.path && n.path === selectedPath }" @click.stop="clickNode(n, 0)">
        <span class="caret" :class="{ open: hasKids(n) && isOpen(n, 0) }">{{ hasKids(n) ? '▶' : '' }}</span>
        <span>{{ hasKids(n) ? '📁' : '🎬' }}</span>
        <span>{{ n.name }}</span>
        <span v-if="n.size" class="small muted" style="margin-left: auto; padding-left: 12px">{{ n.size }}</span>
      </div>
      <div v-if="hasKids(n)">
        <ul v-show="isOpen(n, 0)" class="kids open">
          <li v-for="(k, j) in n.kids" :key="j">
            <div class="tnode" :class="{ sel: selectable && k.path && k.path === selectedPath }" @click.stop="clickNode(k, 1)">
              <span class="caret" :class="{ open: hasKids(k) && isOpen(k, 1) }">{{ hasKids(k) ? '▶' : '' }}</span>
              <span>{{ hasKids(k) ? '📁' : '🎬' }}</span>
              <span>{{ k.name }}</span>
              <span v-if="k.size" class="small muted" style="margin-left: auto; padding-left: 12px">{{ k.size }}</span>
            </div>
            <ul v-show="hasKids(k) && isOpen(k, 1)" class="kids open" style="margin-left: 18px">
              <li v-for="(g, m) in k.kids" :key="m">
                <div class="tnode" :class="{ sel: selectable && g.path && g.path === selectedPath }" @click.stop="clickNode(g, 2)">
                  <span class="caret">{{ hasKids(g) ? '▶' : '' }}</span>
                  <span>{{ hasKids(g) ? '📁' : '🎬' }}</span>
                  <span>{{ g.name }}</span>
                  <span v-if="g.size" class="small muted" style="margin-left: auto; padding-left: 12px">{{ g.size }}</span>
                </div>
              </li>
            </ul>
          </li>
        </ul>
      </div>
    </li>
  </ul>
</template>
