<script setup lang="ts">
/* 懒加载目录树（真实网盘目录）：
 * - 根模式：不传 nodes，挂载即拉根一层；选中目录按需拉子层；
 * - 递归模式：父层传入 nodes + pathBase，子层组件只负责渲染与再加载。
 * 单选目录，选中即发出完整路径；错误就地提示不炸整棵树。 */
import { onMounted, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { RightOutlined, LoadingOutlined, FolderOutlined } from '@ant-design/icons-vue'
import { getFilesList, type DirItem } from '@/api/modules/files'
import type { MainDriveType } from '@/types/model'

interface DirNode extends DirItem {
  path: string
  loaded: boolean
  open: boolean
  loading: boolean
  kids: DirNode[]
}

const props = defineProps<{
  type: MainDriveType
  /** 递归模式：父层传入的子节点；不传 = 根模式（自己加载根层） */
  nodes?: DirNode[]
  /** 递归模式：本层对应的父路径 */
  pathBase?: string
}>()

const emit = defineEmits<{ (e: 'select', path: string, fid: string): void }>()

/** 暴露给调用方：强制刷新根层（绕过后端目录缓存直连重拉） */
defineExpose({ reload: () => loadRoot(true) })

function toNode(it: DirItem, parentPath: string): DirNode {
  return {
    ...it,
    path: (parentPath === '/' ? '' : parentPath) + '/' + it.name,
    loaded: !it.is_dir,
    open: false,
    loading: false,
    kids: [],
  }
}

const root = reactive<{ items: DirNode[]; loading: boolean; error: string }>({
  items: [],
  loading: false,
  error: '',
})
const selectedPath = ref('')

async function loadRoot(force = false) {
  root.loading = true
  root.error = ''
  try {
    const items = await getFilesList(props.type, '0', '/', force)
    root.items = items.map((it) => toNode(it, '/'))
  } catch (e: unknown) {
    root.error = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '目录加载失败'
  } finally {
    root.loading = false
  }
}

/** 点箭头：只展开/收起，不改变选中项（避免「想展开看看」被误当成选目录）。 */
async function toggle(n: DirNode) {
  if (!n.is_dir) return
  if (n.open) {
    n.open = false
    return
  }
  if (n.loaded) {
    n.open = true
    return
  }
  n.loading = true
  try {
    const items = await getFilesList(props.type, n.fid)
    n.kids = items.map((it) => toNode(it, n.path))
    n.loaded = true
    n.open = true
  } catch (e: unknown) {
    message.error((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '子目录加载失败')
  } finally {
    n.loading = false
  }
}

/** 点行：只选中（发 select 事件，带 fid 供调用方做目录预热），要不要保存交给调用方决定。 */
function pick(n: DirNode) {
  if (!n.is_dir) return
  selectedPath.value = n.path
  emit('select', n.path, n.fid)
}

onMounted(() => {
  if (!props.nodes) loadRoot()
})

/* 切换网盘（同一弹窗复用组件）必须整树重载：
 * 否则会残留上一个网盘的目录列表和错误信息——「夸克弹窗里显示百度的报错」就是这么来的。 */
watch(
  () => props.type,
  () => {
    if (props.nodes) return // 递归子层：type 随父级一起变，重建交给父级
    selectedPath.value = ''
    root.items = []
    loadRoot()
  },
)
</script>

<template>
  <!-- 递归模式：渲染父层传入的节点 -->
  <template v-if="nodes">
    <template v-for="n in nodes" :key="n.fid">
      <div
        class="ldt-row"
        :class="{ sel: n.is_dir && n.path === selectedPath }"
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
        <LazyDirTree :type="type" :nodes="n.kids" :path-base="n.path" @select="(p: string, f: string) => emit('select', p, f)" />
      </div>
    </template>
  </template>

  <!-- 根模式：自己拉根一层 -->
  <template v-else>
    <div v-if="root.loading" class="ldt-tip"><LoadingOutlined /> 正在加载目录…</div>
    <div v-else-if="root.error" class="ldt-tip ldt-err">
      {{ root.error }}
      <a-button size="small" style="margin-left: 8px" @click="loadRoot(true)">重试</a-button>
    </div>
    <template v-else>
      <!-- ⚠️ 行与子层必须成对渲染：早前拆成两个 v-for，导致展开的子目录
           被堆到列表末尾、看起来像挂在最后一个根目录下面。 -->
      <template v-for="n in root.items" :key="n.fid">
        <div
          class="ldt-row"
          :class="{ sel: n.is_dir && n.path === selectedPath }"
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
          <LazyDirTree :type="type" :nodes="n.kids" :path-base="n.path" @select="(p: string, f: string) => emit('select', p, f)" />
        </div>
      </template>
    </template>
  </template>
</template>

<style scoped>
.ldt { font-size: 13px; }
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
