<script lang="ts">
/** 由搜索页行按钮带入的目标（网盘类型 + 资源名 + 体积）；供主页面拼参数用 */
export interface TransferTarget {
  type: 'baidu' | 'quark' | '115' | '123' | 'ali' | 'xunlei' | 'uc'
  name: string
  size: string
  /** 真实转存需要：分享链接与提取码 */
  url?: string
  share_code?: string
}
</script>

<script setup lang="ts">
/* 转存弹窗（原型 _shell.html 的 transferMask 移植）。
 * 分享内容收成一行摘要，「查看」展开分享树勾选可只转存部分；
 * 「保存到我的网盘」目录树选目标位置（默认国产剧）；选项条只有
 * 包含子目录 / 转存后触发 QMS —— 手动转存不接 Server 酱（只有自动转存有）。
 * 「开始转存」= pkQueue.enqueue 入队即走，绝无内联进度条。 */
import { computed, provide, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { FolderOutlined } from '@ant-design/icons-vue'
import PkTree from '@/components/PkTree.vue'
import ShareTree from './ShareTree.vue'
import { pkQueue } from '@/queue/engine'
import { DRIVE_META } from '@/api/mock/meta'
import { SHARE_TREE, MINE_TREE } from '@/api/mock/tree'
import type { TreeNode } from '@/types/model'

const props = defineProps<{ open: boolean; target: TransferTarget | null }>()
const emit = defineEmits<{ (e: 'update:open', v: boolean): void }>()

const DEFAULT_DIR = '/我的资源/影视/电视剧/国产剧'

const meta = computed(() => (props.target ? DRIVE_META[props.target.type] : null))
/** 分享摘要行：资源名 + 「N 项 · X GB」跟着资源走（项数 mock 固定 12） */
const sumMeta = computed(() => `12 项 · ${props.target?.size || '82.4 GB'}`)

/* ---- 分享树（勾选） ---- */
const checked = ref(new Set<string>())

/** key = 父链 + 节点名：同名文件在不同目录不串 */
function keyOf(base: string, node: TreeNode): string {
  return base + '/' + node.name
}
function collectKeys(node: TreeNode, base: string): string[] {
  const k = keyOf(base, node)
  const out = [k]
  for (const c of node.kids || []) out.push(...collectKeys(c, k))
  return out
}
provide('shareCheck', {
  checked,
  keyOf,
  collectKeys,
  toggle(keys: string[], val: boolean) {
    const s = new Set(checked.value)
    for (const k of keys) (val ? s.add(k) : s.delete(k))
    checked.value = s
  },
})

/** 分享树 mock：结构取 SHARE_TREE，顶层名换成当前资源（原型 buildShareData 行为） */
const shareData = computed<TreeNode[]>(() => {
  const clone = JSON.parse(JSON.stringify(SHARE_TREE)) as TreeNode[]
  if (clone[0] && props.target) clone[0].name = props.target.name
  return clone
})

/** 勾选的叶子文件数（0 = 全部内容） */
const checkedFiles = computed(() => {
  let n = 0
  const walk = (nodes: TreeNode[], base: string) => {
    for (const nd of nodes) {
      const k = keyOf(base, nd)
      if (nd.kids?.length) walk(nd.kids, k)
      else if (checked.value.has(k)) n++
    }
  }
  walk(shareData.value, '')
  return n
})

const shareOpen = ref(false)

/* ---- 目标目录树 ---- */
const selectedDir = ref(DEFAULT_DIR)
function onPick(node: TreeNode) {
  // 只认有 path 的节点（分享树式节点没有 path，这里树里都有）
  if (node.path) selectedDir.value = node.path
}

function mkfolder() {
  // 原型行为：prompt 输入名字 → toast 反馈（真实版换成后端 mkdir）
  const n = window.prompt('新文件夹名称：', '庆余年2')
  if (n) message.info(`将在 ${selectedDir.value} 下创建 ${n}`)
}

/* ---- 选项（手动转存没有 Server 酱推送，别加回来） ---- */
const includeSub = ref(true)
const postQms = ref(true)

/** 每次打开重置：分享树收起、勾选清空、目标位置回默认国产剧 */
watch(
  () => props.open,
  (v) => {
    if (!v) return
    checked.value = new Set()
    shareOpen.value = false
    selectedDir.value = DEFAULT_DIR
    includeSub.value = true
    postQms.value = true
  },
)

function close() {
  emit('update:open', false)
}

/** 入队即走：toast 报位次、弹窗立即关闭 */
function start() {
  const t = props.target
  if (!t) return
  const files = parseInt((sumMeta.value.match(/(\d+)\s*项/) || [])[1] || '', 10) || 12
  const pos = pkQueue.enqueue({
    name: t.name,
    type: t.type,
    path: selectedDir.value,
    files,
    size: t.size,
    share_url: t.url,
    share_code: t.share_code,
  })
  message.success(`已加入转存队列 · 当前第 ${pos} 位，完成后去「转存记录 → 队列」看日志`)
  close()
}
</script>

<template>
  <a-modal
    :open="open"
    :width="920"
    centered
    destroy-on-close
    :footer="null"
    @update:open="(v: boolean) => emit('update:open', v)"
  >
    <template #title>
      <div class="tm-head">
        <span v-if="meta" class="chip tm-chip" :style="{ background: meta.color }">{{ meta.name }}</span>
        <span>转存到{{ meta?.full }}</span>
      </div>
    </template>

    <div v-if="target" class="tm-body">
      <!-- 分享内容：总结一行，「查看」展开树（宽度不变） -->
      <div class="share-sum">
        <span class="share-sum-ic"><FolderOutlined /></span>
        <div class="share-sum-main">
          <b :title="target.name">{{ target.name }}</b>
          <span>{{ sumMeta }}</span>
        </div>
        <a-button size="small" @click="shareOpen = !shareOpen">{{ shareOpen ? '收起' : '查看' }}</a-button>
      </div>
      <div v-show="shareOpen" class="share-tree-wrap">
        <ShareTree :nodes="shareData" base-key="" />
      </div>

      <!-- 我的网盘：唯一可操作区（选目标位置） -->
      <div class="pane" style="margin-top: 14px">
        <div class="pane-hd">
          <span>保存到我的网盘</span>
          <a-button size="small" @click="mkfolder">新建文件夹</a-button>
        </div>
        <div class="pane-bd">
          <PkTree :nodes="MINE_TREE" selectable :default-expand-depth="2" @select="onPick" />
        </div>
      </div>
      <div class="bcrumb">
        <span class="muted" style="color: var(--text3)">目标位置</span>
        <span>{{ selectedDir }}</span>
      </div>

      <div class="filterbar" style="margin-top: 16px">
        <span class="muted">选项</span>
        <a-checkbox v-model:checked="includeSub">包含子目录</a-checkbox>
        <a-checkbox v-model:checked="postQms">转存后触发 QMS 整理</a-checkbox>
        <!-- 手动转存不接 Server 酱：结果实时看日志就行，只有自动转存（任务弹窗）才有推送 -->
      </div>
    </div>

    <div class="tm-foot">
      <span class="small muted">
        {{ checkedFiles > 0 ? `已勾选 ${checkedFiles} 个文件 · 只转存勾选内容` : '选中分享内的子文件夹可只转存部分内容' }}
      </span>
      <span style="flex: 1"></span>
      <a-button @click="close">取消</a-button>
      <a-button type="primary" @click="start">开始转存</a-button>
    </div>
  </a-modal>
</template>

<style scoped>
.tm-head { display: flex; align-items: center; gap: 9px; font-size: 16px; font-weight: 600; }
.tm-chip { width: 24px; height: 24px; border-radius: 6px; font-size: 11px; }
.tm-body { max-height: 62vh; overflow: auto; padding: 4px 2px; }
.tm-foot {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-top: 14px;
  margin-top: 4px;
  border-top: 1px solid var(--split);
}
.tm-foot .small { flex: none; }

/* 移动端（<768px）：底部操作区改两行（说明一行 + 按钮铺满） */
@media (max-width: 767px) {
  .tm-body { max-height: 56dvh; }
  .tm-foot { flex-wrap: wrap; }
  .tm-foot .small { flex: 1 1 100%; margin-bottom: 2px; }
  .tm-foot :deep(.ant-btn) { flex: 1; }
}
</style>
