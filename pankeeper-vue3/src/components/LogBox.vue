<script setup lang="ts">
import { nextTick, ref, watch } from 'vue';
import type { QueueLogLine } from '@/types/model';
const props = withDefaults(defineProps<{
    lines: (QueueLogLine | {
        lv: string;
        txt: string;
    })[];
    followBottom?: boolean;
}>(), { followBottom: false });
const box = ref<HTMLElement | null>(null);
watch(() => props.lines.length, async () => {
    if (!props.followBottom)
        return;
    await nextTick();
    if (box.value)
        box.value.scrollTop = box.value.scrollHeight;
}, { immediate: true });
</script>

<template>
  <div ref="box" class="logbox">
    <div v-for="(l, i) in lines" :key="i">
      <span :class="'lv-' + l.lv">[{{ l.lv }}]</span> {{ l.txt }}
    </div>
  </div>
</template>
