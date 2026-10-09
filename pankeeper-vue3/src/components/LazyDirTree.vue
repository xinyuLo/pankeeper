<script lang="ts">
/* 节流状态必须放在模块级：<script setup> 里声明是每个组件实例一份——转存弹窗、
 * 目录选择弹窗同屏多棵树时各排各的队，等于没有节流（2026-10-07 实锤多路连打撞 115 风控） */
let cooldownUntil = 0
async function paceIfHot() {
  const wait = cooldownUntil - Date.now()
  if (wait > 0) await new Promise((r) => setTimeout(r, wait))
}
function noteCache(cached: boolean) {
  cooldownUntil = cached ? 0 : Date.now() + 600
}
</script>

<script setup lang="ts">
/* 懒加载目录树（真实网盘目录）：
 * - 根模式：不传 nodes，挂载即拉根一层；选中目录按需拉子层；
 * - 递归模式：父层传入 nodes + pathBase，子层组件只负责渲染与再加载。
 * 单选目录，选中即发出完整路径；错误就地提示不炸整棵树。 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { RightOutlined, LoadingOutlined, FolderOutlined, PlusOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons-vue'
import { getFilesListMeta, createDir, renameDir, deleteDir, type DirItem } from '@/api/modules/files'
import type { MainDriveType } from '@/types/model'

interface DirNode extends DirItem {
  path: string
  /** 本节点所在层的目录缓存 key（新建/重命名/删除后后端就地更新那一层用） */
  parentKey: string
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
  /** 隐藏目录管理工具条（新建/重命名/删除）。不传 = 显示。
   *  ⚠️ 别用「manageable?: boolean 默认 true」这种写法——Vue 3.5 起 absent 的
   *  Boolean prop 会被强转成 false，导致"默认 true"永远不成立（2026-10-08 实锤）。
   *  要隐藏的调用方显式传 hide-toolbar 即可。 */
  hideToolbar?: boolean
}>()

const emit = defineEmits<{ (e: 'select', path: string, fid: string): void }>()

/**
 * 暴露给调用方：刷新"当前可见层级"（根 + 所有已展开的层）。
 * 逐层 force 重拉（绕后端缓存），按 fid 对齐保留展开/选中状态——不是废弃整树重头再来。
 */
