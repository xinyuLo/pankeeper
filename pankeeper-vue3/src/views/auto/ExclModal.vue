<script setup lang="ts">
/* 排除文件清单弹窗（原型 mtExclMask，560px）—— 入口在任务行的「排除」按钮，
 * 不在编辑弹窗里（设计约定：排除是行级操作，开发时别挪回去）。
 * 数据走后端分享清单缓存（share_cache，与目录缓存不同逻辑）：转存每跑完一次刷新一次，
 * 两次转存之间命中秒开；「刷新」按钮忽略缓存直连重拉。无 TTL 定时器（原型的 30 分钟
 * 自动刷新随 mock 一起撤了——刷新时机由转存驱动）。
 * 取消不脏写：勾选是草稿，只有「确定」才回写任务 exclude_json/exclude_count。 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { CheckSquareOutlined, ReloadOutlined } from '@ant-design/icons-vue'
import { DRIVE_META } from '@/api/mock/meta'
import { commitExcl, fetchExclFiles, fmtHms, type PaExclFetch, type PaExclFile } from '@/api/modules/tasks'
import type { MainDriveType, PaTask } from '@/types/model'

import { useBackGuard } from '@/composables/useBackGuard'
const props = defineProps<{ open: boolean; task: PaTask | null }>()
const emit = defineEmits<{ (e: 'update:open', v: boolean): void; (e: 'committed'): void }>()
useBackGuard(() => props.open, () => emit('update:open', false))

const meta = computed(() => (props.task ? DRIVE_META[props.task.type as MainDriveType] : DRIVE_META.baidu))

/* 正则外文件也显示（勾了不会被转存，但必须可见才能取消勾选）；正则非法/为空视为全部候选 */
const rex = computed(() => {
  const p = (props.task?.regex_pattern || '').trim()
  if (!p) return null
  try {
    return new RegExp(p)
  } catch {
    return null
  }
})
function isRegexOut(f: PaExclFile) {
  return !!rex.value && !rex.value.test(f.name)
}

/* 已保存排除但当前清单里没有的文件数（分享变更等）——确定回写会丢，提前告知 */
const missingCount = computed(() => {
  const saved = props.task?.exclude_names || []
  return saved.filter((n) => !files.value.some((f) => f.name === n)).length
})

const files = ref<PaExclFile[]>([])
const sel = ref(new Set<number>())
const loading = ref(false)
const busy = ref(false)
const fresh = ref<boolean | null>(null) // null=还没拿到
const cacheTs = ref(0)

/* 状态条文案：冷启动橙（刚拉取）/ 命中绿；刷新时机由转存驱动（每次任务跑完自动刷） */
const statusText = computed(() => {
  if (loading.value) return ''
  if (fresh.value === null) return '○ 首次加载'
  if (fresh.value) return `○ 刚从网盘重新拉取 · 获取于 ${fmtHms(cacheTs.value)} · 下次转存执行后自动刷新`
  return `● 命中缓存 · 获取于 ${fmtHms(cacheTs.value)} · 转存执行后自动刷新`
})

/** 展示口径：正则命中的 + keep 里点名的（已排除/草稿已勾选）；正则外又没勾的是纯噪音不显示。
 * 已排除的文件即使正则匹配不上也保留——否则取消勾选都无从下手 */
function applyVisible(res: PaExclFetch, keep: string[]) {
  const r = rex.value
  files.value = r ? res.files.filter((f) => r.test(f.name) || keep.includes(f.name)) : res.files
  fresh.value = res.fresh
  cacheTs.value = res.ts
}

watch(
  () => props.open,
  async (v) => {
    if (!v || !props.task) return
    // 草稿 = 任务里已保存的排除清单（按文件名对齐候选）
    const saved: string[] = props.task?.exclude_names || []
    files.value = []
    fresh.value = null
    loading.value = true
    try {
      applyVisible(await fetchExclFiles(props.task.id), saved)
      sel.value = new Set(files.value.map((f, i) => (saved.includes(f.name) ? i : -1)).filter((i) => i >= 0))
    } finally {
      loading.value = false
    }
  },
)

