<script setup lang="ts">
/* 任务配置弹窗（原型 mtTaskMask，720px）—— 新增/编辑共用。
 * 表单顺序：名称 / 分享链接(解析) / 提取码 / 保存到 / 对比路径 / 包含子目录 /
 * QMS 联动(开关+目录下拉) / 正则过滤(可多条) / 转存文件夹下钻 / 定时表达式 / 完成后动作。
 * 注意：没有排除文件清单——入口在任务行的「排除」按钮上，别加回来（设计约定）。
 * QMS 联动开关与「完成后动作 · 触发 QMS 刮削」是同一状态（postQms），两处控件联动。
 * 目录弹窗从这里叠加打开（允许两层）。 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import DirModal from './DirModal.vue'
import { DD_MEDIA, DRIVE_META } from '@/api/mock/meta'
import { listQmsPaths, listStrmPaths } from '@/api/modules/dd'
import {
  cronHuman,
  extractShareCode,
  getDrillDirs,
  getPaExtras,
  savePaTask,
  type PaDrillDir,
  type PaExtras,
} from '@/api/modules/tasks'
import type { DdQmsPath, DdStrmPath, MainDriveType, PaTask } from '@/types/model'

const props = defineProps<{ open: boolean; type: MainDriveType; task: PaTask | null; suspended?: boolean }>()
const emit = defineEmits<{ (e: 'update:open', v: boolean): void; (e: 'saved', task: PaTask): void }>()

const meta = computed(() => DRIVE_META[props.type])
const editing = computed(() => !!(props.task && props.task.id))

/* ===== 表单状态 ===== */
const name = ref('')
const shareUrl = ref('')
const shareCode = ref('')
const saveDir = ref('')
const comparePath = ref('')
const includeSub = ref(true)
const postQms = ref(false) // QMS 联动开关 = 完成后动作「触发 QMS 刮削」，一处动两处亮
const postNotify = ref(false)
const qmsId = ref<number | null>(null)
const strmId = ref<number | null>(null)
const cron = ref('0 3 * * *')
/* 正则过滤：产品定为一组（匹配模式 + 替换名）。存储仍是数组形状（后端契约），
 * 空规则保存时忽略 → 空数组。 */
const regPat = ref('')
const regRep = ref('')
const drillOn = ref(false)
const drill = ref<string[]>([])

/* ===== 下拉数据（进弹窗拉一次即可） ===== */
const qmsPaths = ref<DdQmsPath[]>([])
const strmPaths = ref<DdStrmPath[]>([])
const drillDirs = ref<PaDrillDir[]>([])

function qmsLabel(p: DdQmsPath): string {
  return `#${p.id} · ${DD_MEDIA[p.media_type]} · ${p.source_path}`
}
function strmLabel(p: DdStrmPath): string {
  return `#${p.id} · ${p.remote_path}`
}

/* cron 实时人话（空 → 未设置） */
const cronTextValue = computed(() => cronHuman(cron.value, '未设置'))

/* 链接输入时自动识别提取码并回填（识别不到不动手写的值） */
watch(shareUrl, (v) => {
  const code = extractShareCode(v)
  if (code) shareCode.value = code
})

function onParse() {
  const code = extractShareCode(shareUrl.value)
  if (code) shareCode.value = code
  message.success(code ? `解析成功，共 36 个文件 · 提取码 ${code}` : '解析成功，共 36 个文件')
}

/* 开 QMS 联动时给个默认整理目录，避免下拉空着 */
watch(postQms, (v) => {
  if (v && qmsId.value == null) qmsId.value = qmsPaths.value[0]?.id ?? null
})

/* ===== 目录弹窗（叠加第二层） ===== */
const dirOpen = ref(false)
const dirTarget = ref<'save' | 'compare'>('save')
const dirInitial = ref('')
function browse(target: 'save' | 'compare') {
  dirTarget.value = target
  dirInitial.value = target === 'save' ? saveDir.value : comparePath.value
  dirOpen.value = true
}
function onPicked(path: string) {
  if (dirTarget.value === 'save') saveDir.value = path
  else comparePath.value = path
}