defineExpose({
  reload: () => {
    // 刷新必须绕后端缓存（force=true）：曾走 init() 不带 force，锁定根模式吃了缓存——
    // 网盘上删掉的目录点刷新还在（2026-10-03 套娃目录删了还在案）。
    if (props.rootPath && props.rootPath !== '/') return init(true)
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
  const items = (await fetchDir(parent, parentPath === '/' ? '/' : '', true)).items
  const oldByFid = new Map(oldNodes.map((n) => [n.fid, n]))
  const nodes = items.map((it) => toNode(it, parentPath, parent))
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

function toNode(it: DirItem, parentPath: string, parentKey: string): DirNode {
  return {
    ...it,
    path: (parentPath === '/' ? '' : parentPath) + '/' + it.name,
    parentKey,
    loaded: !it.is_dir,
    open: false,
    loading: false,
    kids: [],
  }
}

const root = reactive<{ items: DirNode[]; loading: boolean; error: string; retry: string }>({
  items: [],
  loading: false,
  error: '',
  retry: '',
})
const sel = ref('')
/** 选中态只在根实例维护：递归实例沿 props 透传，否则每层各记一份，整条链都会「亮着」 */
const currentSel = computed(() => (props.nodes ? props.selected ?? '' : sel.value))

/** 列目录统一入口：风控节流 + 抖动重试一次——根层/子层/初始加载全走这里。
 * 重试纪律（2026-10-07 收紧，115 越打封越久实锤）：
 * - HTTP 429（后端标记的风控/限频）**一次都不重试**，立即把人话报给树；
 * - 其余失败（网络抖动/5xx）最多补 1 发，不再 3 连发。 */
async function fetchDir(
  fid: string,
  path = '',
  force = false,
  onStatus?: (txt: string) => void,
): Promise<{ cached: boolean; items: DirItem[] }> {
  let lastErr: unknown = null
  for (let i = 0; i < 2; i++) {
    try {
      await paceIfHot()
      const meta = await getFilesListMeta(props.type, fid, path, force, props.accId ?? null)
      noteCache(meta.cached)
      onStatus?.('')
      return meta
    } catch (e) {
      lastErr = e
      noteCache(false) // 请求都没成功，按真打了网盘算，冷却照设
      // 429 = 网盘风控中（115 302/406/验证码），重试只会延长封禁——直接放弃
      if ((e as { response?: { status?: number } })?.response?.status === 429) break
      if (i < 1) {
        onStatus?.('目录加载失败，正在重试 1 次 …')
        await new Promise((r) => setTimeout(r, 2000))
      }
    }
  }
  onStatus?.('')
  throw lastErr
}

async function loadRoot(force = false) {
  root.loading = true
  root.error = ''
  root.retry = ''
  try {
    const meta = await fetchDir('0', '/', force, (t) => (root.retry = t))
    // 目录选择器只关心文件夹：文件一律过滤
    root.items = meta.items.filter((it) => it.is_dir).map((it) => toNode(it, '/', '/'))
  } catch (e: unknown) {
    root.error = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '目录加载失败'
  } finally {
    root.loading = false
    root.retry = ''
  }
}

/** 拉一层子目录（toggle 展开；失败已在 fetchDir 内重试，仍败才提示） */
async function loadKids(n: DirNode) {
  n.loading = true
  try {
    const meta = await fetchDir(n.fid)
    n.kids = meta.items.filter((it) => it.is_dir).map((it) => toNode(it, n.path, n.fid))
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

async function init(force = false) {
  if (props.rootPath && props.rootPath !== '/') {
    // 配了默认目录：一个请求让后台按路径解析并回传它的第一层（默认吃目录缓存，
    // reload 传 force=true 绕缓存），树只展示默认目录的子目录；选中默认为锁定根本身。
    // 刻意不自动下钻到 initialPath——逐层连打容易撞百度风控（-7/-9 实测），
    // 已填路径在底部「已选目录」回显，用户点哪层懒加载哪层（命中缓存不打百度）。
    root.loading = true
    try {
      const meta = await fetchDir('0', props.rootPath, force, (t) => (root.retry = t))
      root.items = meta.items.filter((it) => it.is_dir).map((it) => toNode(it, props.rootPath!, props.rootPath!))
      root.loading = false
      sel.value = props.rootPath
      return
    } catch {
      // 锁定目录在网盘里不存在/解析失败：回退真根浏览，别白屏
      root.loading = false
      root.retry = ''
    }
  }
  await loadRoot()
}

/* ===== 目录管理（新建/重命名/删除，2026-10-03） =====
 * 操作打后端接口，后端改完网盘**就地更新对应层的目录缓存**（dir_cache.update），
 * 这里再同步改本地树节点——全程不重打列目录接口。操作对象是当前选中目录；
 * 没选中时「新建」落在锁定根（或真根）下，重命名/删除禁用。 */
const editOpen = ref(false)
const editMode = ref<'create' | 'rename'>('create')
const editName = ref('')
const editBusy = ref(false)

const rootKey = computed(() => (props.rootPath && props.rootPath !== '/' ? props.rootPath : '/'))

function findNode(items: DirNode[], path: string): DirNode | null {
  for (const n of items) {
    if (n.path === path) return n
    const hit = findNode(n.kids, path)
    if (hit) return hit
  }
  return null
}
const selNode = computed(() => (props.nodes ? null : findNode(root.items, currentSel.value)))

function removeFromTree(items: DirNode[], path: string): boolean {
  const i = items.findIndex((n) => n.path === path)
  if (i >= 0) {
    items.splice(i, 1)
    return true
  }
  return items.some((n) => removeFromTree(n.kids, path))
}

function startCreate() {
  editMode.value = 'create'
  editName.value = ''
  editOpen.value = true
}

function startRename() {
  if (!selNode.value) return
  editMode.value = 'rename'
  editName.value = selNode.value.name
  editOpen.value = true
}

async function submitEdit() {
  const name = editName.value.trim()
  if (!name) {
    message.warning('请输入文件夹名称')
    return
  }
  editBusy.value = true
  try {
    if (editMode.value === 'create') {
      const target = selNode.value
      const parentPath = target ? target.path : (props.rootPath || '/')
      const r = await createDir({
        type: props.type,
        accId: props.accId ?? null,
        parentPath,
        parentFid: target?.fid ?? '',
        cacheKey: target ? target.fid : rootKey.value,
        name,
      })
      const node = toNode({ fid: r.fid, name, is_dir: true, size: 0 }, parentPath, target ? target.fid : rootKey.value)
      if (target) {
        target.kids.push(node)
        target.loaded = true
        target.open = true
      } else {
        root.items.push(node)
      }
      message.success(`已创建「${name}」`)
    } else {
      const n = selNode.value
      if (!n) return
      const r = await renameDir({
        type: props.type,
        accId: props.accId ?? null,
        path: n.path,
        fid: n.fid,
        cacheKey: n.parentKey,
        newName: name,
      })
      const oldPath = n.path
      n.name = name
      n.fid = r.fid
      n.path = r.path
      n.kids = [] // 子树路径全变：收起，展开时按新路径重取（后端映射已同步清理）
      n.loaded = false
      n.open = false
      if (sel.value === oldPath) {
        sel.value = r.path
        emit('select', r.path, r.fid)
      }
      message.success(`已重命名为「${name}」`)
    }
    editOpen.value = false
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(detail || '操作失败')
  } finally {
    editBusy.value = false
  }
}

async function doDelete() {
  const n = selNode.value
  if (!n) return
  try {
    await deleteDir({ type: props.type, accId: props.accId ?? null, path: n.path, fid: n.fid, cacheKey: n.parentKey })
    removeFromTree(root.items, n.path)
    if (sel.value === n.path || sel.value.startsWith(n.path + '/')) {
      sel.value = props.rootPath || '/'
      emit('select', sel.value, '')
    }
    message.success(`已删除「${n.name}」`)
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(detail || '删除失败')
  }
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

/* 根路径锁定（rootPath）晚到也要生效：转存弹窗开树时 rootDirs 还没拉回来，
 * 组件先按"无锁定"挂载、真根都列出来了，等 /1.影视 到了却没人理（2026-10-08 实锤：
 * 夸克配了默认根目录，弹窗树却从网盘真根开始）。rootPath 变化 → 整树按新锁重载。 */
watch(
  () => props.rootPath,
  (v, old) => {
    if (props.nodes || v === old) return
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
