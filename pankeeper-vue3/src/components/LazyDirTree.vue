<script lang="ts">
let cooldownUntil = 0;
async function paceIfHot() {
    const wait = cooldownUntil - Date.now();
    if (wait > 0)
        await new Promise((r) => setTimeout(r, wait));
}
function noteCache(cached: boolean) {
    cooldownUntil = cached ? 0 : Date.now() + 600;
}
</script>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { message } from 'ant-design-vue';
import { RightOutlined, LoadingOutlined, FolderOutlined, PlusOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons-vue';
import { getFilesListMeta, createDir, renameDir, deleteDir, type DirItem } from '@/api/modules/files';
import type { MainDriveType } from '@/types/model';
interface DirNode extends DirItem {
    path: string;
    parentKey: string;
    loaded: boolean;
    open: boolean;
    loading: boolean;
    kids: DirNode[];
}
const props = defineProps<{
    type: MainDriveType;
    nodes?: DirNode[];
    pathBase?: string;
    accId?: number | null;
    selected?: string;
    initialPath?: string;
    rootPath?: string;
    hideToolbar?: boolean;
}>();
const emit = defineEmits<{
    (e: 'select', path: string, fid: string): void;
}>();
defineExpose({
    reload: () => {
        if (props.rootPath && props.rootPath !== '/')
            return init(true);
        return refreshLayer('0', '/', root.items, (items) => (root.items = items));
    },
});
async function refreshLayer(parent: string, parentPath: string, oldNodes: DirNode[], commit: (nodes: DirNode[]) => void) {
    const items = (await fetchDir(parent, parentPath === '/' ? '/' : '', true)).items;
    const oldByFid = new Map(oldNodes.map((n) => [n.fid, n]));
    const nodes = items.map((it) => toNode(it, parentPath, parent));
    for (const n of nodes) {
        const o = oldByFid.get(n.fid);
        if (o && o.open && o.loaded && n.is_dir) {
            n.kids = await refreshLayer(n.fid, n.path, o.kids, () => { });
            n.loaded = true;
            n.open = true;
        }
    }
    commit(nodes);
    return nodes;
}
function toNode(it: DirItem, parentPath: string, parentKey: string): DirNode {
    return {
        ...it,
        path: (parentPath === '/' ? '' : parentPath) + '/' + it.name,
        parentKey,
        loaded: !it.is_dir,
        open: false,
        loading: false,
        kids: [],
    };
}
const root = reactive<{
    items: DirNode[];
    loading: boolean;
    error: string;
    retry: string;
}>({
    items: [],
    loading: false,
    error: '',
    retry: '',
});
const sel = ref('');
const currentSel = computed(() => (props.nodes ? props.selected ?? '' : sel.value));
async function fetchDir(fid: string, path = '', force = false, onStatus?: (txt: string) => void): Promise<{
    cached: boolean;
    items: DirItem[];
}> {
    let lastErr: unknown = null;
    for (let i = 0; i < 2; i++) {
        try {
            await paceIfHot();
            const meta = await getFilesListMeta(props.type, fid, path, force, props.accId ?? null);
            noteCache(meta.cached);
            onStatus?.('');
            return meta;
        }
        catch (e) {
            lastErr = e;
            noteCache(false);
            if ((e as {
                response?: {
                    status?: number;
                };
            })?.response?.status === 429)
                break;
            if (i < 1) {
                onStatus?.('目录加载失败，正在重试 1 次 …');
                await new Promise((r) => setTimeout(r, 2000));
            }
        }
    }
    onStatus?.('');
    throw lastErr;
}
async function loadRoot(force = false) {
    root.loading = true;
    root.error = '';
    root.retry = '';
    try {
        const meta = await fetchDir('0', '/', force, (t) => (root.retry = t));
        root.items = meta.items.filter((it) => it.is_dir).map((it) => toNode(it, '/', '/'));
    }
    catch (e: unknown) {
        root.error = (e as {
            response?: {
                data?: {
                    detail?: string;
                };
            };
        })?.response?.data?.detail || '目录加载失败';
    }
    finally {
        root.loading = false;
        root.retry = '';
    }
}
async function loadKids(n: DirNode) {
    n.loading = true;
    try {
        const meta = await fetchDir(n.fid);
        n.kids = meta.items.filter((it) => it.is_dir).map((it) => toNode(it, n.path, n.fid));
        n.loaded = true;
    }
    catch (e: unknown) {
        message.error((e as {
            response?: {
                data?: {
                    detail?: string;
                };
            };
        })?.response?.data?.detail || '子目录加载失败');
    }
    finally {
        n.loading = false;
    }
}
async function toggle(n: DirNode) {
    if (!n.is_dir)
        return;
    if (n.open) {
        n.open = false;
        return;
    }
    if (!n.loaded)
        await loadKids(n);
    n.open = true;
}
function pick(n: DirNode) {
    if (!n.is_dir)
        return;
    sel.value = n.path;
    emit('select', n.path, n.fid);
}
function onChildSelect(p: string, f: string) {
    sel.value = p;
    emit('select', p, f);
}
async function init(force = false) {
    if (props.rootPath && props.rootPath !== '/') {
        root.loading = true;
        try {
            const meta = await fetchDir('0', props.rootPath, force, (t) => (root.retry = t));
            root.items = meta.items.filter((it) => it.is_dir).map((it) => toNode(it, props.rootPath!, props.rootPath!));
            root.loading = false;
            sel.value = props.rootPath;
            return;
        }
        catch {
            root.loading = false;
            root.retry = '';
        }
    }
    await loadRoot();
}
const editOpen = ref(false);
const editMode = ref<'create' | 'rename'>('create');
const editName = ref('');
const editBusy = ref(false);
const rootKey = computed(() => (props.rootPath && props.rootPath !== '/' ? props.rootPath : '/'));
function findNode(items: DirNode[], path: string): DirNode | null {
    for (const n of items) {
        if (n.path === path)
            return n;
        const hit = findNode(n.kids, path);
        if (hit)
            return hit;
    }
    return null;
}
const selNode = computed(() => (props.nodes ? null : findNode(root.items, currentSel.value)));
function removeFromTree(items: DirNode[], path: string): boolean {
    const i = items.findIndex((n) => n.path === path);
    if (i >= 0) {
        items.splice(i, 1);
        return true;
    }
    return items.some((n) => removeFromTree(n.kids, path));
}
function startCreate() {
    editMode.value = 'create';
    editName.value = '';
    editOpen.value = true;
}
function startRename() {
    if (!selNode.value)
        return;
    editMode.value = 'rename';
    editName.value = selNode.value.name;
    editOpen.value = true;
}
async function submitEdit() {
    const name = editName.value.trim();
    if (!name) {
        message.warning('请输入文件夹名称');
        return;
    }
    editBusy.value = true;
    try {
        if (editMode.value === 'create') {
            const target = selNode.value;
            const parentPath = target ? target.path : (props.rootPath || '/');
            const r = await createDir({
                type: props.type,
                accId: props.accId ?? null,
                parentPath,
                parentFid: target?.fid ?? '',
                cacheKey: target ? target.fid : rootKey.value,
                name,
            });
            const node = toNode({ fid: r.fid, name, is_dir: true, size: 0 }, parentPath, target ? target.fid : rootKey.value);
            if (target) {
                target.kids.push(node);
                target.loaded = true;
                target.open = true;
            }
            else {
                root.items.push(node);
            }
            message.success(`已创建「${name}」`);
        }
        else {
            const n = selNode.value;
            if (!n)
                return;
            const r = await renameDir({
                type: props.type,
                accId: props.accId ?? null,
                path: n.path,
                fid: n.fid,
                cacheKey: n.parentKey,
                newName: name,
            });
            const oldPath = n.path;
            n.name = name;
            n.fid = r.fid;
            n.path = r.path;
            n.kids = [];
            n.loaded = false;
            n.open = false;
            if (sel.value === oldPath) {
                sel.value = r.path;
                emit('select', r.path, r.fid);
            }
            message.success(`已重命名为「${name}」`);
        }
        editOpen.value = false;
    }
    catch (e: unknown) {
        const detail = (e as {
            response?: {
                data?: {
                    detail?: string;
                };
            };
        })?.response?.data?.detail;
        message.error(detail || '操作失败');
    }
    finally {
        editBusy.value = false;
    }
}
async function doDelete() {
    const n = selNode.value;
    if (!n)
        return;
    try {
        await deleteDir({ type: props.type, accId: props.accId ?? null, path: n.path, fid: n.fid, cacheKey: n.parentKey });
        removeFromTree(root.items, n.path);
        if (sel.value === n.path || sel.value.startsWith(n.path + '/')) {
            sel.value = props.rootPath || '/';
            emit('select', sel.value, '');
        }
        message.success(`已删除「${n.name}」`);
    }
    catch (e: unknown) {
        const detail = (e as {
            response?: {
                data?: {
                    detail?: string;
                };
            };
        })?.response?.data?.detail;
        message.error(detail || '删除失败');
    }
}
onMounted(() => {
    if (!props.nodes)
        init();
});
watch(() => [props.type, props.accId], () => {
    if (props.nodes)
        return;
    sel.value = '';
    root.items = [];
    init();
});
watch(() => props.rootPath, (v, old) => {
    if (props.nodes || v === old)
        return;
    sel.value = '';
    root.items = [];
    init();
});
</script>

<template>
  
  <template v-if="nodes">
    <template v-for="n in nodes" :key="n.fid">
      <div
        class="ldt-row"
        :class="{ sel: n.is_dir && n.path === currentSel }"
        @click="pick(n)"
      >
        <span class="ldt-caret" @click.stop="toggle(n)">
          <LoadingOutlined v-if="n.loading" />
          <RightOutlined v-else-if="n.is_dir" :class="{ open: n.open }" />
        </span>
        <FolderOutlined v-if="n.is_dir" style="color: var(--primary)" />
        <span class="ldt-name">{{ n.name }}</span>
      </div>
      <div v-if="n.is_dir && n.open && n.loaded" class="ldt-kids">
        <LazyDirTree :type="type" :nodes="n.kids" :path-base="n.path" :selected="currentSel" @select="onChildSelect" />
      </div>
    </template>
  </template>

  
  <template v-else>
    <div v-if="!hideToolbar" class="ldt-toolbar">
      <a-button class="ldt-btn ldt-btn-new" @click="startCreate"><PlusOutlined />新建</a-button>
      <a-button class="ldt-btn ldt-btn-ren" :disabled="!selNode" @click="startRename"><EditOutlined />重命名</a-button>
      <a-popconfirm title="删除该文件夹及其全部内容？" ok-text="删除" cancel-text="取消" :disabled="!selNode" @confirm="doDelete">
        <a-button class="ldt-btn ldt-btn-del" :disabled="!selNode"><DeleteOutlined />删除</a-button>
      </a-popconfirm>
      <span class="ldt-toolbar-tip">针对选中的目录</span>
    </div>
    <div v-if="root.loading" class="ldt-tip">
      <LoadingOutlined /> {{ root.retry || '正在加载目录…' }}
    </div>
    <div v-else-if="root.error" class="ldt-tip ldt-err">
      {{ root.error }}
      <a-button size="small" style="margin-left: 8px" @click="loadRoot(true)">重试</a-button>
    </div>
    <div v-else class="ldt ldt-scroll">
      
      <div v-if="!root.items.length" class="ldt-tip">
        {{ props.rootPath ? '该目录下没有子目录（网盘侧就是空的）' : '网盘里还没有文件夹' }}
      </div>
      <template v-for="n in root.items" :key="n.fid">
        <div
          class="ldt-row"
          :class="{ sel: n.is_dir && n.path === currentSel }"
          @click="pick(n)"
        >
          <span class="ldt-caret" @click.stop="toggle(n)">
            <LoadingOutlined v-if="n.loading" />
            <RightOutlined v-else-if="n.is_dir" :class="{ open: n.open }" />
          </span>
          <FolderOutlined v-if="n.is_dir" style="color: var(--primary)" />
          <span class="ldt-name">{{ n.name }}</span>
        </div>
        <div v-if="n.is_dir && n.open && n.loaded" class="ldt-kids">
          <LazyDirTree :type="type" :nodes="n.kids" :path-base="n.path" :selected="currentSel" @select="onChildSelect" />
        </div>
      </template>
    </div>

    <a-modal
      v-model:open="editOpen"
      :title="editMode === 'create' ? '新建文件夹' : '重命名文件夹'"
      :confirm-loading="editBusy"
      :width="380"
      @ok="submitEdit"
    >
      <a-input v-model:value="editName" placeholder="文件夹名称" maxlength="100" @press-enter="submitEdit" />
    </a-modal>
  </template>
</template>

<style scoped>
.ldt { font-size: 13px; }
.ldt-toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.ldt-toolbar-tip { font-size: 12px; color: var(--text3); }
/* 工具条按钮：紧凑文字级描边小按钮（字号=目录名 13px，高 24px），三色系：
   新建=主题蓝 / 重命名=橙 / 删除=红；平时淡色，悬停加深 */
.ldt-btn {
  height: 24px;
  padding: 0 8px;
  font-size: 13px;
  line-height: 1;
  border-radius: 6px;
  background: transparent;
  box-shadow: none;
}
.ldt-btn .anticon { font-size: 12px; }
.ldt-btn-new {
  color: var(--primary);
  border-color: color-mix(in srgb, var(--primary) 35%, var(--border));
}
.ldt-btn-new:hover, .ldt-btn-new:focus-visible {
  border-color: var(--primary);
  background: color-mix(in srgb, var(--primary) 8%, transparent);
}
.ldt-btn-ren {
  color: #fa8c16;
  border-color: color-mix(in srgb, #fa8c16 35%, var(--border));
}
.ldt-btn-ren:hover, .ldt-btn-ren:focus-visible {
  border-color: #fa8c16;
  background: color-mix(in srgb, #fa8c16 8%, transparent);
}
.ldt-btn-del {
  color: var(--error);
  border-color: color-mix(in srgb, var(--error) 30%, var(--border));
}
.ldt-btn-del:hover, .ldt-btn-del:focus-visible {
  border-color: var(--error);
  background: color-mix(in srgb, var(--error) 7%, transparent);
}
.ldt-btn:disabled {
  color: var(--text4);
  border-color: var(--split);
  background: transparent;
}
/* 限高滚动：目录树过长时内部滚动，弹窗标题/底部按钮始终可见 */
.ldt-scroll { max-height: min(55vh, 480px); overflow-y: auto; overscroll-behavior: contain; }
.ldt-tip { padding: 18px 0; text-align: center; color: var(--text3); }
.ldt-err { color: var(--error); }
.ldt-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  border-radius: 8px;
  cursor: pointer;
  user-select: none;
  white-space: nowrap;
  transition: background 0.15s;
}
.ldt-row:hover { background: var(--hover); }
.ldt-row.sel { background: var(--primary-bg); color: var(--primary); font-weight: 500; }
.ldt-caret { width: 14px; text-align: center; color: var(--text4); font-size: 10px; flex: none; }
.ldt-caret .open { transform: rotate(90deg); }
.ldt-kids { margin-left: 16px; border-left: 1px dashed var(--split); padding-left: 8px; }
.ldt-name { overflow: hidden; text-overflow: ellipsis; }
</style>