/* ===== 打开时回填 ===== */
watch(
  () => props.open,
  async (v) => {
    if (!v) return
    const t = props.task
    const ex: PaExtras = t ? getPaExtras(t.id) : { regex: [{ pat: '', rep: '' }], drill_on: false, drill: [], qms_id: null, strm_id: null }
    name.value = t?.name || ''
    shareUrl.value = t?.share_url || ''
    shareCode.value = t?.share_code || ''
    saveDir.value = t?.save_dir || ''
    comparePath.value = t?.compare_path || ''
    includeSub.value = t ? t.include_subdirs : true
    postQms.value = !!t?.post_qms
    postNotify.value = !!t?.post_notify
    cron.value = t ? t.cron || '' : '0 3 * * *' // 新任务给个常用默认，编辑带原值（空=仅手动）
    const firstRule = ex.regex[0]
    regPat.value = firstRule?.pat || ''
    regRep.value = firstRule?.rep || ''
    drillOn.value = ex.drill_on
    drill.value = [...ex.drill]
    qmsId.value = ex.qms_id
    strmId.value = ex.strm_id

    if (!qmsPaths.value.length) {
      qmsPaths.value = await listQmsPaths()
      strmPaths.value = await listStrmPaths()
    }
    // 带出联动开着但没存过目录的旧任务：给个默认整理目录，别让下拉空着
    if (postQms.value && qmsId.value == null) qmsId.value = qmsPaths.value[0]?.id ?? null
    if (!drillDirs.value.length) drillDirs.value = await getDrillDirs()
  },
)

/* ===== 下钻勾选 ===== */
function toggleDrill(nm: string, e: Event) {
  const s = new Set(drill.value)
  if ((e.target as HTMLInputElement).checked) s.add(nm)
  else s.delete(nm)
  drill.value = [...s]
}

/* ===== 保存 ===== */
async function onSave() {
  if (!name.value.trim() || !shareUrl.value.trim()) {
    message.error('请填写任务名称和分享链接（必填）')
    return
  }
  const t = props.task
  const task: PaTask = {
    id: t?.id ?? 0,
    type: props.type,
    name: name.value.trim(),
    enabled: t ? t.enabled : true,
    share_url: shareUrl.value.trim(),
    share_code: shareCode.value.trim(),
    save_dir: saveDir.value,
    compare_path: comparePath.value,
    include_subdirs: includeSub.value,
    cron: cron.value.trim(),
    exclude_count: t ? t.exclude_count : 0,
    exclIdx: t ? [...t.exclIdx] : [],
    last_run: t ? t.last_run : '',
    last_status: t ? t.last_status : 'never',
    last_result: t ? t.last_result : '',
    post_qms: postQms.value,
    post_notify: postNotify.value,
  }
  // 正则过滤：一组规则，pat/rep 都空就忽略；STRM 只在联动开着时才有意义
  const extras: PaExtras = {
    regex: regPat.value.trim() || regRep.value.trim() ? [{ pat: regPat.value.trim(), rep: regRep.value.trim() }] : [],
    drill_on: drillOn.value,
    drill: [...drill.value],
    qms_id: postQms.value ? qmsId.value : null,
    strm_id: postQms.value ? strmId.value : null,
  }
  const saved = await savePaTask(task, extras)
  message.success(`保存成功：${saved.name}`)
  emit('saved', saved)
  close()
}

function close() {
  if (dirOpen.value) dirOpen.value = false // Esc/取消连目录弹窗一起收，避免残留
  emit('update:open', false)
}

