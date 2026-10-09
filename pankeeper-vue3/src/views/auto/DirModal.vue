<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { message } from 'ant-design-vue';
import { DRIVE_META } from '@/api/mock/meta';
import { getDirTree } from '@/api/modules/tasks';
import { getFilesList } from '@/api/modules/files';
import { USE_MOCK } from '@/api/http';
import { getRootDirs } from '@/api/modules/accounts';
import type { MainDriveType, TreeNode } from '@/types/model';
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
interface FlatNode {
    name: string;
    path: string;
    depth: number;
    hasKids: boolean;
}
const meta = computed(() => DRIVE_META[props.type]);
const tree = ref<TreeNode[]>([]);
const expanded = ref(new Set<string>());
const selected = ref('');
const remoteRows = ref<FlatNode[]>([]);
const remoteLoading = ref(false);
const fidByPath = ref<Record<string, string>>({});
const rootLoaded = ref(false);
async function loadRemoteRoot(force = false) {
    if (remoteLoading.value)
        return;
    remoteLoading.value = true;
    try {
        const items = await getFilesList(props.type, '0', '/', force, props.accId ?? null);
        const rows: FlatNode[] = [];
        for (const it of items) {
            if (!it.is_dir)
                continue;
            const p = '/' + it.name;
            fidByPath.value[p] = it.fid;
            rows.push({ path: p, name: it.name, depth: 0, hasKids: true });
        }
        remoteRows.value = rows;
        rootLoaded.value = true;
    }
    catch (e: unknown) {
        const detail = (e as {
            response?: {
                data?: {
                    detail?: string;
                };
            };
        })?.response?.data?.detail;
        remoteRows.value = [];
        rootLoaded.value = false;
        message.error(detail || '目录加载失败', 4);
    }
    finally {
        remoteLoading.value = false;
    }
}
async function loadRemoteKids(parent: string) {
    const fid = fidByPath.value[parent];
    if (!fid)
        return;
    try {
        const items = await getFilesList(props.type, fid, '', false, props.accId ?? null);
        const depth = parent.split('/').filter(Boolean).length;
        const rows: FlatNode[] = [];
        for (const it of items) {
            if (!it.is_dir)
                continue;
            const p = parent === '/' ? '/' + it.name : parent + '/' + it.name;
            fidByPath.value[p] = it.fid;
            rows.push({ path: p, name: it.name, depth, hasKids: true });
        }
        const list = remoteRows.value;
        const idx = list.findIndex((r) => r.path === parent);
        if (idx >= 0)
            list.splice(idx + 1, 0, ...rows);
    }
    catch {
    }
}
const refreshing = ref(false);
async function onRefresh() {
    refreshing.value = true;
    try {
        const keepExpand = new Set(expanded.value);
        const keepSelected = selected.value;
        await loadRemoteRoot(true);
        for (const p of keepExpand) {
            if (fidByPath.value[p])
                await loadRemoteKids(p);
        }
        expanded.value = keepExpand;
        selected.value = keepSelected;
        message.success('已刷新');
    }
    finally {
        refreshing.value = false;
    }
}
function flatten(list: TreeNode[], depth: number, out: FlatNode[]) {
    for (const nd of list) {
        const hasKids = !!nd.kids && nd.kids.length > 0;
        out.push({ name: nd.name, path: nd.path || nd.name, depth, hasKids });
        if (hasKids && expanded.value.has(nd.path || nd.name))
            flatten(nd.kids!, depth + 1, out);
    }
}
function allPaths(list: TreeNode[], out: string[]) {
    for (const nd of list) {
        const p = nd.path || nd.name;
        out.push(p);
        if (nd.kids)
            allPaths(nd.kids, out);
    }
    return out;
}
const visible = computed(() => {
    if (!USE_MOCK) {
        const lock = defaultDirPath();
        if (lock && lock !== '/') {
            return remoteRows.value.filter((r) => {
                if (!r.path.startsWith(lock + '/'))
                    return false;
                const parts = r.path.split('/').filter(Boolean);
                for (let i = 1; i < parts.length; i++) {
                    if (!expanded.value.has('/' + parts.slice(0, i).join('/')))
                        return false;
                }
                return true;
            });
        }
        return remoteRows.value.filter((r) => {
            const parts = r.path.split('/').filter(Boolean);
            for (let i = 1; i < parts.length; i++) {
                if (!expanded.value.has('/' + parts.slice(0, i).join('/')))
                    return false;
            }
            return true;
        });
    }
    const out: FlatNode[] = [];
    flatten(tree.value, 0, out);
    return out;
});
async function toggle(node: FlatNode) {
    const s = new Set(expanded.value);
    if (s.has(node.path)) {
        s.delete(node.path);
        expanded.value = s;
        return;
    }
    s.add(node.path);
    expanded.value = s;
    if (!USE_MOCK && fidByPath.value[node.path] && !remoteRows.value.some((r) => r.path.startsWith(node.path + '/'))) {
        await loadRemoteKids(node.path);
    }
}
function pick(node: FlatNode) {
    selected.value = node.path;
}
function close() {
    emit('update:open', false);
}
function ok() {
    if (selected.value)
        emit('picked', selected.value);
    close();
}
const rootDirs = ref<Record<string, string>>({});
function defaultDirPath(): string {
    return rootDirs.value[props.type] || '';
}
async function expandTowards(targetPath: string) {
    const parts = targetPath.split('/').filter(Boolean);
    for (let i = 1; i <= parts.length; i++) {
        const ancestor = '/' + parts.slice(0, i).join('/');
        if (!fidByPath.value[ancestor])
            break;
        if (!remoteRows.value.some((r) => r.path.startsWith(ancestor + '/'))) {
            await loadRemoteKids(ancestor);
        }
        const s = new Set(expanded.value);
        s.add(ancestor);
        expanded.value = s;
    }
}
watch(() => props.open, async (v) => {
    if (!v)
        return;
    if (!USE_MOCK)
        rootDirs.value = await getRootDirs().catch(() => ({}));
    selected.value = props.initial || defaultDirPath();
    if (!USE_MOCK) {
        fidByPath.value = {};
        expanded.value = new Set();
        await loadRemoteRoot();
        const lock = defaultDirPath();
        if (lock && lock !== '/')
            await expandTowards(lock);
        if (selected.value && selected.value !== '/')
            await expandTowards(selected.value);
        return;
    }
    tree.value = await getDirTree(props.type);
    const paths = allPaths(tree.value, []);
    const keep = new Set<string>(['/']);
    for (const p of paths) {
        if (p !== '/' && selected.value.startsWith(p + '/'))
            keep.add(p);
    }
    for (const nd of tree.value[0]?.kids || [])
        keep.add(nd.path || nd.name);
    expanded.value = keep;
});
function onKey(e: KeyboardEvent) {
    if (e.key === 'Escape')
        close();
}
watch(() => props.open, (v) => {
    if (v)
        window.addEventListener('keydown', onKey);
    else
        window.removeEventListener('keydown', onKey);
});
onBeforeUnmount(() => window.removeEventListener('keydown', onKey));
</script>

