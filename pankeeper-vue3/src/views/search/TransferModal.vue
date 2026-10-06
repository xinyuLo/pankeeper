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
 * 「保存到我的网盘」目录树选目标位置；选项：包含子目录 / 文件夹更名 /
 * QMS·STRM 显式下拉（默认按目标目录前缀自动带出，可改「不触发」）。
 * 「开始转存」= pkQueue.enqueue 入队即走，绝无内联进度条。 */
import { computed, provide, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { FolderOutlined } from '@ant-design/icons-vue'
import PkTree from '@/components/PkTree.vue'
import LazyDirTree from '@/components/LazyDirTree.vue'
import { USE_MOCK } from '@/api/http'
import { getRootDirs } from '@/api/modules/accounts'
import { listDdItems, listQmsPaths } from '@/api/modules/dd'
import { recognizeShare, type RecognizeCandidate } from '@/api/modules/recognize'
import { getSettings } from '@/api/modules/settings'
import { ddStore } from '@/api/mock/dd'
import ShareTree from './ShareTree.vue'
import RecognizePicker from '@/components/RecognizePicker.vue'
import { pkQueue } from '@/queue/engine'
import { DRIVE_META } from '@/api/mock/meta'
import { SHARE_TREE, MINE_TREE } from '@/api/mock/tree'
import type { DdItem, DdQmsPath, MainDriveType, TreeNode } from '@/types/model'

const props = defineProps<{ open: boolean; target: TransferTarget | null }>()
const emit = defineEmits<{ (e: 'update:open', v: boolean): void }>()

const DEFAULT_DIR = USE_MOCK ? '/我的资源/影视/电视剧/国产剧' : '/'

/** 真实模式的目录树落点：网盘连接页配置了「默认根目录」就以它为根，只显示其子目录 */
const rootDir = ref('')
const rootDirs = ref<Record<string, string>>({})
/** 只有三大盘支持真实目录浏览；其余盘允许直接转存（目标目录自动创建） */
const isMainDrive = computed(() => !!props.target && (['baidu', 'quark', '115'] as string[]).includes(props.target.type))

/* 真实目录树刷新：绕过后端缓存直连重拉根层 */
const mineTree = ref<InstanceType<typeof LazyDirTree> | null>(null)
const treeRefreshing = ref(false)
async function onRefreshTree() {
  treeRefreshing.value = true
  try {
    await mineTree.value?.reload()
  } finally {
    treeRefreshing.value = false
  }
}

const meta = computed(() => (props.target ? DRIVE_META[props.target.type] : null))
/** 分享摘要行：资源名 + 「N 项 · X GB」跟着资源走（项数 mock 固定 12） */
const sumMeta = computed(() => `12 项 · ${props.target?.size || '82.4 GB'}`)

/** 一键识别：资源名 → TMDB → 回填「文件夹更名」（识别器后端无关，QMS/LitePan 模式都可用）。
 * 置信度高直接回填；歧义（同名剧/电影，实锤：狂飙 vs F1：狂飙飞车）弹候选卡片让用户挑。 */
const recognizing = ref(false)
const pickerOpen = ref(false)
const pickerCands = ref<RecognizeCandidate[]>([])
async function onRecognize() {
  const src = (props.target?.name || '').trim()
  if (!src) {
    message.warning('没有可识别的资源名')
    return
  }
  if (recognizing.value) return
  recognizing.value = true
  try {
    const r = await recognizeShare(src, src, { share_type: props.target?.type || '', share_url: props.target?.url || '', share_code: props.target?.share_code || '' })
    if (r.ok && r.media_name) {
      if (r.confident) {
        renameInput.value = r.media_name
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
function onPickCandidate(c: RecognizeCandidate) {
  renameInput.value = c.year ? `${c.title} (${c.year})` : c.title
}

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
/* 文件夹更名：非空 = 在目标目录下按此名新建文件夹、分享内容剥壳转入 */
const renameInput = ref('')

/* ---- QMS 联动（对齐任务弹窗样式：开关 + 全宽下拉）。
 *  STRM 不再单独选（2026-10-04 用户定稿）：配了 QMS，刮削成功自动联动 STRM 生成、
 *  失败不生成——STRM 目标 = 与该 QMS 配对的转存配置目录的 strm_id。
 *  下拉默认按目标位置前缀自动带出；用户手动改过就不再自动覆盖。
 *  开关关 = 明确不联动（media_off，连目录匹配都不做）。 ---- */
const mediaOn = ref(true)
const qmsSel = ref<number | null>(null)
// LitePan 模式：联动行换成「推 LitePan」开关 + 事件名输入框（空=按转存配置目录/全局默认）
const lpOn = ref(true)
const lpEvent = ref('')
const mediaBackend = ref<'qms' | 'litepan'>('qms')
const qmsPaths = ref<DdQmsPath[]>([])
const pathsLoading = ref(false)
const mediaTouched = ref(false)

const qmsOpts = computed(() => qmsPaths.value.map((p) => ({ value: p.id, label: `#${p.id} · ${p.source_path}` })))

/** 目标目录命中的转存配置（最长前缀优先，对齐后端 _match_dd_link 语义） */
function hitDd(pred: (d: DdItem) => boolean): DdItem | null {
  const t = props.target
  if (!t) return null
  const dir = selectedDir.value
  return (
    (ddStore.items as DdItem[])
      .filter((d) => d.type === t.type && pred(d) && (dir === d.path || dir.startsWith((d.path || '').replace(/\/+$/, '') + '/')))
      .sort((a, b) => b.path.length - a.path.length)[0] || null
  )
}
/** 联动可用性（总闸，2026-10-06 用户定稿：什么都没联动就什么都不让选）：
 * 保存位置没命中任何开了联动的目录 → 开关禁用+自动关，QMS/LitePan 同规则。 */
const qmsHit = computed(() => hitDd((d) => !!d.qms_on && !!d.qms_id))
const lpHit = computed(() => hitDd((d) => !!d.lp_on))
const qmsDisabled = computed(() => mediaBackend.value === 'qms' && !qmsHit.value)
const lpDisabled = computed(() => mediaBackend.value === 'litepan' && !lpHit.value)
watch(qmsDisabled, (v) => {
  if (v) mediaOn.value = false
})
watch(lpDisabled, (v) => {
  if (v) lpOn.value = false
})

/** 按目标目录前缀自动带出联动目标（对齐后端 _match_dd_link 语义） */
function autoMatchMedia() {
  qmsSel.value = qmsHit.value?.qms_id ?? null
}
watch(selectedDir, () => {
  if (!mediaTouched.value) autoMatchMedia()
})

/** 每次打开重置：分享树收起、勾选清空、目标位置回默认国产剧 */
  watch(
  () => props.open,
  async (v) => {
    if (!v) return
    checked.value = new Set()
    shareOpen.value = false
    renameInput.value = ''
    // 打开即选中锁定根（默认根目录）；没配置就回退原来的默认
    if (!USE_MOCK) rootDirs.value = await getRootDirs().catch(() => ({}))
    rootDir.value = rootDirs.value[props.target?.type || ''] || ''
    selectedDir.value = rootDir.value || DEFAULT_DIR
    includeSub.value = true
    mediaOn.value = true
    lpOn.value = true
    lpEvent.value = ''
    getSettings().then((d) => (mediaBackend.value = d.media?.backend || 'qms')).catch(() => {})
    mediaTouched.value = false
    // QMS/STRM 目录清单后台拉（QMS 在 NAS 上，秒级）；到货后按目标目录带默认值
    if (!USE_MOCK) {
      listDdItems().catch(() => {})
      pathsLoading.value = true
      const qs = await listQmsPaths().catch(() => [])
      pathsLoading.value = false
      qmsPaths.value = qs
    }
    autoMatchMedia()
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
    /* 建壳转存：没勾选具体内容时按默认名/更名值新建文件夹、剥壳转入（勾选了就走原平铺逻辑） */
    rename: renameInput.value.trim(),
    with_shell: checkedFiles.value === 0,
    /* 联动：开关关 = 明确不触发；开 = 用下拉选的 QMS（默认按目标位置自动带出）。
       STRM 不传——后端与 QMS 自动配对，刮削成功才生成。
       LitePan 模式：lp_event 带弹窗填的事件名（后端按 media.backend 分流，qms 时忽略） */
    media_off: mediaBackend.value === 'litepan' ? !lpOn.value : !mediaOn.value,
    qms_id: mediaBackend.value === 'qms' && mediaOn.value ? qmsSel.value : null,
    lp_event: mediaBackend.value === 'litepan' && lpOn.value ? lpEvent.value.trim() : '',
  })
  if (pos < 0) {
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

      <!-- 文件夹更名：分享摘要下方整行（留空 = 用默认名在目标位置新建文件夹）；「识别」= TMDB 回填 -->
      <div class="tm-rename">
        <label>文件夹更名</label>
        <a-input v-model:value="renameInput" :maxlength="80" placeholder="留空则用资源名新建文件夹" allow-clear>
          <template #suffix>
            <a-button size="small" type="text" :loading="recognizing" style="margin-right: -7px" @click="onRecognize">
              识别
            </a-button>
          </template>
        </a-input>
      </div>

      <!-- 包含子目录：独立一行（自绘勾选框，样式对齐任务弹窗） -->
      <div class="filterbar" style="margin-top: 12px">
        <span class="muted">选项</span>
        <div class="tm-opts">
          <label class="tm-opt">
            <input v-model="includeSub" type="checkbox" /><span class="tm-box"></span>分享内有子目录时一并转存
          </label>
        </div>
      </div>

      <!-- 联动：按「系统设置 → 联动后端」切换 QMS / LitePan 表单。
           总闸：保存位置没命中任何开了联动的目录 → 开关禁用+自动关（什么都不让选） -->
      <div class="tm-media">
        <template v-if="mediaBackend === 'qms'">
          <label class="tm-media-switch">
            <a-switch v-model:checked="mediaOn" size="small" :disabled="qmsDisabled" />
            <span>转存完成后联动 QMS 整理</span>
          </label>
          <div v-if="qmsDisabled" class="tm-hint">
            该保存位置未配置 QMS 联动——先到「转存配置」给目录开启后才能在这里联动。
          </div>
          <template v-else-if="mediaOn">
            <a-select
              v-model:value="qmsSel"
              :options="qmsOpts"
              style="width: 100%; margin-top: 10px"
              :loading="pathsLoading"
              :placeholder="pathsLoading ? '正在加载 QMS 目录…' : qmsPaths.length ? '选择 QMS 刮削目录' : 'QMS 未连接或没有刮削目录'"
              allow-clear
              @change="mediaTouched = true"
            />
            <div class="tm-hint">
              默认按目标位置自动带出。QMS 整理成功后自动生成 STRM，失败不生成；不需要就清空。
            </div>
          </template>
        </template>
        <template v-else>
          <label class="tm-media-switch">
            <a-switch v-model:checked="lpOn" size="small" :disabled="lpDisabled" />
            <span>转存完成后推送 LitePan</span>
          </label>
          <div v-if="lpDisabled" class="tm-hint">
            该保存位置未配置 LitePan 联动——先到「转存配置」给目录开启后才能在这里推送。
          </div>
          <template v-else-if="lpOn">
            <a-input
              v-model:value="lpEvent"
              style="width: 100%; margin-top: 10px"
              :maxlength="80"
              placeholder="LitePan 事件名，留空则用转存配置里配的"
            />
            <div class="tm-hint">
              须与 LitePan 自动化规则里配的事件名一致；留空则按转存配置目录配的事件，目录也没配就不推送。
            </div>
          </template>
        </template>
      </div>

      <!-- 我的网盘：唯一可操作区（选目标位置） -->
      <div class="pane" style="margin-top: 16px">
        <div class="pane-hd">
          <span>保存到我的网盘</span>
          <span style="display: flex; gap: 6px">
            <!-- 绕过后端目录缓存直连重拉：网盘侧刚建/删了文件夹时用 -->
            <a-button size="small" :loading="treeRefreshing" @click="onRefreshTree">刷新</a-button>
            <a-button size="small" @click="mkfolder">新建文件夹</a-button>
          </span>
        </div>
        <div class="pane-bd">
          <LazyDirTree
            v-if="!USE_MOCK && isMainDrive"
            ref="mineTree"
            :type="target!.type as MainDriveType"
            :root-path="rootDir"
            @select="(p: string) => (selectedDir = p)"
          />
          <div v-else-if="!USE_MOCK" class="small" style="color: var(--text3); padding: 12px 0">
            该网盘的目录浏览暂未支持，可直接开始转存（目标目录不存在时会自动创建）。
          </div>
          <PkTree v-else :nodes="MINE_TREE" selectable :default-expand-depth="2" @select="onPick" />
        </div>
      </div>
      <div class="bcrumb">
        <span class="muted" style="color: var(--text3)">目标位置</span>
        <span>{{ selectedDir }}</span>
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

    <!-- 识别歧义候选（同名剧/电影时让用户挑，回填「文件夹更名」） -->
    <RecognizePicker v-model:open="pickerOpen" :candidates="pickerCands" :source-name="target?.name" @pick="onPickCandidate" />
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

/* 选项行内字段：label 小字在上、控件在下（下拉文案长，竖排不挤行） */
.tm-field { display: inline-flex; flex-direction: column; gap: 4px; min-width: 0; }
.tm-field label { font-size: 12px; color: var(--text3); }

/* 选项勾选框（样式对齐任务弹窗 mt-opt）：带框容器 + 自绘 16px 勾选块 */
.tm-opts {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 20px;
  padding: 10px 14px;
  border: 1px solid var(--split);
  border-radius: 10px;
  background: var(--surface-2);
}
.tm-opt {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  cursor: pointer;
  user-select: none;
  font-size: 13.5px;
  color: var(--text);
  line-height: 1.2;
  position: relative;
}
.tm-opt input { position: absolute; opacity: 0; width: 0; height: 0; }
.tm-opt .tm-box {
  position: relative;
  width: 16px;
  height: 16px;
  flex: 0 0 auto;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--card);
  transition: background 0.15s, border-color 0.15s;
}
.tm-opt:hover .tm-box { border-color: var(--primary); }
.tm-opt .tm-box::after {
  content: '';
  position: absolute;
  left: 5px;
  top: 1.5px;
  width: 4px;
  height: 9px;
  border: 2px solid #fff;
  border-top: 0;
  border-left: 0;
  transform: rotate(45deg) scale(0.4);
  opacity: 0;
  transition: opacity 0.12s, transform 0.12s;
}
.tm-opt input:checked + .tm-box { background: var(--primary); border-color: var(--primary); }
.tm-opt input:checked + .tm-box::after { opacity: 1; transform: rotate(45deg) scale(1); }

/* 文件夹更名：分享摘要下方整行（label 小字在上，输入框全宽） */
.tm-rename { margin-top: 14px; display: flex; flex-direction: column; gap: 6px; }
.tm-rename label { font-size: 12px; color: var(--text3); }

/* 联动区（对齐任务弹窗观感）：一行开关，开后下拉全宽堆叠 */
.tm-media {
  margin-top: 12px;
  padding: 11px 12px;
  border: 1px solid var(--split);
  border-radius: 10px;
  background: var(--surface-2);
}
.tm-media-switch { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--text2); cursor: pointer; }
.tm-hint { margin-top: 8px; font-size: 12px; color: var(--text3); line-height: 1.65; }

/* 移动端（<768px）：底部操作区改两行（说明一行 + 按钮铺满） */
@media (max-width: 767px) {
  .tm-body { max-height: 56dvh; }
  .tm-foot { flex-wrap: wrap; }
  .tm-foot .small { flex: 1 1 100%; margin-bottom: 2px; }
  .tm-foot :deep(.ant-btn) { flex: 1; }
}
</style>
