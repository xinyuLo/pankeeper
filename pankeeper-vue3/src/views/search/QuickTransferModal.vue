<script setup lang="ts">
/* 快速转存弹窗（qs- 前缀，原型 search-ui.js 的 qsMask 移植）。
 * 前提：该网盘在「转存配置」页配过目录（账号级），没配过则整页按钮禁用/不开弹窗。
 * 顶部并排：下拉选保存位置 + 可空的「文件夹更名」；下方预览块集中展示
 * 「转存后路径」和按该位置配置会触发的 QMS / STRM。确认 = pkQueue.enqueue 入队即走。 */
import { computed, nextTick, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { pkQueue } from '@/queue/engine'
import { DRIVE_META, DD_MEDIA } from '@/api/mock/meta'
import { listDdItems, listQmsPaths } from '@/api/modules/dd'
import { recognizeShare, type RecognizeCandidate } from '@/api/modules/recognize'
import { getSettings } from '@/api/modules/settings'
import { listAccounts } from '@/api/modules/accounts'
import { ddStore } from '@/api/mock/dd'
import RecognizePicker from '@/components/RecognizePicker.vue'
import type { DdItem, DdQmsPath, DriveType, MainDriveType } from '@/types/model'

const props = defineProps<{
  open: boolean
  /** 目标网盘类型（只有配过转存配置的网盘才进得来） */
  type: DriveType | null
  /** 默认任务名/新建文件夹名（搜索页给：搜索词+年份，见 SearchTransfer.defaultName） */
  shareName: string
 shareUrl?: string
    shareCode?: string
  }>()

const emit = defineEmits<{ (e: 'update:open', v: boolean): void }>()

/** 保存位置候选：直接读 ddStore（reactive）—— 搜索页进入时已加载过，
 *  所以打开弹窗的瞬间就有数据，不必等网络，也就不会先闪一下「还没配置转存目录」的空态。 */
const items = computed<DdItem[]>(() => ddStore.items)
const qmsPaths = ref<DdQmsPath[]>([])
/** 账号 id → 显示名（别名优先，空则昵称）：保存位置下拉里标出「这条配置属于哪个账号」 */
const accNames = ref<Record<string, string>>({})
const selId = ref<number | null>(null)
const rename = ref('')
const renameRef = ref()
// LitePan 事件名（media.backend=litepan 时显示输入框；空=按转存配置目录/全局默认）。
// 先用输入框，LitePan 出接口后换下拉（2026-10-06 用户定稿）
const lpEvent = ref('')
const mediaBackend = ref<'qms' | 'litepan'>('qms')

/** 该网盘可用的保存位置（按 sort 升序，与转存配置页排序一致） */
const options = computed<DdItem[]>(() =>
  items.value
    .filter((x) => x.type === props.type)
    .sort((a, b) => (a.sort || 0) - (b.sort || 0)),
)

function accLabel(it: DdItem): string {
  return accNames.value[it.account] || ''
}

const selectOptions = computed(() =>
  options.value.map((it) => {
    const acc = accLabel(it)
    const name = acc ? `${acc} · ${it.name}` : it.name
    return {
      value: it.id,
      label: `${name}　—　${it.path}${it.is_default ? '（默认）' : ''}`,
    }
  }),
)

const currentItem = computed<DdItem | null>(
  () => options.value.find((x) => x.id === selId.value) || null,
)

const driveName = computed(() => (props.type ? DRIVE_META[props.type as MainDriveType]?.full || props.type : ''))

/** 钉住默认项：is_default 是账号级属性，同网盘两个账号可能各有一条默认，
 *  必须显式钉到排序最前的默认项（原型踩过：浏览器只认最后一个 selected）。 */
function pinDefault() {
  const def = options.value.find((x) => x.is_default) || options.value[0] || null
  selId.value = def ? def.id : null
}

/** 每次打开：清空更名 → 立即用 ddStore 的现成数据渲染 → 慢请求转后台补 → 聚焦输入框。
 *  ⚠️ 这里**不能 await**：QMS/STRM 打的是 NAS 上的 qmediasync，原先三个请求 Promise.all
 *     全等完才赋值，导致弹窗先渲染成「还没配置转存目录」的空态、两三秒后才切成表单。 */
watch(
  () => props.open,
  (v) => {
    if (!v) return
    rename.value = ''
    lpEvent.value = ''
    getSettings().then((d) => (mediaBackend.value = d.media?.backend || 'qms')).catch(() => {})
    pinDefault() // 用 store 现成数据立即钉默认项，弹窗首帧就是完整表单
    // 后台静默刷新保存位置（写回 ddStore，items 是它的 computed 会自动更新）
    listDdItems().catch(() => {})
    // 账号显示名：下拉里标所属账号（accNames 是普通对象，到货即渲染）
    listAccounts()
      .then((rows) => {
        const m: Record<string, string> = {}
        for (const r of rows) m[String(r.id)] = r.alias || r.nickname || `账号#${r.id}`
        accNames.value = m
      })
      .catch(() => {})
    // QMS / STRM 最慢，各自到货各自填，绝不挡主表单渲染
    listQmsPaths().then((qs) => (qmsPaths.value = qs)).catch(() => {})
    void nextTick().then(() => {
      setTimeout(() => renameRef.value?.focus?.(), 60)
    })
  },
)

/** store 异步刷新回来后，若选中项已不在候选中（首帧无数据 / 配置被删），重钉一次 */
watch(options, () => {
  if (!options.value.some((x) => x.id === selId.value)) pinDefault()
})

function close() {
  emit('update:open', false)
}

/** 一键识别：分享名 → TMDB → 回填「文件夹更名」（识别器后端无关，QMS/LitePan 模式都可用）。
 * 置信度高直接回填；歧义（同名剧/电影，实锤：狂飙 vs F1：狂飙飞车）弹候选卡片让用户挑。 */
const recognizing = ref(false)
const pickerOpen = ref(false)
const pickerCands = ref<RecognizeCandidate[]>([])
async function onRecognize() {
  const src = (props.shareName || '').trim()
  if (!src) {
    message.warning('没有可识别的资源名')
    return
  }
  if (recognizing.value) return
  recognizing.value = true
  try {
    const r = await recognizeShare(src, src, { share_type: props.type || '', share_url: props.shareUrl || '', share_code: props.shareCode || '' })
    if (r.ok && r.media_name) {
      if (r.confident) {
        rename.value = r.media_name
        message.success(r.doubt ? `已识别（存疑，请确认）：${r.media_name}` : `已识别：${r.media_name}`)
      } else {
        pickerCands.value = r.candidates || []
        pickerOpen.value = true
      }
    } else {
      message.warning(r.message || '未识别到 TMDB 条目')
    }
  } catch {
    message.error('识别失败，请稍后重试')
  } finally {
    recognizing.value = false
  }
}
function onPick(c: RecognizeCandidate) {
  rename.value = c.year ? `${c.title} (${c.year})` : c.title
}

/** 预览「转存后长什么样」+ 按所选位置的配置列出触发的 QMS / STRM */
const pv = computed(() => {
  const it = currentItem.value
  if (!it) return null
  const raw = rename.value.trim()
  const origin = props.shareName || '分享的目录名'
  // 触发行：跟着所选位置的配置走（qms_on / qms_id / strm_id），没配就明说
  const xrows: { k: string; v: string; on: boolean }[] = []
  if (mediaBackend.value === 'litepan') {
    // LitePan 模式：联动行显示目录的 LitePan 配置 + 弹窗覆盖的事件名
    xrows.push({
      k: '推送 LitePan',
      v: it.lp_on ? `事件 ${lpEvent.value.trim() || it.lp_event || '（全局默认）'}` : '不推送（该目录未开 LitePan 联动）',
      on: it.lp_on,
    })
    return { base: it.path, raw, origin, xrows }
  }
  if (it.qms_on) {
    const q = qmsPaths.value.find((x) => x.id === it.qms_id) || null
    xrows.push({
      k: '触发 QMS',
      v: q ? `#${q.id} · ${DD_MEDIA[q.media_type] || q.media_type} · ${q.source_path}` : '联动已开，但刮削目录已失效',
      on: !!q,
    })
  } else {
    xrows.push({ k: '触发 QMS', v: '不联动（该目录未开启）', on: false })
  }
  // STRM 跟随 QMS 自动联动（整理成功才生成），不再单独配置
  xrows.push({ k: '触发 STRM', v: it.qms_on ? 'QMS 整理成功后自动生成' : '不生成', on: false })
  return { base: it.path, raw, origin, xrows }
})

/** 确认 = 入队即走，绝不弹进度条 */
function onOk() {
  const it = currentItem.value
  if (!it) {
    message.warning('请先选择保存位置')
    return
  }
  const raw = rename.value.trim()
  const pos = pkQueue.enqueue({
    name: raw || props.shareName || '分享资源',
    type: it.type,
    path: it.path,
    size: '—',
    share_url: props.shareUrl,
    share_code: props.shareCode,
    /* 建壳转存：在保存位置下按资源名（或更名值）新建文件夹，分享内容剥壳转入——
       记录页显示的名字和盘里的文件夹名天然一致 */
    rename: raw,
    with_shell: true,
    // LitePan 事件名（litepan 后端时弹窗覆盖；qms 后端后端自动忽略此字段）
    lp_event: mediaBackend.value === 'litepan' ? lpEvent.value.trim() : '',
    // 转存配置条目属于哪个账号就用哪个转（account 是账号 id 字符串）；
    // 空 = 该类型默认账号（后端兜底取 id 最小）
    acc_id: it.account ? Number(it.account) : null,
  })
  if (pos < 0) {
    // 后端同链接去重：wait/run 里已有同一 shareUrl
    message.warning('该分享已在转存队列中，勿重复添加')
    return
  }
  message.success(`已加入转存队列 · 当前第 ${pos} 位，完成后去右下角队列抽屉看日志`)
  close()
}
</script>

<template>
  <a-modal
    :open="open"
    :width="560"
    centered
    :title="`快速转存 · ${driveName}`"
    :footer="null"
    destroy-on-close
    @update:open="(v: boolean) => emit('update:open', v)"
  >
    <div class="qs-body">
      <div v-if="!options.length" class="qs-noconfig">
        「{{ driveName }}」还没配置转存目录，请先到「转存配置」页添加一条路径。
      </div>

      <template v-else>
        <!-- 保存位置 -->
        <div class="dd-field">
          <label class="dd-label">保存位置<i>*</i></label>
          <a-select
            v-model:value="selId"
            style="width: 100%"
            :options="selectOptions"
            placeholder="选择保存位置"
          />
        </div>

        <!-- 新文件夹名：label 和输入框同一行，留空 = 用资源名；「识别」= TMDB 识别回填 -->
        <div class="dd-field dd-inline">
          <label class="dd-label" style="margin-bottom: 0">文件夹更名</label>
          <a-input
            ref="renameRef"
            v-model:value="rename"
            :maxlength="80"
            placeholder="留空则用资源名新建文件夹"
            @press-enter="onOk"
          >
            <template #suffix>
              <a-button size="small" type="text" :loading="recognizing" style="margin-right: -7px" @click="onRecognize">
                识别
              </a-button>
            </template>
          </a-input>
        </div>

        <!-- LitePan 事件（联动后端=litepan 时显示）：输入框直填，与 LitePan 规则的事件名一致 -->
        <div v-if="mediaBackend === 'litepan'" class="dd-field dd-inline">
          <label class="dd-label" style="margin-bottom: 0">LitePan 事件</label>
          <a-input v-model:value="lpEvent" :maxlength="80" placeholder="留空则用转存配置里配的事件名" />
        </div>

        <!-- 「转存后」预览：三段结构（标题/路径/触发行），防止被拍平回退 -->
        <div class="dd-field">
          <div v-if="pv" class="qs-preview" :class="{ 'is-renamed': pv.raw }">
            <div class="qs-pv-hd">转存后</div>
            <div class="qs-pv-body">
              <div class="qs-pv-path">
                <span class="qs-pv-base">{{ pv.base }}</span>
                <span class="qs-pv-slash">/</span>
                <span class="qs-pv-new">{{ pv.raw || pv.origin }}</span>
              </div>
              <div v-if="pv.raw" class="qs-pv-note">
                新建文件夹 <b>{{ pv.raw }}</b> 承接分享内容（剥壳转入）
              </div>
              <div v-else class="qs-pv-note">
                以「{{ pv.origin }}」新建文件夹承接分享内容 · 填「文件夹更名」可换成别的名字
              </div>
              <div class="qs-pv-xrows">
                <div v-for="x in pv.xrows" :key="x.k" class="qs-pv-xrow">
                  <span class="qs-pv-xk">{{ x.k }}</span>
                  <span class="qs-pv-xv" :class="{ on: x.on }">{{ x.v }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </template>
    </div>

    <div class="qs-foot">
      <a-button @click="close">取消</a-button>
      <a-button type="primary" :disabled="!currentItem" @click="onOk">开始转存</a-button>
    </div>

    <!-- 识别歧义候选（同名剧/电影时让用户挑，回填「文件夹更名」） -->
    <RecognizePicker v-model:open="pickerOpen" :candidates="pickerCands" :source-name="shareName" @pick="onPick" />
  </a-modal>
</template>

<style scoped>
/* 表单行布局（原型 dd-field/dd-label 结构，scoped 不跨页） */
.qs-body { padding-top: 18px; }
.dd-field { margin-bottom: 16px; }
.dd-field:last-child { margin-bottom: 0; }
.dd-label { display: block; font-size: 13px; color: var(--text2); margin-bottom: 6px; }
.dd-label i { color: var(--error); font-style: normal; margin-left: 2px; }
/* 行内字段：label 和输入框同一行（「文件夹更名」） */
.dd-inline { display: flex; align-items: center; gap: 10px; }
.dd-inline > :last-child { flex: 1; }

.qs-noconfig {
  padding: 14px 16px;
  border: 1px solid var(--note-border);
  border-radius: var(--r-sm);
  background: var(--note-bg);
  color: var(--note-fg);
  font-size: 13px;
  line-height: 1.7;
}

/* 底部按钮条：与原型 dd-mfoot 对齐（右对齐 + 顶部分隔线） */
.qs-foot {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 14px 0 2px;
  margin-top: 6px;
  border-top: 1px solid var(--split);
}

/* ---- 「转存后」结果预览（search-ui 原型样式移植） ---- */
.qs-preview { border: 1px solid var(--split); border-radius: 10px; background: var(--surface-2); overflow: hidden; }
.qs-pv-hd {
  display: flex; align-items: center; gap: 6px; padding: 8px 12px;
  font-size: 12px; color: var(--text3); border-bottom: 1px solid var(--split);
  background: var(--card); letter-spacing: 0.02em;
}
.qs-pv-hd::before { content: ''; width: 5px; height: 5px; border-radius: 50%; background: var(--primary); flex: none; }
.qs-pv-body { padding: 11px 12px 12px; }
/* 路径本体：等宽、字重略加，明显是「结果」而不是「备注」 */
.qs-pv-path { font-family: var(--font-mono); font-size: 13px; font-weight: 500; line-height: 1.75; word-break: break-all; color: var(--text); }
/* 前缀（保存位置）次级色，末段（本次新建的目录）主色高亮 */
.qs-pv-base { color: var(--text3); }
.qs-pv-slash { color: var(--text4); margin: 0 2px; }
.qs-pv-new {
  background: rgba(22, 119, 255, 0.1); color: var(--primary); border-radius: 6px;
  padding: 1px 7px; font-weight: 600;
  box-decoration-break: clone; -webkit-box-decoration-break: clone;
}
.qs-pv-note { margin-top: 9px; padding-top: 8px; border-top: 1px dashed var(--split); font-size: 12px; color: var(--text3); line-height: 1.65; }
.qs-pv-note b { color: var(--text2); font-weight: 500; }
/* 触发行：明说会联动什么，没配的明说「不联动/不生成」 */
.qs-pv-xrows { margin-top: 9px; padding-top: 8px; border-top: 1px dashed var(--split); display: flex; flex-direction: column; gap: 4px; }
.qs-pv-xrow { display: flex; align-items: baseline; gap: 10px; font-size: 12px; line-height: 1.65; }
.qs-pv-xk { flex: none; color: var(--text3); letter-spacing: 0.02em; }
.qs-pv-xv { color: var(--text2); word-break: break-all; }
.qs-pv-xv.on { color: var(--primary); }
/* 更名发生时：预览块换主色系，与「没改名」的常态区分 */
.qs-preview.is-renamed { border-color: rgba(22, 119, 255, 0.35); }
.qs-preview.is-renamed .qs-pv-hd { color: var(--primary); }

/* 暗色：高亮是硬编码浅色底，深色上几乎看不见，必须单独换（原型实测踩坑） */
html[data-theme='dark'] .qs-pv-new { background: rgba(64, 150, 255, 0.2); color: #91caff; }
html[data-theme='dark'] .qs-preview.is-renamed { border-color: rgba(64, 150, 255, 0.34); }

/* 移动端（<768px）：行内字段（文件夹更名）改上下结构 */
@media (max-width: 767px) {
  .qs-body { padding-top: 12px; }
  .dd-inline { display: block; }
  .dd-inline .dd-label { margin-bottom: 6px !important; }
}
</style>
