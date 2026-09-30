<script setup lang="ts">
/* 懒加载目录树（真实网盘目录）：
 * - 根模式：不传 nodes，挂载即拉根一层；选中目录按需拉子层；
 * - 递归模式：父层传入 nodes + pathBase，子层组件只负责渲染与再加载。
 * 单选目录，选中即发出完整路径；错误就地提示不炸整棵树。 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
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
  /** 浏览哪个账号的目录；空 = 该类型默认账号（后端缓存按账号隔离） */
  accId?: number | null
  /** 当前选中路径（递归模式由根实例一路传下来；选中态全局唯一） */
  selected?: string
  /** 初始路径：根模式挂载时自动逐层展开到这里并选中（如已配置的默认目录） */
  initialPath?: string
  /** 根路径锁定：配置后树只展示该目录的子目录（以默认目标目录为根）；空=从网盘真根浏览 */
  rootPath?: string
}>()

const emit = defineEmits<{ (e: 'select', path: string, fid: string): void }>()

/**
 * 暴露给调用方：刷新"当前可见层级"（根 + 所有已展开的层）。
 * 逐层 force 重拉（绕后端缓存），按 fid 对齐保留展开/选中状态——不是废弃整树重头再来。
 */
defineExpose({
  reload: () => {
    if (props.rootPath && props.rootPath !== '/') return init() // 锁定根：整树重走（含锁定目录）
    return refreshLayer('0', '/', root.items, (items) => (root.items = items))
  },
})

/** force 重拉一层；旧子节点里已展开的按 fid 对齐递归强刷。返回该层新节点。 */
async function refreshLayer(
  parent: string,
  parentPath: string,
  oldNodes: DirNode[],
  commit: (nodes: DirNode[]) => void,
) {
  const items = await getFilesList(props.type, parent, parentPath === '/' ? '/' : '', true, props.accId ?? null)
  const oldByFid = new Map(oldNodes.map((n) => [n.fid, n]))
  const nodes = items.map((it) => toNode(it, parentPath))
  for (const n of nodes) {
    const o = oldByFid.get(n.fid)
    if (o && o.open && o.loaded && n.is_dir) {
      // 该子层本来就展开着：跟着强刷，保持展开
      n.kids = await refreshLayer(n.fid, n.path, o.kids, () => {})
      n.loaded = true
      n.open = true
    }
  }
  commit(nodes)
  return nodes
}

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
const sel = ref('')
/** 选中态只在根实例维护：递归实例沿 props 透传，否则每层各记一份，整条链都会「亮着」 */
const currentSel = computed(() => (props.nodes ? props.selected ?? '' : sel.value))

async function loadRoot(force = false) {
  root.loading = true
  root.error = ''
  try {
    const items = await getFilesList(props.type, '0', '/', force, props.accId ?? null)
    // 目录选择器只关心文件夹：文件一律过滤
    root.items = items.filter((it) => it.is_dir).map((it) => toNode(it, '/'))
  } catch (e: unknown) {
    root.error = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '目录加载失败'
  } finally {
    root.loading = false
  }
}

/** 拉一层子目录（toggle 与初始下钻共用）。 */
async function loadKids(n: DirNode) {
  n.loading = true
  try {
    const items = await getFilesList(props.type, n.fid, '', false, props.accId ?? null)
    n.kids = items.filter((it) => it.is_dir).map((it) => toNode(it, n.path))
    n.loaded = true
  } catch (e: unknown) {
    message.error((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '子目录加载失败')
  } finally {
    n.loading = false
  }
}

/** 点箭头：只展开/收起，不改变选中项（避免「想展开看看」被误当成选目录）。 */
async function toggle(n: DirNode) {
  if (!n.is_dir) return
  if (n.open) {
    n.open = false
    return
  }
  if (!n.loaded) await loadKids(n)
  n.open = true
}

/** 挂载/重建后沿初始路径逐层展开（拿不到的段静默停住），最后一层设为选中 */
async function walkTo(path: string) {
  const segs = path.split('/').filter(Boolean)
  let level: DirNode[] = root.items
  let acc = ''
  for (const s of segs) {
    acc += '/' + s
    const node = level.find((n) => n.path === acc)
    if (!node) return null
    if (!node.loaded) await loadKids(node)
    node.open = true
    level = node.kids
  }
  sel.value = path
  return null
}

/** 下钻到指定路径（绝对路径），逐层展开；用于初始路径比锁定根更深时 */
async function descend(path: string): Promise<void> {
  const segs = path.split('/').filter(Boolean)
  let level: DirNode[] = root.items
  let acc = ''
  for (const s of segs) {
    acc += '/' + s
    const node = level.find((n) => n.path === acc) || null
    if (!node) return
    if (!node.loaded) await loadKids(node)
    node.open = true
    level = node.kids
  }
}

/** 点行：只选中（发 select 事件，带 fid 供调用方做目录预热），要不要保存交给调用方决定。 */
function pick(n: DirNode) {
  if (!n.is_dir) return
  // 根路径锁定时允许选中锁定目录本身（= 就存在这里）
  sel.value = n.path
  emit('select', n.path, n.fid)
}

/** 子层选中：先回写根实例的选中态（props 一路传下去高亮才跟手），再向调用方转发 */
function onChildSelect(p: string, f: string) {
  sel.value = p
  emit('select', p, f)
}

async function init() {
  if (props.rootPath && props.rootPath !== '/') {
    // 配了默认目录：一个请求让后台按路径解析并回传它的第一层（吃目录缓存），
    // 树只展示默认目录的子目录；选中默认为锁定根本身
    root.loading = true
    try {
      const items = await getFilesList(props.type, '0', props.rootPath, false, props.accId ?? null)
      root.items = items.filter((it) => it.is_dir).map((it) => toNode(it, props.rootPath!))
      root.loading = false
      sel.value = props.rootPath
      // 初始路径比锁定根更深（编辑的是默认目录下的子目录）：逐层展开到它（走缓存）
      if (props.initialPath && props.initialPath !== props.rootPath && props.initialPath.startsWith(props.rootPath + '/')) {
        await descend(props.initialPath)
        sel.value = props.initialPath
      }
      return
    } catch {
      // 锁定目录在网盘里不存在/解析失败：回退真根浏览，别白屏
      root.loading = false
    }
  }
  await loadRoot()
  if (props.initialPath && props.initialPath !== '/') await walkTo(props.initialPath)
}

onMounted(() => {
  if (!props.nodes) init()
})

/* 切换网盘（同一弹窗复用组件）必须整树重载：
 * 否则会残留上一个网盘的目录列表和错误信息——「夸克弹窗里显示百度的报错」就是这么来的。 */
watch(
  () => [props.type, props.accId],
  () => {
    if (props.nodes) return // 递归子层：type/accId 随父级一起变，重建交给父级
    sel.value = ''
    root.items = []
    init()
  },
)
</script>

<template>
  <!-- 递归模式：渲染父层传入的节点 -->
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

  <!-- 根模式：自己拉根一层；限高滚动，防止目录太长把弹窗底部按钮顶出屏幕 -->
  <template v-else>
    <div v-if="root.loading" class="ldt-tip"><LoadingOutlined /> 正在加载目录…</div>
    <div v-else-if="root.error" class="ldt-tip ldt-err">
      {{ root.error }}
      <a-button size="small" style="margin-left: 8px" @click="loadRoot(true)">重试</a-button>
    </div>
    <div v-else class="ldt ldt-scroll">
      <!-- 根锁定时空目录给提示，别像白屏 -->
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
  </template>
</template>

<style scoped>
.ldt { font-size: 13px; }
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
