<script setup lang="ts">
/* 排除文件清单弹窗（原型 mtExclMask，560px）—— 入口在任务行的「排除」按钮，
 * 不在编辑弹窗里（设计约定：排除是行级操作，开发时别挪回去）。
 * 缓存 key = 任务分享链接：任务执行时已就地预热（primeExclCache），
 * 这里命中秒开；冷启动才转圈。下次自动刷新跟任务 cron 走，无 cron 退回 30 分钟 TTL。
 * 取消不脏写：勾选是草稿，只有「确定」才回写任务 exclude_count/exclIdx。 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { DRIVE_META } from '@/api/mock/meta'
import {
  EXCL_TTL,
  clearExclCache,
  commitExcl,
  cronNextTs,
  fetchExclFiles,
  fmtHms,
  type PaExclFetch,
  type PaExclFile,
} from '@/api/modules/tasks'
import type { MainDriveType, PaTask } from '@/types/model'

const props = defineProps<{ open: boolean; task: PaTask | null }>()
const emit = defineEmits<{ (e: 'update:open', v: boolean): void; (e: 'committed'): void }>()

const meta = computed(() => (props.task ? DRIVE_META[props.task.type as MainDriveType] : DRIVE_META.baidu))

const files = ref<PaExclFile[]>([])
const sel = ref(new Set<number>())
const loading = ref(false)
const busy = ref(false)
const fresh = ref<boolean | null>(null) // null=还没拿到
const cacheTs = ref(0)
const nextAt = ref(0)
let autoTimer: number | null = null

const cacheKey = computed(() => props.task?.share_url || '')

/* 状态条文案：冷启动橙（刚拉取）/ 命中绿（剩 N 分钟）；
   「下次自动刷新」= cron 下次触发时刻，解析不了才退回 30 分钟 TTL。 */
const statusText = computed(() => {
  if (loading.value) return ''
  if (fresh.value === null) return '○ 首次加载 · 拉取后缓存 30 分钟'
  const hasCron = !!cronNextTs(props.task?.cron || '')
  const nextTxt = '下次自动刷新 ' + fmtHms(nextAt.value) + (hasCron ? '' : '（30 分钟）')
  if (fresh.value) {
    return `○ 刚从网盘重新拉取 · 获取于 ${fmtHms(cacheTs.value)} · ${nextTxt}`
  }
  const mins = Math.max(0, Math.round((nextAt.value - Date.now()) / 60000))
  return `● 命中缓存 · 获取于 ${fmtHms(cacheTs.value)} · ${nextTxt}（剩 ${mins} 分钟）`
})

function scheduleAuto() {
  if (autoTimer) {
    clearTimeout(autoTimer)
    autoTimer = null
  }
  // 先算下次刷新时刻再渲染状态条（顺序反了状态条会拿到 0，原型踩过）
  const cronMs = cronNextTs(props.task?.cron || '')
  nextAt.value = cronMs || cacheTs.value + EXCL_TTL
  autoTimer = window.setTimeout(async () => {
    autoTimer = null
    if (!props.open) return // 弹窗开着才刷；关了就取消
    const res = await fetchExclFiles(cacheKey.value, true)
    applyFetch(res)
    message.info('缓存到期，已自动刷新文件清单')
    scheduleAuto()
  }, Math.max(1000, nextAt.value - Date.now()))
}

function applyFetch(res: PaExclFetch) {
  files.value = res.files
  fresh.value = res.fresh
  cacheTs.value = res.ts
}

watch(
  () => props.open,
  async (v) => {
    if (!v) {
      if (autoTimer) {
        clearTimeout(autoTimer)
        autoTimer = null
      }
      return
    }
    sel.value = new Set(props.task?.exclIdx || []) // 草稿 = 任务里已保存的清单
    files.value = []
    fresh.value = null
    loading.value = true
    const res = await fetchExclFiles(cacheKey.value)
    applyFetch(res)
    loading.value = false
    scheduleAuto()
  },
)

