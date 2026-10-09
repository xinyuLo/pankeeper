<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { message } from 'ant-design-vue';
import LazyDirTree from '@/components/LazyDirTree.vue';
import { DRIVE_META } from '@/api/mock/meta';
import { getRootDirs } from '@/api/modules/accounts';
import type { MainDriveType } from '@/types/model';
import { useBackGuard } from '@/composables/useBackGuard';
const props = defineProps<{
    open: boolean;
    type: MainDriveType;
    initial: string;
    accId?: number | null;
}>();
const emit = defineEmits<{
    (e: 'update:open', v: boolean): void;
    (e: 'picked', path: string): void;
}>();
useBackGuard(() => props.open, () => emit('update:open', false));
const meta = computed(() => DRIVE_META[props.type]);
const bdType = computed(() => props.type);
const bdAccId = computed(() => {
    const n = Number(props.accId);
    return Number.isFinite(n) && n > 0 ? n : null;
});
const bdKey = computed(() => `${bdType.value}/${bdAccId.value ?? 'def'}`);
const rootDirs = ref<Record<string, string>>({});
const bdRootPath = computed(() => rootDirs.value[bdType.value] || '');
const treeRef = ref<InstanceType<typeof LazyDirTree> | null>(null);
const bdPath = ref('');
const refreshing = ref(false);
const rootReady = ref(false);
async function onRefresh() {
    refreshing.value = true;
    try {
        await treeRef.value?.reload();
    }
    finally {
        refreshing.value = false;
    }
}
function onBdPick(path: string) {
    bdPath.value = path;
}
function onPickOk() {
    if (!bdPath.value) {
        message.warning('请先在树里选择一个目录');
        return;
    }
    emit('picked', bdPath.value);
    emit('update:open', false);
}
watch(() => props.open, async (v) => {
    if (!v)
        return;
    bdPath.value = props.initial;
    rootReady.value = false;
    rootDirs.value = await getRootDirs().catch(() => ({}));
    rootReady.value = true;
});
</script>

<template>
  <a-modal
    :open="open"
    :width="480"
    :destroy-on-close="true"
    ok-text="选定此处"
    cancel-text="取消"
    @ok="onPickOk"
    @update:open="(v: boolean) => emit('update:open', v)"
  >
    <template #title>
      <div class="ad-title">
        <span>选择网盘目录 · {{ meta.full }}</span>
        <a-button size="small" :loading="refreshing" @click="onRefresh">刷新</a-button>
      </div>
    </template>
    <p class="small" style="color: var(--text3); margin: 0 0 10px">点文件夹名选中目标目录，点左侧箭头展开子目录。</p>
    <LazyDirTree
      v-if="rootReady"
      ref="treeRef"
      :key="bdKey"
      :type="bdType"
      :acc-id="bdAccId"
      :root-path="bdRootPath"
      :initial-path="bdPath"
      @select="onBdPick"
    />
    <div v-else class="ad-picked" style="text-align: center">正在加载目录配置…</div>
    <div class="ad-picked">已选目录：<b>{{ bdPath || '/' }}</b></div>
  </a-modal>
</template>

<style scoped>
.ad-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-right: 30px; /* 给右上角 X 留位 */
  font-size: 15px;
  font-weight: 600;
}
.ad-picked {
  margin-top: 12px;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--surface-2);
  font-size: 12.5px;
  color: var(--text3);
}
.ad-picked b {
  color: var(--primary);
  font-weight: 500;
  font-family: var(--font-mono);
}
</style>