/** 刷新：忽略缓存重拉，保留已勾选项（按文件名对齐，勾选原样带回） */
async function onRefresh() {
  if (busy.value || !props.task) return // 防连点造成并发请求
  busy.value = true
  const keep = new Set(files.value.filter((_, i) => sel.value.has(i)).map((f) => f.name))
  try {
    const res = await fetchExclFiles(props.task.id, true)
    // 展示口径的 keep = 已排除清单 ∪ 草稿已勾选，否则草稿里新勾的正则外文件刷新后就消失了
    applyVisible(res, [...new Set([...(props.task.exclude_names || []), ...keep])])
    sel.value = new Set(files.value.map((f, i) => (keep.has(f.name) ? i : -1)).filter((i) => i >= 0))
    message.success('文件清单已刷新')
  } finally {
    busy.value = false
  }
}

/** 一键勾选无 MD5：无校验值的文件转存时只能按文件名去重，最值得排除；三种结果都给提示 */
function checkNoMd5() {
  const s = new Set<number>()
  files.value.forEach((f, i) => {
    if (!f.md5) s.add(i)
  })
  sel.value = s
  if (!files.value.length) message.info('清单为空，没有可勾选的文件')
  else if (!s.size) message.info('所有文件都有 MD5 校验值，不用排除')
  else message.success(`已勾选 ${s.size} 个无 MD5 文件`)
}

function toggle(i: number, e: Event) {
  const s = new Set(sel.value)
  if ((e.target as HTMLInputElement).checked) s.add(i)
  else s.delete(i)
  sel.value = s
}

async function onOk() {
  if (!props.task) return
  const idxs = [...sel.value].sort((a, b) => a - b)
  const names = idxs.map((i) => files.value[i]?.name || '').filter(Boolean)
  // 有校验值的文件同时记 MD5：分享里改名的文件靠 MD5 兜住（名字对不上也能排掉）
  const md5s = idxs.map((i) => files.value[i]?.md5 || '').filter(Boolean)
  await commitExcl(props.task.id, names, md5s)
  message.success(`已排除 ${names.length} 个文件`)
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
              <!-- 两个动作钮并排放标题行：勾选是高频操作、刷新是缓存补救，都够不着就都得低头找 -->
              <div class="mt-head-btns" :class="{ busy: busy }">
                <button class="mt-btn mt-btn-sm mt-btn-soft" title="勾选全部无校验值的文件（转存时只能按文件名去重，最值得排除）" @click="checkNoMd5">
                  <CheckSquareOutlined />一键勾选无 MD5
                </button>
                <button class="mt-btn mt-btn-sm mt-btn-soft" title="忽略缓存，重新拉取文件清单（点完即用最新数据）" @click="onRefresh">
                  <ReloadOutlined />刷新
                </button>
              </div>
            </div>

            <!-- 缓存状态条：命中秒开（任务执行时已预热）；刷新钮已上移标题行，这里只剩状态 -->
            <div class="mt-listcache">
              <span class="mt-listcache-txt" :class="fresh === false ? 'hit' : fresh === true ? 'cold' : ''">
                <template v-if="loading"><span class="mt-spin"></span>正在获取文件清单…</template>
                <template v-else>{{ statusText }}</template>
              </span>
            </div>

            <div class="mt-check-list">
              <label v-for="(f, i) in files" :key="f.name" class="mt-check-item" :class="{ 'mt-checked': sel.has(i) }">
                <input type="checkbox" :checked="sel.has(i)" @change="toggle(i, $event)" />
                <span class="mt-check-name">{{ f.name }}</span>
                <span v-if="isRegexOut(f)" class="mt-tag mt-tag-dim">正则外</span>
                <span v-if="!f.md5" class="mt-tag">无 MD5</span>
              </label>
              <div v-if="!files.length && !loading" class="mt-list-foot"><span>清单为空</span></div>
            </div>
            <div class="mt-list-foot">
              <span>已选 {{ sel.size }} 个</span>
              <span v-if="missingCount" class="mt-list-warn">另 {{ missingCount }} 个已排除文件不在当前清单，点「确定」后将移出排除清单</span>
              <span v-else class="mt-section-tip">勾选的文件在转存时会被排除</span>
            </div>
          </div>
          <div class="mt-hint">候选为正则命中的文件 + 已勾选的排除项：正则匹配不上的文件不会转存，只保留已勾选的方便取消；「无 MD5」的文件无校验值，转存时按文件名去重。</div>
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