/** 刷新：忽略缓存重拉，保留已勾选项（下标不变，勾选原样带回） */
async function onRefresh() {
  if (busy.value) return // 防连点造成并发请求
  busy.value = true
  const keep = new Set(sel.value)
  clearExclCache(cacheKey.value)
  const res = await fetchExclFiles(cacheKey.value, true)
  applyFetch(res)
  sel.value = keep
  busy.value = false
  scheduleAuto()
  message.success('文件清单已刷新')
}

/** 一键勾选无 MD5：无校验值的文件转存时只能按文件名去重，最值得排除 */
function checkNoMd5() {
  const s = new Set<number>()
  files.value.forEach((f, i) => {
    if (!f.md5) s.add(i)
  })
  sel.value = s
}

function toggle(i: number, e: Event) {
  const s = new Set(sel.value)
  if ((e.target as HTMLInputElement).checked) s.add(i)
  else s.delete(i)
  sel.value = s
}

async function onOk() {
  if (!props.task) return
  const idx = [...sel.value].sort((a, b) => a - b)
  await commitExcl(props.task.id, idx)
  message.success(`已排除 ${idx.length} 个文件`)
  emit('committed')
  close()
}

function close() {
  emit('update:open', false) // 取消不脏写，重开恢复已保存清单
}

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') close()
}
watch(
  () => props.open,
  (v) => {
    if (v) window.addEventListener('keydown', onKey)
    else window.removeEventListener('keydown', onKey)
  },
)
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  if (autoTimer) clearTimeout(autoTimer)
})
</script>

<template>
  <teleport to="body">
    <div v-if="open" class="mt-mask" style="z-index: 1002" @click.self="close">
      <div class="mt-dialog" style="width: 560px">
        <div class="mt-dialog-head">
          <span class="mt-color-dot" :style="{ background: meta.color }"></span>
          <span class="mt-dialog-title">排除文件清单 · {{ meta.full }}</span>
          <button class="mt-close" title="关闭" @click="close">×</button>
        </div>
        <div class="mt-dialog-body">
          <div class="mt-section">
            <div class="mt-section-head">
              <span class="mt-section-title">候选文件</span>
              <button class="mt-btn mt-btn-sm" @click="checkNoMd5">一键勾选无 MD5 文件</button>
            </div>

            <!-- 缓存状态条：命中秒开（任务执行时已预热），右侧「刷新」强制重拉 -->
            <div class="mt-listcache" :class="{ busy: busy }">
              <span class="mt-listcache-txt" :class="fresh === false ? 'hit' : fresh === true ? 'cold' : ''">
                <template v-if="loading"><span class="mt-spin"></span>正在获取文件清单…</template>
                <template v-else>{{ statusText }}</template>
              </span>
              <button class="mt-btn mt-btn-sm" title="忽略缓存，重新拉取文件清单（点完即用最新数据）" @click="onRefresh">刷新</button>
            </div>

            <div class="mt-check-list">
              <label v-for="(f, i) in files" :key="f.name" class="mt-check-item">
                <input type="checkbox" :checked="sel.has(i)" @change="toggle(i, $event)" />
                <span class="mt-check-name">{{ f.name }}</span>
                <span v-if="!f.md5" class="mt-tag">无 MD5</span>
              </label>
              <div v-if="!files.length && !loading" class="mt-list-foot"><span>清单为空</span></div>
            </div>
            <div class="mt-list-foot">
              <span>已选 {{ sel.size }} 个</span>
              <span class="mt-section-tip">勾选的文件在转存时会被排除</span>
            </div>
          </div>
          <div class="mt-hint">标注「无 MD5」的文件无校验值，转存时按文件名去重。</div>
        </div>
        <div class="mt-dialog-foot">
          <button class="mt-btn" @click="close">取消</button>
          <button class="mt-btn mt-btn-primary" @click="onOk">确定</button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<style scoped src="./mt-modal.css"></style>
