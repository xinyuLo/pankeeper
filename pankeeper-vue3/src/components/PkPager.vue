<script setup lang="ts">
/* 分页条 —— 原型 .pager 结构的组件化：总条数在左、页码居中、每页条数+跳页在右，
 * 页码超过窗口宽度出省略号。搜索页 / 转存记录页共用，保证长得一模一样。 */
import { computed, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    total: number
    current: number
    pageSize: number
    pageSizes?: number[]
  }>(),
  { pageSizes: () => [8, 20, 50] },
)

const emit = defineEmits<{
  (e: 'update:current', v: number): void
  (e: 'update:pageSize', v: number): void
}>()

const jumpInput = ref('')

const totalPages = computed(() => Math.max(1, Math.ceil(props.total / props.pageSize)))

// 页码窗口：当前页前后各 2 页，首尾常驻，超出出省略号
const pages = computed<(number | 'gap')[]>(() => {
  const tp = totalPages.value
  const cur = props.current
  if (tp <= 9) return Array.from({ length: tp }, (_, i) => i + 1)
  const set = new Set<number>([1, tp, cur - 2, cur - 1, cur, cur + 1, cur + 2])
  const list = [...set].filter((p) => p >= 1 && p <= tp).sort((a, b) => a - b)
  const out: (number | 'gap')[] = []
  let prev = 0
  for (const p of list) {
    if (p - prev > 1) out.push('gap')
    out.push(p)
    prev = p
  }
  return out
})

watch(
  () => props.pageSize,
  () => emit('update:current', 1), // 切每页条数回第 1 页（原型约定）
)

function goPage(p: number) {
  const target = Math.min(totalPages.value, Math.max(1, p))
  emit('update:current', target)
}

function doJump() {
  const n = parseInt(jumpInput.value, 10)
  if (!isNaN(n)) goPage(n)
  jumpInput.value = ''
}

function fmtRange(): string {
  if (props.total === 0) return '第 0 条'
  const start = (props.current - 1) * props.pageSize + 1
  const end = Math.min(props.total, props.current * props.pageSize)
  return `第 ${start}–${end} 条`
}
</script>

<template>
  <div class="pager" v-if="total > 0">
    <span class="pg-total">共 <b>{{ total }}</b> 条 · {{ fmtRange() }}</span>

    <div class="pg-list">
      <button class="pg-it" :disabled="current <= 1" @click="goPage(current - 1)">上一页</button>
      <template v-for="(p, i) in pages" :key="i">
        <span v-if="p === 'gap'" class="pg-gap">…</span>
        <button v-else class="pg-it" :class="{ on: p === current }" @click="goPage(p)">{{ p }}</button>
      </template>
      <button class="pg-it" :disabled="current >= totalPages" @click="goPage(current + 1)">下一页</button>
    </div>

    <div class="pg-size">
      每页
      <select :value="pageSize" @change="emit('update:pageSize', parseInt(($event.target as HTMLSelectElement).value, 10))">
        <option v-for="s in pageSizes" :key="s" :value="s">{{ s }}</option>
      </select>
    </div>
    <div class="pg-jump">
      跳至 <input v-model="jumpInput" @keydown.enter="doJump" /> 页
    </div>
  </div>
</template>
