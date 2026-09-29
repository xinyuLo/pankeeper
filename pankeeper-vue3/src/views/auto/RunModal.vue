<script setup lang="ts">
/* 执行监控弹窗（原型 mtRunMask，640px）。
 * 打开即关掉其余弹窗（互斥由父页面控制）；进度条 0→100 渐进 + 统计四卡随步跳变
 * + LogBox 分级日志滚动。跑完回写任务执行快照（last_run/last_status/last_result）
 * 并 toast。中途关掉 = 停表，重开重跑。 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import LogBox from '@/components/LogBox.vue'
import { DRIVE_META } from '@/api/mock/meta'
import { buildPaRunSeq, finishPaRun, type PaRunStep } from '@/api/modules/tasks'
import type { MainDriveType, PaTask, QueueLogLine } from '@/types/model'

const props = defineProps<{ open: boolean; task: PaTask | null }>()
const emit = defineEmits<{ (e: 'update:open', v: boolean): void; (e: 'finished'): void }>()

const meta = computed(() => (props.task ? DRIVE_META[props.task.type as MainDriveType] : DRIVE_META.baidu))

/** 每步 480ms，和原型节奏一致 */
const STEP_MS = 480

const pct = ref(0)
const stats = ref({ add: 0, skip: 0, fail: 0, excl: 0 })
const lines = ref<QueueLogLine[]>([])
const hint = ref('正在执行，请稍候…')
const done = ref(false)
let timer: number | null = null
let idx = 0
let seq: PaRunStep[] = []

function stop() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

async function finish() {
  const t = props.task
  if (!t || done.value) return
  done.value = true
  hint.value = '已完成，可在转存记录查看详情'
  await finishPaRun(t.id, stats.value.add, stats.value.skip, stats.value.fail)
  message.success(`任务「${t.name}」执行完成 · ${t.last_result}`)
  emit('finished') // 父页面刷新任务表（last_status/last_result 已变色）
}

function step() {
  if (idx >= seq.length) {
    stop()
    void finish()
    return
  }
  const s = seq[idx++]
  pct.value = s.p
  const a = s.add
  if (a) {
    if (a.add != null) stats.value.add = a.add
    if (a.skip != null) stats.value.skip = a.skip
    if (a.fail != null) stats.value.fail = a.fail
    if (a.excl != null) stats.value.excl = a.excl
  }
  // 行首时间戳 = 模拟已耗时（mm:ss）
  const sec = Math.round((idx * STEP_MS) / 1000)
  const ts = `${String(Math.floor(sec / 60)).padStart(2, '0')}:${String(sec % 60).padStart(2, '0')}`
  lines.value = [...lines.value, { lv: s.lv, txt: `${ts} ${s.txt}` }]
}

function start() {
  if (!props.task) return
  seq = buildPaRunSeq(props.task)
  idx = 0
  pct.value = 0
  stats.value = { add: 0, skip: 0, fail: 0, excl: 0 }
  lines.value = []
  hint.value = '正在执行，请稍候…'
  done.value = false
  stop()
  timer = window.setInterval(step, STEP_MS)
}

watch(
  () => props.open,
  (v) => {
    if (v) start()
    else stop()
  },
)

function close() {
  stop()
  emit('update:open', false)
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
  stop()
})
</script>

<template>
  <teleport to="body">
    <div v-if="open" class="mt-mask" style="z-index: 1004" @click.self="close">
      <div class="mt-dialog" style="width: 640px">
        <div class="mt-dialog-head">
          <span class="mt-color-dot" :style="{ background: meta.color }"></span>
          <span class="mt-dialog-title">执行监控 · {{ task?.name || '任务' }}</span>
          <span class="mt-dialog-sub">{{ meta.full }}</span>
          <button class="mt-close" title="关闭" @click="close">×</button>
        </div>
        <div class="mt-dialog-body">
          <div class="mt-progress">
            <div class="mt-progress-bar" :style="{ width: pct + '%' }"></div>
          </div>
          <div class="mt-progress-text" style="text-align: right">{{ pct }}%</div>

          <!-- 统计四卡：数字随执行进度涨 -->
          <div class="mt-stats">
            <div class="mt-stat-card mt-stat-add">
              <div class="mt-stat-num">{{ stats.add }}</div>
              <div class="mt-stat-label">新增</div>
            </div>
            <div class="mt-stat-card mt-stat-skip">
              <div class="mt-stat-num">{{ stats.skip }}</div>
              <div class="mt-stat-label">跳过</div>
            </div>
            <div class="mt-stat-card mt-stat-fail">
              <div class="mt-stat-num">{{ stats.fail }}</div>
              <div class="mt-stat-label">失败</div>
            </div>
            <div class="mt-stat-card mt-stat-excl">
              <div class="mt-stat-num">{{ stats.excl }}</div>
              <div class="mt-stat-label">已排除</div>
            </div>
          </div>

          <LogBox :lines="lines" follow-bottom class="mt-runlog" />
        </div>
        <div class="mt-dialog-foot">
          <span class="mt-run-foot" style="margin: 0">{{ hint }}</span>
          <button class="mt-btn mt-btn-primary" style="margin-left: auto" @click="close">关闭</button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<style scoped src="./mt-modal.css"></style>