/* Esc 逐层关：目录弹窗在上先关目录；执行监控/排除弹窗盖着时不动（suspended 由父页面控制） */
function onKey(e: KeyboardEvent) {
  if (e.key !== 'Escape' || props.suspended) return
  if (dirOpen.value) {
    dirOpen.value = false
    return
  }
  close()
}
watch(
  () => props.open,
  (v) => {
    if (v) window.addEventListener('keydown', onKey)
    else window.removeEventListener('keydown', onKey)
  },
)
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <teleport to="body">
    <div v-if="open" class="mt-mask" style="z-index: 1000" @click.self="close">
      <div class="mt-dialog">
        <div class="mt-dialog-head">
          <span class="mt-color-dot" :style="{ background: meta.color }"></span>
          <span class="mt-dialog-title">{{ editing ? '编辑' : '新增' }}自动转存任务</span>
          <span class="mt-dialog-sub">{{ meta.full }}</span>
          <button class="mt-close" title="关闭" @click="close">×</button>
        </div>
        <div class="mt-dialog-body">
          <!-- 任务名称 -->
          <div class="mt-form-row">
            <label class="mt-label">任务名称</label>
            <div class="mt-control">
              <input v-model="name" class="mt-input" placeholder="如：兰香如故 自动转存" />
            </div>
          </div>

          <!-- 分享链接：?pwd= 自动识别提取码回填 -->
          <div class="mt-form-row">
            <label class="mt-label">分享链接</label>
            <div class="mt-control">
              <div class="mt-row-inline">
                <input
                  v-model="shareUrl"
                  class="mt-input"
                  placeholder="粘贴完整分享链接，含提取码，如 https://pan.baidu.com/s/1abc?pwd=xyz4"
                />
                <button class="mt-btn mt-btn-inline" @click="onParse">解析</button>
              </div>
              <div class="mt-hint">链接末尾带 <code>?pwd=</code> 会自动识别提取码</div>
            </div>
          </div>

          <!-- 提取码：解析自动回填，也可手填 -->
          <div class="mt-form-row">
            <label class="mt-label">提取码</label>
            <div class="mt-control">
              <input v-model="shareCode" class="mt-input" style="max-width: 220px" placeholder="4 位提取码（粘贴链接点「解析」自动识别）" />
            </div>
          </div>

          <!-- 保存到 -->
          <div class="mt-form-row">
            <label class="mt-label">保存到</label>
            <div class="mt-control">
              <div class="mt-row-inline">
                <input :value="saveDir" class="mt-input" placeholder="选择网盘目录" readonly title="点击「浏览」选择目录" />
                <button class="mt-btn mt-btn-inline" @click="browse('save')">浏览</button>
              </div>
            </div>
          </div>

          <!-- 对比路径 -->
          <div class="mt-form-row">
            <label class="mt-label">对比路径</label>
            <div class="mt-control">
              <div class="mt-row-inline">
                <input :value="comparePath" class="mt-input" placeholder="用于去重对比的目录" readonly title="点击「浏览」选择目录" />
                <button class="mt-btn mt-btn-inline" @click="browse('compare')">浏览</button>
              </div>
              <div class="mt-hint">转存前会与该路径下的文件做去重对比（先 MD5 后文件名）。</div>
            </div>
          </div>

          <!-- 包含子目录 -->
          <div class="mt-form-row">
            <label class="mt-label">包含子目录</label>
            <div class="mt-control">
              <label class="mt-opt">
                <input v-model="includeSub" type="checkbox" /><span class="mt-box"></span>分享内有子目录时一并转存
              </label>
            </div>
          </div>

          <!-- QMS 联动：开关默认关，开后展开目录下拉（链路固定：转存→15s→QMS→15s→STRM） -->
          <div class="mt-form-row" style="align-items: flex-start">
            <label class="mt-label">QMS 联动</label>
            <div class="mt-control">
              <div class="mt-opts">
                <label class="mt-switch-row">
                  <label class="mt-switch"><input v-model="postQms" type="checkbox" /><span class="mt-switch-slider"></span></label>
                  <span class="mt-switch-label">转存完成后联动 QMS 整理</span>
                </label>
              </div>
              <div v-if="postQms" style="margin-top: 10px">
                <select v-model="qmsId" class="mt-select" style="margin-bottom: 8px">
                  <option v-for="p in qmsPaths" :key="p.id" :value="p.id">{{ qmsLabel(p) }}</option>
                </select>
                <select v-model="strmId" class="mt-select">
                  <option :value="null">不生成 STRM</option>
                  <option v-for="p in strmPaths" :key="p.id" :value="p.id">{{ strmLabel(p) }}</option>
                </select>
                <div class="mt-hint">自动转存完成 → 15 秒后触发 QMS 整理 → 整理完成 → 15 秒后触发 STRM 生成；不需要 STRM 就选「不生成」。</div>
              </div>
            </div>
          </div>

          <!-- 正则过滤：固定一组（模式 → 替换名），都留空 = 不过滤 -->
          <div class="mt-form-row" style="align-items: flex-start">
            <label class="mt-label">正则过滤</label>
            <div class="mt-control">
              <input v-model="regPat" class="mt-input" placeholder="匹配模式（正则），如 \.mkv$" style="margin-bottom: 8px" />
              <input v-model="regRep" class="mt-input" placeholder="替换 / 重命名，如 [1080P]；留空 = 只匹配过滤" />
              <div class="mt-hint">两项都留空 = 不启用过滤；填了模式，转存时按规则处理文件名。</div>
            </div>
          </div>

          <!-- 转存文件夹下钻：开关总控 + 勾选列表（可多选） -->
          <div class="mt-form-row" style="align-items: flex-start">
            <label class="mt-label">转存文件夹下钻</label>
            <div class="mt-control">
              <div class="mt-opts">
                <label class="mt-opt">
                  <input v-model="drillOn" type="checkbox" /><span class="mt-box"></span>仅转存勾选的子目录
                </label>
              </div>
              <div v-if="drillOn" style="margin-top: 10px">
                <div class="mt-tree-toolbar">
                  <span class="small muted">已选 {{ drill.length }} 项</span>
                  <span style="flex: 1"></span>
                  <button class="mt-btn mt-btn-sm" @click="drill = drillDirs.map((d) => d.name)">全选</button>
                  <button class="mt-btn mt-btn-sm" @click="drill = []">清空</button>
                </div>
                <div class="mt-check-list">
                  <label v-for="d in drillDirs" :key="d.name" class="mt-check-item">
                    <input type="checkbox" :checked="drill.includes(d.name)" @change="toggleDrill(d.name, $event)" />
                    <span class="mt-check-name">{{ d.name }}</span>
                    <span v-if="d.size" class="mt-dt-size">{{ d.size }}</span>
                  </label>
                </div>
                <div class="mt-hint">勾选分享内需要转存的子目录（可多选）。</div>
              </div>
            </div>
          </div>

          <!-- 定时表达式：cron 输入 + 实时人话 -->
          <div class="mt-form-row">
            <label class="mt-label">定时表达式</label>
            <div class="mt-control">
              <div class="mt-row-inline">
                <input v-model="cron" class="mt-input" placeholder="0 3 * * *" />
              </div>
              <div class="mt-hint">此处为 cron 表达式规则（分 时 日 月 周），如 0 3 * * * = 每天凌晨 3 点。</div>
              <div class="mt-hint mt-hint-strong">{{ cronTextValue }}</div>
            </div>
          </div>

          <!-- 完成后动作：两个开关（触发 QMS 与上面联动开关同状态） -->
          <div class="mt-form-row">
            <label class="mt-label">完成后动作</label>
            <div class="mt-control">
              <div class="mt-opts">
                <label class="mt-opt">
                  <input v-model="postQms" type="checkbox" /><span class="mt-box"></span>触发 QMS 刮削
                </label>
                <label class="mt-opt">
                  <input v-model="postNotify" type="checkbox" /><span class="mt-box"></span>Server 酱推送
                </label>
              </div>
            </div>
          </div>
        </div>
        <div class="mt-dialog-foot">
          <button class="mt-btn" @click="close">取消</button>
          <button class="mt-btn mt-btn-primary" @click="onSave">保存</button>
        </div>
      </div>
    </div>

    <!-- 目录树选择器：叠加第二层（z-index 1002 > 任务弹窗 1000） -->
    <DirModal v-model:open="dirOpen" :type="type" :initial="dirInitial" @picked="onPicked" />
  </teleport>
</template>

<style scoped src="./mt-modal.css"></style>
