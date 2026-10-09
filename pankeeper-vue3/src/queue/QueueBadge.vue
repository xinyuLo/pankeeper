<script setup lang="ts">
import { computed, ref } from 'vue';
import { useBackGuard } from '@/composables/useBackGuard';
import { useIsMobile } from '@/composables/useIsMobile';
import { queueView, isAutoQueued } from '@/queue/engine';
import QueueBoard from '@/queue/QueueBoard.vue';
const isMobile = useIsMobile();
const open = ref(false);
useBackGuard(open);
const manual = computed(() => queueView.tasks.filter((t) => !isAutoQueued(t)));
const active = computed(() => manual.value.filter((t) => t.status === 'wait' || t.status === 'run').length);
const running = computed(() => manual.value.filter((t) => t.status === 'run').length);
const visible = computed(() => manual.value.length > 0);
</script>

<template>
  <div v-if="visible" class="pkq-badge on" title="点开看转存队列和日志" @click="open = true">
    <span class="pkq-dot" :class="{ run: running > 0 }"></span>
    转存队列 · {{ active }}<template v-if="running > 0"> · {{ running }} 个转存中</template>
  </div>

  <a-drawer v-model:open="open" title="转存队列" :width="isMobile ? '100%' : 640" placement="right">
    <QueueBoard />
  </a-drawer>
</template>
