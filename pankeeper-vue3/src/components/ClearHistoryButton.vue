<script setup lang="ts">
import { Modal } from 'ant-design-vue';
export type ClearRange = '1m' | '3m' | '6m' | 'all';
const emit = defineEmits<{
    (e: 'clear', range: ClearRange): void;
}>();
const LABELS: Record<ClearRange, string> = { '1m': '一个月前', '3m': '三个月前', '6m': '半年前', all: '全部记录' };
function onMenuClick({ key }: {
    key: string;
}) {
    const range = key as ClearRange;
    Modal.confirm({
        title: range === 'all' ? '确定清空全部记录？' : `确定清除${LABELS[range]}的记录？`,
        content: '删除后不可恢复，请谨慎操作。',
        okText: '删除',
        okType: 'danger',
        cancelText: '取消',
        onOk: () => emit('clear', range),
    });
}
</script>

<template>
  <a-dropdown>
    <a-button>清空记录</a-button>
    <template #overlay>
      <a-menu @click="onMenuClick">
        <a-menu-item key="1m">一个月前</a-menu-item>
        <a-menu-item key="3m">三个月前</a-menu-item>
        <a-menu-item key="6m">半年前</a-menu-item>
        <a-menu-divider />
        <a-menu-item key="all"><span style="color: var(--error)">全部记录</span></a-menu-item>
      </a-menu>
    </template>
  </a-dropdown>
</template>
