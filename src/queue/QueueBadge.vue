<script setup lang="ts">
/* 右下角「转存中」浮标 —— 记录页不显示（那边有完整看板），点击直达 records#queue */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { queueView } from '@/queue/engine'

const route = useRoute()
const router = useRouter()

const hidden = computed(() => route.name === 'records')
const active = computed(() => queueView.tasks.filter((t) => t.status === 'wait' || t.status === 'run').length)
const running = computed(() => queueView.tasks.filter((t) => t.status === 'run').length)

function go() {
  router.push({ path: '/records', hash: '#queue' })
}
</script>

<template>
  <div v-if="!hidden && active > 0" class="pkq-badge on" title="点开转存记录页看队列和日志" @click="go">
    <span class="pkq-dot" :class="{ run: running > 0 }"></span>
    转存队列 · {{ active }}<template v-if="running > 0"> · {{ running }} 个转存中</template>
  </div>
</template>
