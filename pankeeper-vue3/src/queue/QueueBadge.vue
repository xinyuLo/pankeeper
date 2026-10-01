<script setup lang="ts">
/* 右下角「转存队列」浮标 —— 全站常驻（队列有任务才显示），点击开侧边抽屉。
 * 看板本体在 QueueBoard（原记录页队列段同一组件），规则一致：日志单选、进度跟随。
 * 记录页的队列 tab 已撤，这里就是队列的唯一入口。 */
import { computed, ref } from 'vue'
import { queueView } from '@/queue/engine'
import QueueBoard from '@/queue/QueueBoard.vue'

const open = ref(false)
const active = computed(() => queueView.tasks.filter((t) => t.status === 'wait' || t.status === 'run').length)
const running = computed(() => queueView.tasks.filter((t) => t.status === 'run').length)
/* 队列里有任何任务（含 1 小时内的完成/失败）都显示——刚收尾的任务也能点开看日志 */
const visible = computed(() => queueView.tasks.length > 0)
</script>

<template>
  <div v-if="visible" class="pkq-badge on" title="点开看转存队列和日志" @click="open = true">
    <span class="pkq-dot" :class="{ run: running > 0 }"></span>
    转存队列 · {{ active }}<template v-if="running > 0"> · {{ running }} 个转存中</template>
  </div>

  <a-drawer v-model:open="open" title="转存队列" :width="640" placement="right">
    <QueueBoard />
  </a-drawer>
</template>
