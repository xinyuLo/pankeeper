<script setup lang="ts">
/* 「查看」弹窗：分享内文件树。走后端分享清单缓存（转存跑完自动刷新），
 * 「刷新」按钮忽略缓存直连重拉；mock 模式用演示树（SHARE_TREE）。
 * 数据源二选一：taskId（自动转存任务，走 /pa/tasks/{id}/share-files）
 * 或 fetcher（记录页等无任务 id 的场景，由调用方注入取数函数）。 */
import { ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { LoadingOutlined, FolderOutlined, FileOutlined } from '@ant-design/icons-vue'
import { getShareFiles } from '@/api/modules/tasks'
import type { ShareFilesMeta } from '@/api/modules/tasks'
import { USE_MOCK } from '@/api/http'
import { SHARE_TREE } from '@/api/mock/tree'

interface TreeNode {
  name: string
  is_dir: boolean
  size: number
  kids: TreeNode[]
}

import { useBackGuard } from '@/composables/useBackGuard'
const props = defineProps<{ open: boolean; taskId: number | null; taskName: string; fetcher?: (refresh: boolean) => Promise<ShareFilesMeta> }>()
const emit = defineEmits<{ (e: 'update:open', v: boolean): void }>()
useBackGuard(() => props.open, () => emit('update:open', false))

const loading = ref(false)
const refreshing = ref(false)
const error = ref('')
const tree = ref<TreeNode[]>([])
const total = ref(0)
const cachedAt = ref(0)
const fresh = ref(false)
const openSet = ref(new Set<string>())

function fmtSize(n: number): string {
  if (!n) return ''
  if (n > 1024 ** 3) return (n / 1024 ** 3).toFixed(1) + ' GB'
  if (n > 1024 ** 2) return (n / 1024 ** 2).toFixed(0) + ' MB'
  return Math.max(1, Math.round(n / 1024)) + ' KB'
}
function fmtTs(ts: number): string {
  const d = new Date(ts)
  const p = (v: number) => String(v).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

function toggleOpen(key: string) {
  const s = new Set(openSet.value)
  if (s.has(key)) s.delete(key)
  else s.add(key)
  openSet.value = s
}

async function load(refresh = false) {
  if (!props.taskId && !props.fetcher) return
  refresh ? (refreshing.value = true) : (loading.value = true)
  error.value = ''
  openSet.value = new Set()
  try {
    const res = props.fetcher ? await props.fetcher(refresh) : await getShareFiles(props.taskId!, refresh)
    if (!res.total && !res.tree.length) {
      // 空清单 = 死链典型形态（页面正常但没文件）；兜底历史缓存里的空数据
      error.value = '分享内容为空（0 个文件），链接可能已失效'
      tree.value = []
      return
    }
    tree.value = res.tree
    total.value = res.total
    cachedAt.value = res.cached_at * 1000
    fresh.value = res.fresh
    if (refresh) message.success('文件清单已刷新')
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    error.value = detail || '获取分享内容失败'
    tree.value = []
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

watch(
  () => props.open,
  (v) => {
    if (!v) return
    if (USE_MOCK) {
      // mock：演示树（顶层名换成任务名）
      error.value = ''
      total.value = 12
      cachedAt.value = Date.now()
      fresh.value = true
      tree.value = JSON.parse(JSON.stringify(SHARE_TREE)).map((n: TreeNode) => ({
        ...n,
        name: props.taskName || n.name,
        is_dir: true,
        kids: (n.kids || []).map((k: TreeNode) => ({ ...k, is_dir: !!k.kids?.length, kids: k.kids || [] })),
      }))
      loading.value = false
      return
    }
    load()
  },
)

function close() {
  emit('update:open', false)
}

defineExpose({ close })
void message
</script>

<template>
  <a-modal
    :open="open"
    :width="600"
    :title="`分享内容 · ${taskName}`"
    :footer="null"
    destroy-on-close
    @update:open="(v: boolean) => emit('update:open', v)"
  >
    <template #title>
      <div class="sfm-title">
        <span>分享内容 · {{ taskName }}</span>
        <a-button size="small" :loading="refreshing" @click="load(true)">刷新</a-button>
      </div>
    </template>
    <div class="sfm-meta small muted">
      <template v-if="loading"><LoadingOutlined /> 正在获取分享内容…</template>
      <template v-else-if="error">{{ error }}</template>
      <template v-else>
        {{ fresh ? '刚从网盘拉取' : '来自缓存 · 转存执行后自动刷新' }} · 共 {{ total }} 个文件 · 获取于 {{ fmtTs(cachedAt) }}
      </template>
    </div>

    <div v-if="loading" class="sfm-body">
      <div class="sfm-skel" v-for="i in 6" :key="i" :style="{ width: 90 - i * 6 + '%' }"></div>
    </div>

    <div v-else-if="error" class="sfm-body sfm-err">{{ error }}</div>

    <div v-else class="sfm-body">
      <template v-for="n in tree" :key="n.name">
        <div class="sfm-row" :class="{ dir: n.is_dir }" @click="toggleOpen(n.name)">
          <span class="sfm-caret" :class="{ open: openSet.has(n.name) }">
            <RightOutlined v-if="n.is_dir" />
          </span>
          <FolderOutlined v-if="n.is_dir" style="color: var(--primary)" />
          <FileOutlined v-else style="color: var(--text3)" />
          <span class="sfm-name">{{ n.name }}</span>
          <span v-if="!n.is_dir && n.size" class="small muted">{{ fmtSize(n.size) }}</span>
        </div>
        <template v-if="n.is_dir && openSet.has(n.name)">
          <div
            v-for="k in n.kids"
            :key="n.name + '/' + k.name"
            class="sfm-row"
            style="padding-left: 34px"
          >
            <FileOutlined v-if="!k.is_dir" style="color: var(--text3)" />
            <FolderOutlined v-else style="color: var(--primary)" />
            <span class="sfm-name">{{ k.name }}</span>
            <span v-if="!k.is_dir && k.size" class="small muted">{{ fmtSize(k.size) }}</span>
          </div>
        </template>
      </template>
    </div>
  </a-modal>
</template>

<style scoped>
.sfm-title { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding-right: 30px; font-size: 15px; font-weight: 600; }
.sfm-meta { margin-bottom: 10px; }
.sfm-body {
  max-height: 52vh;
  overflow: auto;
  border: 1px solid var(--split);
  border-radius: 10px;
  padding: 8px 10px;
}
.sfm-err { color: var(--error); text-align: center; padding: 28px 0; }
.sfm-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 8px;
  font-size: 13px;
}
.sfm-row.dir { font-weight: 500; cursor: pointer; }
.sfm-row.dir:hover { background: var(--hover); }
.sfm-caret { width: 12px; font-size: 10px; color: var(--text4); transition: transform 0.16s; }
.sfm-caret.open { transform: rotate(90deg); }
.sfm-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sfm-skel {
  height: 18px;
  border-radius: 6px;
  background: linear-gradient(90deg, var(--surface-2), var(--split), var(--surface-2));
  margin: 10px 0;
  animation: sfmPulse 1.2s ease-in-out infinite;
}
@keyframes sfmPulse {
  0%, 100% { opacity: 0.55; }
  50% { opacity: 1; }
}
</style>
