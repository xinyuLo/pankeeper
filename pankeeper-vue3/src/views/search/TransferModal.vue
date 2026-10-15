<script lang="ts">
/* 视频扩展名（与后端 adapters/base.VIDEO_EXTS 同口径）：左栏清单禁选非视频文件用 */
const VIDEO_EXTS = new Set([
  '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.3gp', '.ts',
  '.iso', // 蓝光/DVD 原盘镜像（用户点名：iso 也是视频）
])
function isVideoFile(name: string): boolean {
  const dot = name.lastIndexOf('.')
  return dot >= 0 && VIDEO_EXTS.has(name.slice(dot).toLowerCase())
}

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
import { computed, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { FolderOutlined, ThunderboltOutlined } from '@ant-design/icons-vue'
import PkTree from '@/components/PkTree.vue'
import LazyDirTree from '@/components/LazyDirTree.vue'
import { USE_MOCK } from '@/api/http'
import { getRootDirs, listAccounts } from '@/api/modules/accounts'
import { listDdItems, listQmsPaths, listStrmPaths } from '@/api/modules/dd'
import { checkShareLink, getSearchShareFiles } from '@/api/modules/search'
import { extractShareCode } from '@/api/modules/tasks'
import { recognizeShare, type RecognizeCandidate } from '@/api/modules/recognize'
import { getSettings } from '@/api/modules/settings'
import { pkQueueCfgFetch } from '@/queue/engine'
import { ddStore } from '@/api/mock/dd'
import RecognizePicker from '@/components/RecognizePicker.vue'
import { pkQueue } from '@/queue/engine'
import { DRIVE_META } from '@/api/mock/meta'
import { MINE_TREE } from '@/api/mock/tree'
import type { DdItem, DdQmsPath, DdStrmPath, MainDriveType, TreeNode } from '@/types/model'

import { useBackGuard } from '@/composables/useBackGuard'
const props = defineProps<{ open: boolean; target: TransferTarget | null; linkMode?: boolean }>()
const emit = defineEmits<{ (e: 'update:open', v: boolean): void }>()
useBackGuard(() => props.open, () => emit('update:open', false))

/* ---- 链接直转模式（记录页「链接转存」入口，2026-10-15 定稿单弹窗两段式）：
 * 上段贴链接+提取码 →「识别」（探活 + 拉文件清单），成功后链接区折叠成摘要卡、
 * 下方展开转存表单；失败留在原位改了重试。该盘没配转存目录 → 拦下不让填。 ---- */
const linkTarget = ref<TransferTarget | null>(null)
/** 实际生效目标：搜索页传入优先；链接模式用解析结果。全弹窗统一读它。 */
const target = computed<TransferTarget | null>(() => props.target ?? linkTarget.value)
const linkUrl = ref('')
const linkCode = ref('')
const linkParsing = ref(false)
const linkError = ref('')
const parsed = computed(() => !!linkTarget.value)

const DEFAULT_DIR = USE_MOCK ? '/我的资源/影视/电视剧/国产剧' : '/'

/** 真实模式的目录树落点：网盘连接页配置了「默认根目录」就以它为根，只显示其子目录 */
const rootDir = ref('')
const rootDirs = ref<Record<string, string>>({})
/** 本次打开的根目录配置是否已就绪：没就绪先不渲染树（不然按真根白列一轮，等锁定了又重列） */
const rootReady = ref(false)
/** 只有三大盘支持真实目录浏览；其余盘允许直接转存（目标目录自动创建） */
const isMainDrive = computed(() => !!target.value && (['baidu', 'quark', '115'] as string[]).includes(target.value.type))

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

const meta = computed(() => (target.value ? DRIVE_META[target.value.type] : null))
/** 分享摘要行：资源名 + 「N 项 · X GB」跟着资源走（项数 mock 固定 12） */
const sumMeta = computed(() => `12 项 · ${target.value?.size || '82.4 GB'}`)

/** 识别分阶段提示：后端并行搜 TMDB 也要 2~3s（走代理），按耗时轮换文案让用户知道在动 */
const RECOGNIZE_STAGES: Array<[number, string]> = [
  [3000, '正在查询相关年份…'],
  [6000, '正在比对候选准确率…'],
  [9000, '正在整理候选结果…'],
]
function recognizeTipStart() {
  message.loading({ content: '正在识别…', key: 'recognize', duration: 0 })
  return RECOGNIZE_STAGES.map(([ms, text]) => window.setTimeout(() => {
    message.loading({ content: text, key: 'recognize', duration: 0 })
  }, ms))
}
function recognizeTipDone(timers: number[], ok: boolean, text: string) {
  timers.forEach((t) => window.clearTimeout(t))
  if (ok) message.success({ content: text, key: 'recognize', duration: 3 })
  else message.warning({ content: text, key: 'recognize', duration: 4 })
}

/** 一键识别：资源名 → TMDB → 回填「文件夹更名」（识别器后端无关，QMS/LitePan 模式都可用）。
 * 置信度高直接回填；歧义（同名剧/电影，实锤：狂飙 vs F1：狂飙飞车）弹候选卡片让用户挑。 */
const recognizing = ref(false)
const pickerOpen = ref(false)
const pickerCands = ref<RecognizeCandidate[]>([])
async function onRecognize() {
  const src = (target.value?.name || '').trim()
  if (!src) {
    message.warning('没有可识别的资源名')
    return
  }
  if (recognizing.value) return
  recognizing.value = true
  const timers = recognizeTipStart()
  try {
    const r = await recognizeShare(src, src, { share_type: target.value?.type || '', share_url: target.value?.url || '', share_code: target.value?.share_code || '' })
    if (r.ok && r.media_name) {
      // 联动：TMDB 认定电影/电视节目 → 同步右侧 radio（用户定稿：识别要能定类型）
      const kind = r.candidates?.[0]?.media_type
      if (kind === 'movie' || kind === 'tv') mediaKind.value = kind
      if (r.confident) {
        renameInput.value = r.media_name
        recognizeTipDone(timers, true, r.doubt ? `已识别（存疑，请确认）：${r.media_name}` : `已识别：${r.media_name}`)
      } else {
        recognizeTipDone(timers, true, `识别到 ${r.candidates?.length || 0} 个候选，请选择`)
        pickerCands.value = r.candidates || []
        pickerOpen.value = true
      }
    } else {
      recognizeTipDone(timers, false, r.message || '未识别到 TMDB 条目')
    }
  } catch {
    recognizeTipDone(timers, false, '识别失败，请稍后重试')
  } finally {
    recognizing.value = false
  }
}
function onPickCandidate(c: RecognizeCandidate) {
  renameInput.value = c.year ? `${c.title} (${c.year})` : c.title
  mediaKind.value = c.media_type // 用户挑了候选，类型跟着候选走
}

/* ---- 分享文件多选（左栏，必选）：真实清单（share_list_cache 联动，点过查看文件秒开）。
 * 文件检测是**最终守门**（2026-10-08 用户定稿）：没过（拉取失败/清单为空=坏链）→
 * 开始转存一直灰，不许再"整包转存"兜底。 ---- */
interface FileRow { path: string; name: string; size: number }
const filesLoading = ref(false)
const filesFailed = ref(false)
const fileRows = ref<FileRow[]>([])
const selPaths = ref<string[]>([])
/** 检测已完成且未通过：清单拉取失败，或成功但 0 个文件（空分享=坏链） */
const detectDone = ref(false)
const detectBad = computed(() => detectDone.value && (filesFailed.value || fileRows.value.length === 0))

function fmtSize(n: number): string {
  if (!n) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let x = n
  let i = 0
  while (x >= 1024 && i < units.length - 1) { x /= 1024; i++ }
  return `${i === 0 || x >= 100 ? Math.round(x) : x.toFixed(1)} ${units[i]}`
}
async function loadShareFiles() {
  filesLoading.value = true
  filesFailed.value = false
  detectDone.value = false
  fileRows.value = []
  selPaths.value = []
  try {
    const meta = await getSearchShareFiles(target.value!.type, target.value!.url || '', target.value!.share_code || '')
    fileRows.value = (meta.files || [])
      .filter((f) => !f.is_dir)
      .map((f) => ({ path: f.path, name: f.name, size: f.size }))
  } catch {
    filesFailed.value = true // 清单拉不到 = 检测未通过：开始转存保持灰，不许转
  } finally {
    filesLoading.value = false
    detectDone.value = true
  }
}
/** 可勾选的文件：过滤其他文件开着时只放行视频（非视频行禁用） */
const selectableRows = computed(() => (onlyVideo.value ? fileRows.value.filter((f) => isVideoFile(f.name)) : fileRows.value))
function toggleAllFiles(e: Event) {
  selPaths.value = (e.target as HTMLInputElement).checked ? selectableRows.value.map((f) => f.path) : []
}
function toggleFile(path: string) {
  selPaths.value = selPaths.value.includes(path)
    ? selPaths.value.filter((x) => x !== path)
    : [...selPaths.value, path]
}
const allSelected = computed(() => selectableRows.value.length > 0 && selPaths.value.length === selectableRows.value.length)
/** 已选文件总大小：勾了算勾选的；一个没勾按整包算（参考值） */
const selTotal = computed(() => {
  const m = new Map(fileRows.value.map((f) => [f.path, f.size]))
  const picked = selPaths.value.length ? selPaths.value : fileRows.value.map((f) => f.path)
  return picked.reduce((s, p) => s + (m.get(p) || 0), 0)
})

/* ---- 目标目录树 ---- */
const selectedDir = ref(DEFAULT_DIR)
function onPick(node: TreeNode) {
  // 只认有 path 的节点（分享树式节点没有 path，这里树里都有）
  if (node.path) selectedDir.value = node.path
}

/* ---- 选项（手动转存没有 Server 酱推送，别加回来） ---- */
const includeSub = ref(true)
/* 文件夹更名：非空 = 在目标目录下按此名新建文件夹、分享内容剥壳转入 */
const renameInput = ref('')

/* ---- QMS 联动（对齐任务弹窗样式：开关 + 全宽下拉）。
 *  STRM 2026-10-09 恢复弹窗可选：不选 = 目录值 → 自动配对兜底；选了 = 按指定的触发。
 *  下拉默认按目标位置前缀自动带出；用户手动改过就不再自动覆盖。
 *  开关关 = 明确不联动（media_off，连目录匹配都不做）。 ---- */
const mediaOn = ref(true)
const qmsSel = ref<number | null>(null)
const strmSel = ref<number | null>(null)
/** 过滤其他文件：默认开——本次转存只保存视频文件（mkv/mp4/iso 等），nfo/图片等杂件直接跳过 */
const onlyVideo = ref(true)
// 开「过滤其他文件」的瞬间：把已勾选的非视频路径剔掉（否则禁选行还挂着勾、计数也错）
watch(onlyVideo, (v) => {
  if (v) selPaths.value = selPaths.value.filter((p) => fileRows.value.some((f) => f.path === p && isVideoFile(f.name)))
})
// LitePan 模式：联动行换成「推 LitePan」开关 + 事件名输入框（空=按转存配置目录/全局默认）
const lpOn = ref(true)
const lpEvent = ref('')
const mediaBackend = ref<'qms' | 'litepan'>('qms')
/** QMS/STRM 反转（队列配置全局）：开启时联动 QMS 必须选 STRM 同步路径 */
const reverseOn = ref(false)
/** 转存路径 = 目标位置 + 壳名（更名值优先，回落资源名） */
const savePath = computed(() => {
  const dir = (selectedDir.value || '').replace(/\/+$/, '')
  const shell = renameInput.value.trim() || target.value?.name || ''
  return `${dir}/${shell}`
})
const qmsPaths = ref<DdQmsPath[]>([])
const strmPaths = ref<DdStrmPath[]>([])
const pathsLoading = ref(false)
const mediaTouched = ref(false)

const qmsOpts = computed(() => qmsPaths.value.map((p) => ({ value: p.id, label: `#${p.id} · ${p.source_path}` })))
const strmOpts = computed(() => strmPaths.value.map((p) => ({ value: p.id, label: `#${p.id} · ${p.remote_path}` })))

/** 目标目录命中的转存配置（最长前缀优先，对齐后端 _match_dd_link 语义） */
function hitDd(pred: (d: DdItem) => boolean): DdItem | null {
  const t = target.value
  if (!t) return null
  const dir = selectedDir.value
  return (
    (ddStore.items as DdItem[])
      .filter((d) => d.type === t.type && pred(d) && (dir === d.path || dir.startsWith((d.path || '').replace(/\/+$/, '') + '/')))
      .sort((a, b) => b.path.length - a.path.length)[0] || null
  )
}
/** 目录配置命中（用于**初始状态**：目标目录开了联动 → 开关默认开、带出目录配的目标；
 *  之后用户随便切——2026-10-07 用户定稿：弹窗里永远可选，不再因「没配目录」禁用。
 *  QMS 下拉显式选了就直传后端；LitePan 输入框填了事件名 = 一次性联动，不要求目录配过）。 */
const qmsHit = computed(() => hitDd((d) => !!d.qms_on && !!d.qms_id))
const lpHit = computed(() => hitDd((d) => !!d.lp_on))

/** 打开弹窗：重置为「联动开 + 目录默认值」（2026-10-08 用户定稿：默认打开） */
function resetMedia() {
  mediaOn.value = true
  lpOn.value = true
  qmsSel.value = qmsHit.value?.qms_id ?? null
  strmSel.value = qmsHit.value?.strm_id ?? null
  lpEvent.value = lpHit.value?.lp_event || ''
}
/** 点目录跟随带出联动目标（对齐后端 _match_dd_link 语义）。
 *  ⚠️ 联动开关**关着时什么都不动**——用户关了就是关了，点目录不许重新打开/覆盖
 *  （2026-10-08 用户实锤）；开着才跟随目录切换带出该目录配的默认值。 */
function autoMatchMedia() {
  if (mediaBackend.value === 'qms') {
    if (mediaOn.value) {
      qmsSel.value = qmsHit.value?.qms_id ?? null
      strmSel.value = qmsHit.value?.strm_id ?? null
    }
  } else if (lpOn.value) {
    lpEvent.value = lpHit.value?.lp_event || ''
  }
}
watch(selectedDir, () => {
  if (!mediaTouched.value) autoMatchMedia()
})
// 关掉再打开：目录默认值立刻带回来（下拉没被手动改过时）
watch(mediaOn, (v) => {
  if (v && !mediaTouched.value && mediaBackend.value === 'qms') {
    qmsSel.value = qmsHit.value?.qms_id ?? null
    strmSel.value = qmsHit.value?.strm_id ?? null
  }
})
watch(lpOn, (v) => {
  if (v && !mediaTouched.value && mediaBackend.value === 'litepan') lpEvent.value = lpHit.value?.lp_event || ''
})

/* ---- 链接直转：解析四步（域名定盘 → 配置闸 → 探活 → 拉清单）。
 * 识别按钮 = 这四步；成功折叠摘要卡、失败留原位改了重试（2026-10-15 定稿）。 ---- */
const DRIVE_DOMAINS: Array<[RegExp, TransferTarget['type']]> = [
  [/pan\.baidu\.com/i, 'baidu'],
  [/pan\.quark\.cn/i, 'quark'],
  [/(?:115|115cdn|anxia)\.com/i, '115'],
]
/** 电影/电视节目（用于推荐目标位置）：切到与当前目录 media_type 不符时自动跳到匹配目录 */
const mediaKind = ref<'movie' | 'tv' | ''>('')

async function onParseLink() {
  if (linkParsing.value) return
  const url = linkUrl.value.trim()
  if (!url) {
    message.warning('请先粘贴分享链接')
    return
  }
  const type = DRIVE_DOMAINS.find(([re]) => re.test(url))?.[1]
  if (!type) {
    linkError.value = '无法识别的网盘链接（支持百度 / 夸克 / 115）'
    return
  }
  linkParsing.value = true
  linkError.value = ''
  linkTarget.value = null
  try {
    // 账号闸（2026-10-15 用户定稿，就一条：有没有这个网盘）——链接转存 = 普通转存，
    // 不要求配过「转存配置」，目录树随便选、目标目录自动创建
    const accs = (await listAccounts().catch(() => [])) as Array<{ type: string }>
    if (!accs.some((a) => a.type === type)) {
      linkError.value = `还没有 ${DRIVE_META[type]?.name || type} 账号，先到「网盘连接」添加`
      return
    }
    // 提取码：框里填的优先，空则按 ?pwd=/密码=/括号/末尾四位规则自动识别回填
    const code = (linkCode.value.trim() || extractShareCode(url)).trim()
    if (code && !linkCode.value.trim()) linkCode.value = code
    // 探活：死链/缺码在这里拦（locked 语义 2026-10-15 起后端真实现）；
    // uncertain/unknown 与搜索页同策略放行，靠文件清单这道最终守门
    const chk = await checkShareLink(type, url, code)
    if (chk.state === 'bad') {
      linkError.value = `分享已失效（${chk.summary || '检测未通过'}）`
      return
    }
    if (chk.state === 'locked') {
      linkError.value =
        chk.has_code === false
          ? '链接需要提取码——链接和提取码框都没带上，补码后重新识别'
          : '提取码不对，核对后重新识别'
      return
    }
    const list = await getSearchShareFiles(type, url, code)
    const rows = list.files || []
    if (!rows.length) {
      linkError.value = '分享内容为空（可能已失效），无法转存'
      return
    }
    // 资源名回落：挑最大的视频文件名去扩展名（更名框可手改，右侧「识别」可再过 TMDB 校准）
    const vids = rows.filter((f) => !f.is_dir && isVideoFile(f.name)).sort((a, b) => b.size - a.size)
    const baseName = (vids[0]?.name || rows.find((f) => !f.is_dir)?.name || '链接分享').replace(/\.[^.]+$/, '')
    const total = rows.filter((f) => !f.is_dir).reduce((s, f) => s + f.size, 0)
    linkTarget.value = { type, name: baseName, size: fmtSize(total), url, share_code: code }
    renameInput.value = baseName
    // 电影/电视节目初判：文件名带季/集特征归电视节目
    mediaKind.value = /S\d{1,2}E\d{1,2}|第\s*\d+\s*[集期]|Season\s*\d|第[一二三四五六七八九十]+季/i.test(rows.map((f) => f.name).join(' ')) ? 'tv' : 'movie'
    // 表单就位：目录树根按该盘重取、文件清单（缓存命中毫秒级）、联动默认值
    rootDir.value = rootDirs.value[type] || ''
    selectedDir.value = rootDir.value || DEFAULT_DIR
    void loadShareFiles()
    resetMedia()
    // 自动过一遍 TMDB 识别：更名校准 + 电影/电视节目联动（候选带 media_type；
    // 加载提示走全局 message，与成功 toast 互不干扰）
    void onRecognize()
    message.success(`链接有效，共 ${rows.filter((f) => !f.is_dir).length} 个文件，请在下方确认转存配置`)
  } catch {
    linkError.value = '文件清单获取失败——链接可能已失效，无法转存'
  } finally {
    linkParsing.value = false
  }
}
/** 摘要卡上的「重新识别」：回到链接输入态（链接和码保留，方便改一半重试） */
function reEditLink() {
  linkTarget.value = null
  linkError.value = ''
}
/** 切电影/电视节目：当前目录的 media_type 不符且有匹配目录 → 自动跳过去 */
function onKindChange(k: 'movie' | 'tv') {
  const dir = selectedDir.value
  const cur = hitDd((d) => dir === d.path || dir.startsWith((d.path || '').replace(/\/+$/, '') + '/'))
  if (cur && cur.media_type && cur.media_type !== k) {
    const alt = (ddStore.items as DdItem[]).find((d) => d.type === target.value?.type && d.media_type === k)
    if (alt) {
      selectedDir.value = alt.path
      message.info(`已切到${k === 'tv' ? '剧集' : '电影'}目录：${alt.path}`)
    }
  }
}

/** 每次打开重置：分享树收起、勾选清空、目标位置回默认国产剧 */
  watch(
  () => props.open,
  async (v) => {
    if (!v) return
    if (props.linkMode) {
      // 链接直转：回输入态重新开始（链接/码也清空）
      linkTarget.value = null
      linkUrl.value = ''
      linkCode.value = ''
      linkError.value = ''
      mediaKind.value = ''
    }
    selPaths.value = []
    renameInput.value = ''
    detectDone.value = false
    if (props.target?.url) void loadShareFiles() // 左栏文件清单（缓存联动秒开）；链接模式由 onParseLink 成功后触发
    // 打开即选中锁定根（默认根目录）；没配置就回退原来的默认
    rootReady.value = false
    if (!USE_MOCK) rootDirs.value = await getRootDirs().catch(() => ({}))
    rootDir.value = rootDirs.value[target.value?.type || ''] || ''
    rootReady.value = true
    selectedDir.value = rootDir.value || DEFAULT_DIR
    includeSub.value = true
    getSettings().then((d) => (mediaBackend.value = d.media?.backend || 'qms')).catch(() => {})
    pkQueueCfgFetch().then((c) => (reverseOn.value = !!c.reverse)).catch(() => {})
    onlyVideo.value = true
    mediaTouched.value = false
    // QMS/STRM 目录清单后台拉（QMS 在 NAS 上，秒级）；到货后按目标目录带默认值
    if (!USE_MOCK) {
      listDdItems().catch(() => {})
      pathsLoading.value = true
      const qs = await listQmsPaths().catch(() => [])
      strmPaths.value = await listStrmPaths().catch(() => [])
      pathsLoading.value = false
      qmsPaths.value = qs
    }
    resetMedia()
  },
)

function close() {
  emit('update:open', false)
}

/** 入队即走：toast 报位次、弹窗立即关闭。
 * 必选：左栏至少勾一个文件（清单可用时）+ 右栏目标位置。
 * 填了更名 = 建壳承接、只转勾选文件（单文件夹单/少文件，正合刮削要求）；
 * 没填更名 = 按勾选路径直接转（子目录结构保留）。清单失败回退"全转"老行为。 */
function start() {
  const t = target.value
  if (!t) return
  if (filesLoading.value) {
    message.warning('正在检测资源，请稍候…')
    return
  }
  if (detectBad.value) {
    message.error('文件检测未通过（链接可能已失效），无法转存')
    return
  }
  if (!selectedDir.value) {
    message.warning('请先在右侧选择目标位置')
    return
  }
  const picked = selPaths.value
  if (fileRows.value.length && !picked.length) {
    message.warning('请先在左侧勾选要转存的文件')
    return
  }
  const rename = renameInput.value.trim()
  const files = fileRows.value.length
  if (mediaBackend.value === 'qms' && mediaOn.value && reverseOn.value && strmSel.value == null) {
    message.warning('已开启 QMS/STRM 反转：请选择 STRM 同步路径（可到「队列配置」关闭反转）')
    return
  }
  const pos = pkQueue.enqueue({
    name: t.name,
    type: t.type,
    path: selectedDir.value,
    files,
    size: t.size,
    share_url: t.url,
    share_code: t.share_code,
    /* 更名填了 = 建壳承接（更名文件夹 + 只转勾选文件）；没填 = 按勾选路径直接转 */
    rename,
    with_shell: !!rename,
    file_paths: picked,
    /* 勾选了嵌套路径时必须带子目录列举，否则 only_paths 找不到文件 */
    include_subdirs: includeSub.value || picked.some((x) => x.includes('/')),
    /* 联动：开关关 = 明确不触发；开 = 用下拉选的 QMS（默认按目标位置自动带出）。
       STRM 同款：弹窗选了就直传，没选 = 后端目录值 → 自动配对兜底，刮削成功才生成。
       LitePan 模式：lp_event 带弹窗填的事件名（后端按 media.backend 分流，qms 时忽略） */
    media_off: mediaBackend.value === 'litepan' ? !lpOn.value : !mediaOn.value,
    qms_id: mediaBackend.value === 'qms' && mediaOn.value ? qmsSel.value : null,
    strm_id: mediaBackend.value === 'qms' && mediaOn.value ? strmSel.value : null,
    lp_event: mediaBackend.value === 'litepan' && lpOn.value ? lpEvent.value.trim() : '',
    only_video: onlyVideo.value,
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
        <template v-if="target && meta">
          <span class="chip tm-chip" :style="{ background: meta.color }">{{ meta.name }}</span>
          <span>转存到{{ meta.full }}</span>
        </template>
        <template v-else>
          <span>链接转存</span>
        </template>
      </div>
    </template>

    <!-- 链接直转（linkMode）：识别中链接框让位 loading；成功折叠摘要卡；失败留原位可改 -->
    <div v-if="linkMode" class="tm-linkbox">
      <div v-if="linkParsing" class="tm-link-loading">
        <a-spin size="small" />
        <span>正在检测链接有效性…（探活 + 拉文件清单，首次要几秒）</span>
      </div>
      <div v-else-if="parsed && target" class="tm-link-ok">
        <span class="chip tm-chip" :style="{ background: meta?.color }">{{ meta?.name }}</span>
        <span class="tm-link-ok-name" :title="target.name">{{ target.name }}</span>
        <span class="tm-link-ok-meta">
          {{ filesLoading ? '清单加载中…' : `${fileRows.length} 个文件 · ${fmtSize(selTotal)}` }}
        </span>
        <span style="flex: 1"></span>
        <a-button size="small" @click="reEditLink">重新识别</a-button>
      </div>
      <template v-else>
        <div class="tm-link-row">
          <label>分享链接</label>
          <a-input
            v-model:value="linkUrl"
            :maxlength="500"
            placeholder="粘贴 pan.baidu.com / pan.quark.cn / 115 分享链接"
            allow-clear
            @pressEnter="onParseLink"
          />
          <a-button type="primary" :loading="linkParsing" @click="onParseLink">识别</a-button>
        </div>
        <div class="tm-link-row tm-link-code">
          <label>提取码</label>
          <a-input v-model:value="linkCode" :maxlength="16" placeholder="链接末尾 ?pwd= 会自动识别" allow-clear />
          <span v-if="linkError" class="tm-link-err">{{ linkError }}</span>
        </div>
      </template>
    </div>

    <div v-if="target" class="tm-body">
      <!-- 转存路径：目标位置 + 壳名（更名值/资源名），随选择实时更新 -->
      <div class="tm-savepath">
        <span class="tm-savepath-label">转存路径</span>
        <span class="tm-savepath-val" :title="savePath">{{ savePath }}</span>
      </div>

      <!-- 文件夹更名：撑满整行 + 电影/电视节目（推荐目标位置）；「识别」= TMDB 识别回填 -->
      <div class="tm-rename">
        <label>文件夹更名</label>
        <div class="tm-rename-row">
          <a-input v-model:value="renameInput" :maxlength="80" placeholder="留空则用资源名新建文件夹" allow-clear>
            <template #suffix>
              <a-button size="small" class="tm-recog" :loading="recognizing" @click="onRecognize">
                <template #icon><ThunderboltOutlined /></template>
                识别
              </a-button>
            </template>
          </a-input>
          <a-radio-group
            :value="mediaKind"
            size="small"
            class="tm-kind"
            @change="(e: any) => { mediaKind = e.target.value; onKindChange(e.target.value) }"
          >
            <a-radio-button value="movie">电影</a-radio-button>
            <a-radio-button value="tv">电视节目</a-radio-button>
          </a-radio-group>
        </div>
      </div>

      <!-- 联动：按「系统设置 → 联动后端」切换 QMS / LitePan 表单 -->
      <div class="tm-media">
        <template v-if="mediaBackend === 'qms'">
          <label class="tm-media-switch">
            <a-switch v-model:checked="mediaOn" size="small" />
            <span>联动 QMS</span>
          </label>
          <label class="tm-media-switch">
            <a-switch v-model:checked="onlyVideo" size="small" />
            <span>过滤其他文件</span>
          </label>
          <label class="tm-opt tm-sub">
            <input v-model="includeSub" type="checkbox" /><span class="tm-box"></span>
            <a-tooltip title="分享内有子目录时一并转存">
              <span class="tm-sub-label">子目录</span>
            </a-tooltip>
          </label>
          <template v-if="mediaOn">
            <a-select
              v-model:value="qmsSel"
              :options="qmsOpts"
              style="width: 100%; margin-top: 10px"
              :loading="pathsLoading"
              :placeholder="pathsLoading ? '正在加载 QMS 目录…' : qmsPaths.length ? '选择 QMS 刮削目录' : 'QMS 未连接或没有刮削目录'"
              allow-clear
              @change="mediaTouched = true"
            />
            <a-select
              v-model:value="strmSel"
              :options="strmOpts"
              style="width: 100%; margin-top: 10px"
              :loading="pathsLoading"
              :placeholder="pathsLoading ? '正在加载 STRM 同步路径…' : strmPaths.length ? '选择 STRM 同步路径（可选）' : 'QMS 暂无 STRM 同步路径，不选则自动配对'"
              allow-clear
              @change="mediaTouched = true"
            />
            <div class="tm-hint">
              默认按目标位置自动带出。STRM 不选 = 自动配对（同步路径的来源 = QMS 整理目标时生效），选了 = 按指定的触发；
              QMS 整理成功才生成 STRM，失败不生成。
            </div>
          </template>
          <div v-else class="tm-hint">
            已关闭：本次转存完成后不触发 QMS 刮削与 STRM 生成。
          </div>
        </template>
        <template v-else>
          <label class="tm-media-switch">
            <a-switch v-model:checked="lpOn" size="small" />
            <span>联动 LitePan</span>
          </label>
          <label class="tm-media-switch">
            <a-switch v-model:checked="onlyVideo" size="small" />
            <span>过滤其他文件</span>
          </label>
          <label class="tm-opt tm-sub">
            <input v-model="includeSub" type="checkbox" /><span class="tm-box"></span>
            <a-tooltip title="分享内有子目录时一并转存">
              <span class="tm-sub-label">子目录</span>
            </a-tooltip>
          </label>
          <template v-if="lpOn">
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
          <div v-else class="tm-hint">
            已关闭：本次转存完成后不推送 LitePan。
          </div>
        </template>
      </div>

      <!-- 双栏：左=分享文件多选（必选）/ 右=目标位置目录树（必选，无新建文件夹） -->
      <div class="tm-split">
        <div class="tm-col">
          <div class="tm-col-hd">
            <span class="tm-col-t">分享内容</span>
            <span class="tm-col-n" :class="{ ok: allSelected }">{{ selPaths.length }}/{{ fileRows.length }}</span>
          </div>
          <div class="tm-col-list">
            <div v-if="filesLoading" class="tm-col-loading">
              <a-spin size="small" />
              <span class="small muted">正在获取文件清单…（点过「查看文件」的分享秒开）</span>
            </div>
            <template v-else-if="fileRows.length">
              <label class="tm-file tm-file-all">
                <input type="checkbox" :checked="allSelected" @change="toggleAllFiles" />
                <b>全选</b>
              </label>
              <label
                v-for="f in fileRows"
                :key="f.path"
                class="tm-file"
                :class="{ on: selPaths.includes(f.path), off: onlyVideo && !isVideoFile(f.name) }"
                :title="onlyVideo && !isVideoFile(f.name) ? '非视频文件——开启「过滤其他文件」时不参与转存' : undefined"
              >
                <input
                  type="checkbox"
                  :checked="selPaths.includes(f.path)"
                  :disabled="onlyVideo && !isVideoFile(f.name)"
                  @change="toggleFile(f.path)"
                />
                <span class="tm-file-name" :title="f.name">{{ f.name }}</span>
                <span class="tm-file-size">{{ fmtSize(f.size) }}</span>
              </label>
            </template>
            <div v-else class="tm-col-loading">
              <span class="small" style="color: var(--error)">文件检测未通过（清单拉取失败或为空）——链接可能已失效，无法转存</span>
            </div>
          </div>
        </div>
        <div class="tm-col">
          <div class="tm-col-hd">
            <span class="tm-col-t">目标位置</span>
            <span class="tm-col-path" :title="selectedDir">{{ selectedDir }}</span>
            <a-button size="small" :loading="treeRefreshing" @click="onRefreshTree">刷新</a-button>
          </div>
          <div class="tm-col-list">
            <LazyDirTree
              v-if="!USE_MOCK && isMainDrive && rootReady"
              ref="mineTree"
              :type="target!.type as MainDriveType"
              :root-path="rootDir"
              @select="(p: string) => (selectedDir = p)"
            />
            <div v-else-if="!USE_MOCK && isMainDrive && !rootReady" class="tm-col-loading">
              <a-spin size="small" />
              <span class="small muted">正在读取默认根目录…</span>
            </div>
            <div v-else-if="!USE_MOCK" class="small" style="color: var(--text3); padding: 12px 0">
              该网盘的目录浏览暂未支持，可直接开始转存（目标目录不存在时会自动创建）。
            </div>
            <PkTree v-else :nodes="MINE_TREE" selectable :default-expand-depth="2" @select="onPick" />
          </div>
        </div>
      </div>

    </div>

    <div class="tm-foot">
      <template v-if="target">
        <span class="small muted">
          {{
            detectBad
              ? '文件检测未通过 · 链接可能已失效，无法转存'
              : filesLoading
                ? '正在检测资源…'
                : `已选 ${selPaths.length}/${fileRows.length} 个文件 · 共 ${fmtSize(selTotal)} · 只转存勾选内容`
          }}
        </span>
        <span style="flex: 1"></span>
      </template>
      <span v-else class="small muted">粘贴分享链接点「识别」，检测通过后在这里确认转存配置</span>
      <span style="flex: 1"></span>
      <a-button @click="close">取消</a-button>
      <a-button
        v-if="target"
        type="primary"
        :disabled="filesLoading || detectBad"
        :title="filesLoading ? '正在检测资源，请稍候…' : detectBad ? '文件检测未通过（链接可能已失效）' : undefined"
        @click="start"
      >开始转存</a-button>
    </div>

    <!-- 识别歧义候选（同名剧/电影时让用户挑，回填「文件夹更名」） -->
    <RecognizePicker v-model:open="pickerOpen" :candidates="pickerCands" :source-name="target?.name" @pick="onPickCandidate" />
  </a-modal>
</template>

<style scoped>
.tm-head { display: flex; align-items: center; gap: 9px; font-size: 16px; font-weight: 600; }
.tm-chip { width: 24px; height: 24px; border-radius: 6px; font-size: 11px; }
.tm-body { max-height: 68vh; overflow: auto; padding: 4px 2px; }
/* 转存路径行：目标位置 + 壳名实时拼接 */
.tm-savepath {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border: 1px solid rgba(22, 119, 255, 0.25);
  border-radius: 8px;
  background: rgba(22, 119, 255, 0.05);
  margin-bottom: 12px;
}
.tm-savepath-label { flex: none; font-size: 12px; color: var(--text3); }
.tm-savepath-val {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  color: var(--text2);
}
/* 双栏：左=分享文件多选（必选）/ 右=目标位置目录树（必选） */
.tm-split { display: flex; gap: 12px; margin-top: 14px; }
.tm-col { flex: 1; min-width: 0; border: 1px solid var(--split); border-radius: 10px; overflow: hidden; display: flex; flex-direction: column; }
.tm-col-hd {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--split);
  background: var(--surface-2);
}
.tm-col-t { font-weight: 600; font-size: 13px; }
.tm-col-n { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 11px; color: var(--primary); background: rgba(22, 119, 255, 0.1); border-radius: 999px; padding: 0 8px; line-height: 17px; }
.tm-col-n.ok { color: #237804; background: rgba(82, 196, 26, 0.12); }
.tm-col-path { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; color: var(--text2); }
.tm-col-list { height: 220px; overflow: auto; padding: 6px; }
.tm-col-loading { display: flex; align-items: center; gap: 10px; justify-content: center; padding: 24px 0; }
.tm-file {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 12.5px;
}
.tm-file:hover { background: rgba(22, 119, 255, 0.07); }
.tm-file.on { background: rgba(22, 119, 255, 0.06); }
.tm-file-all { border-bottom: 1px dashed var(--split); border-radius: 0; margin-bottom: 4px; }
.tm-file-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tm-file-size { flex: none; color: var(--text3); font-size: 11.5px; }
/* 非视频文件（过滤其他文件开着时）：显示但禁选置灰（普通转存的口径——与快速转存的"直接消失"区分） */
.tm-file.off { opacity: 0.45; cursor: not-allowed; }
.tm-file.off:hover { background: transparent; }
.tm-file.off input { cursor: not-allowed; }

/* 右栏目录管理工具条：虚线分隔与左栏「全选」行同款观感 */
.tm-col-list :deep(.ldt-toolbar) {
  border-bottom: 1px dashed var(--split);
  padding-bottom: 8px;
  margin-bottom: 4px;
}
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

/* 勾选框（样式对齐任务弹窗 mt-opt）：自绘 16px 勾选块 */
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

/* 文件夹更名：label 在上、输入框+电影/电视节目 radio 横排在下 */
.tm-rename { margin-top: 14px; display: flex; flex-direction: column; gap: 6px; }
.tm-rename label { font-size: 12px; color: var(--text3); }
.tm-rename-row { display: flex; align-items: center; gap: 8px; min-width: 0; }
.tm-rename-row .ant-input-affix-wrapper { flex: 1; min-width: 0; }
.tm-kind { flex: none; }
/* 选中态填充主色（antd 默认浅蓝底看不清，用户点名调整）：实底白字 */
.tm-kind :deep(.ant-radio-button-wrapper-checked) {
  background: var(--primary, #1677ff);
  border-color: var(--primary, #1677ff);
}
.tm-kind :deep(.ant-radio-button-wrapper-checked:not(:first-child)) { border-inline-start-color: var(--primary, #1677ff); }
.tm-kind :deep(.ant-radio-button-wrapper-checked span) { color: #fff; }

/* 链接直转区（linkMode）：边框盒与「转存路径」行同观感；错误红字跟在提取码行尾 */
.tm-linkbox {
  margin-bottom: 12px;
  padding: 10px 12px;
  border: 1px solid rgba(22, 119, 255, 0.25);
  border-radius: 8px;
  background: rgba(22, 119, 255, 0.05);
}
.tm-link-row { display: flex; align-items: center; gap: 8px; min-width: 0; }
.tm-link-row + .tm-link-row { margin-top: 8px; }
.tm-link-row label { flex: none; font-size: 12px; color: var(--text3); }
.tm-link-row .ant-input-affix-wrapper { flex: 1; min-width: 0; }
.tm-link-code .ant-input-affix-wrapper { flex: 0 1 220px; }
.tm-link-err { color: var(--error); font-size: 12px; line-height: 1.5; min-width: 0; }
.tm-link-loading { display: flex; align-items: center; gap: 10px; justify-content: center; padding: 14px 0; font-size: 13px; color: var(--text2); }
.tm-link-ok { display: flex; align-items: center; gap: 9px; min-width: 0; }
.tm-link-ok-name { font-weight: 600; font-size: 13.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tm-link-ok-meta { font-size: 12px; color: var(--text3); flex: none; }
/* 子目录：联动框开关行里的小号灰边框盒（跟在「过滤其他文件」后面） */
.tm-opt.tm-sub {
  height: 22px; /* 与旁边 small 开关等高（16px 开关+描边余量），vertical-align 对齐上沿 */
  padding: 0 8px;
  margin-right: 0;
  border: 1px solid var(--split);
  border-radius: 6px;
  background: var(--card);
  vertical-align: middle;
}
.tm-sub-label { font-size: 12.5px; color: var(--text2); line-height: 1; }
.tm-sub .tm-box { width: 14px; height: 14px; }
.tm-sub .tm-box::after { left: 4px; top: 1px; width: 3.5px; height: 8px; }
/* 识别按钮：淡紫描边 + 闪电图标（智能识别一族的颜色语言），悬停加深 */
.tm-recog {
  color: #8c73e6;
  border-color: rgba(140, 115, 230, 0.5);
  background: rgba(140, 115, 230, 0.06);
  box-shadow: none;
}
/* 图标贴紧文字（antd 默认图标/文字双向留 8px，小按钮里松散） */
.tm-recog :deep(.ant-btn-icon + span) { margin-inline-start: 4px; }
.tm-recog :deep(.anticon) { margin-inline-end: 0; }
.tm-recog:hover, .tm-recog:focus-visible {
  color: #7451d8;
  border-color: #8c73e6;
  background: rgba(140, 115, 230, 0.12);
}

/* 联动区（对齐任务弹窗观感）：整行 + min-height 按展开态占位（开关切换框体不跳） */
.tm-media {
  margin-top: 12px;
  min-height: 140px;
  padding: 11px 12px;
  border: 1px solid var(--split);
  border-radius: 10px;
  background: var(--surface-2);
}
.tm-media-switch { display: inline-flex; align-items: center; gap: 8px; font-size: 13px; color: var(--text2); cursor: pointer; margin-right: 24px; vertical-align: middle; }
.tm-hint { margin-top: 8px; font-size: 12px; color: var(--text3); line-height: 1.65; }

/* 移动端（<768px）：⚠️ 本块必须排在所有桌面规则之后——同优先级下后写的赢，
   之前排前面被 tm-rename/tm-media 的桌面规则覆盖，手机上更名列被 flex-basis
   撑出 240px 空洞、子目录被推到右边（2026-10-08 实锤） */
@media (max-width: 767px) {
  .tm-body { max-height: 56dvh; }
  .tm-split { flex-direction: column; }
  /* 更名输入框与「子目录」保持同行（2026-10-08 用户定稿）：只收紧 flex 基准不换行 */
  .tm-rename { flex: 1 1 140px; }
  .tm-media { min-height: 0; }
  /* 链接直转：窄屏输入行允许折行，提取码框占满一行 */
  .tm-link-row { flex-wrap: wrap; }
  .tm-link-code .ant-input-affix-wrapper { flex: 1 1 100%; }
  .tm-rename-row { flex-wrap: wrap; }
  .tm-foot { flex-wrap: wrap; }
  .tm-foot .small { flex: 1 1 100%; margin-bottom: 2px; }
  .tm-foot :deep(.ant-btn) { flex: 1; }
}
</style>