<template>
  <teleport to="body">
    <div v-if="open" class="mt-mask" style="z-index: 1002" @click.self="close">
      <div class="mt-dialog" style="width: 480px">
        <div class="mt-dialog-head">
          <span class="mt-color-dot" :style="{ background: meta.color }"></span>
          <span class="mt-dialog-title">选择网盘目录 · {{ meta.full }}</span>
          <button class="mt-btn mt-btn-inline mt-btn-soft" style="margin-right: 10px" :disabled="refreshing" @click="onRefresh">
            {{ refreshing ? '刷新中…' : '刷新' }}
          </button>
          <button class="mt-close" title="关闭" @click="close">×</button>
        </div>
        <div class="mt-dialog-body">
          <div class="mt-dir-path">{{ selected || '尚未选择目录' }}</div>
          <div class="mt-tree">
            <template v-if="USE_MOCK">
              <div v-for="node in visible" :key="node.path" class="mt-tree-row" :class="{ 'mt-selected': node.path === selected }" @click="pick(node)">
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
            <template v-else>
              <div v-if="remoteLoading" class="mt-hint" style="padding: 14px 0; text-align: center">正在加载目录…</div>
              <div v-else-if="!remoteRows.length" class="mt-hint" style="padding: 14px 0; text-align: center">
                目录为空或加载失败，点「刷新」重试
              </div>
              <template v-for="node in visible" :key="node.path">
                <div class="mt-tree-row" :class="{ 'mt-selected': node.path === selected }" @click="pick(node)">
                  <span
                    v-if="node.hasKids"
                    class="mt-tree-toggle"
                    :class="{ 'mt-open': expanded.has(node.path) }"
                    @click.stop="toggle(node)"
                  >{{ expanded.has(node.path) ? '▼' : '▶' }}</span>
                  <span v-else class="mt-tree-toggle"></span>
                  <span class="mt-tree-name">{{ node.name }}</span>
                </div>
                
              </template>
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
