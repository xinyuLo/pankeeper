/* =====================================================================
 * 领域类型 —— 字段形状对齐原型 docs/02-data-model.md（接口契约）
 * 后端（Python）就绪后，接口返回体按这里定义的字段命名对齐。
 * 页面私有的扩展类型放各自视图文件夹，不要全部堆在这个文件里。
 * ===================================================================== */

/** 网盘类型：搜索结果支持 7 家；转存/任务只涉及前三家 */
export type DriveType = 'baidu' | 'quark' | '115' | '123' | 'ali' | 'xunlei' | 'uc' | 'magnet'

/** 转存/自动任务涉及的网盘 */
export type MainDriveType = 'baidu' | 'quark' | '115'

/** ===== 自动转存任务（docs/02 §1） ===== */
export interface PaTask {
  id: number
  type: MainDriveType
  /** 用哪个账号跑；null = 该类型默认账号 */
  acc_id: number | null
  name: string
  enabled: boolean
  share_url: string
  share_code: string
  save_dir: string
  /** 对比路径（去重基线，空则回退 save_dir） */
  compare_path?: string
  include_subdirs: boolean
  /** 定时策略（空 = 仅手动） */
  cron: string
  exclude_count: number
  /** 排除文件下标（真实系统应为文件路径/md5 列表） */
  exclIdx: number[]
  /** 文件过滤正则（转存链路先行过滤；空=不过滤） */
  regex_pattern?: string
  /** 已保存排除清单（文件名 + MD5，任一命中即排除） */
  exclude_names?: string[]
  exclude_md5s?: string[]
  last_run: string
  last_status: 'success' | 'partial' | 'fail' | 'running' | 'never'
  last_result: string
  post_qms: boolean
  /** Server 酱推送已改全局开关（推送通知页）控制，字段保留兼容旧记录 */
  post_notify?: boolean
}

/** ===== 转存队列（docs/02 §2） ===== */
export type QueueTaskStatus = 'wait' | 'run' | 'done' | 'fail' | 'warn'
export type QueuePhase = 'transfer' | 'waitqms' | 'qms' | 'waitstrm' | 'strm' | ''

export interface QueueLogLine {
  lv: 'STEP' | 'INFO' | 'WARN' | 'ERROR'
  txt: string
}

export interface QueueTask {
  id: number
  name: string
  type: MainDriveType
  path: string
  files: number
  size: string
  status: QueueTaskStatus
  phase: QueuePhase
  /** 当前阶段开始时刻（算 QMS/STRM 延迟用） */
  phaseStart: number
  progress: number
  /** 每阶段只发一次日志的标记 */
  flags: Record<string, number>
  /** 完成时刻（出队计时：保留 1 小时） */
  doneAt: number
  /** 来源自动任务的 id（自动转存入队时带；手动任务没有）——执行监控按它对上队列项 */
  paTaskId?: number | null
  /** 任务来源：search=搜索转存 / auto=自动转存（后端 enqueue 写入；restore 后可能缺失，别只认它） */
  source?: string
  logs: QueueLogLine[]
}

export interface QueueState {
  seq: number
  lastTick: number
  lastDone: number
  tasks: QueueTask[]
}

/** ===== 队列配置（docs/02 §3） ===== */
export interface QueueCfg {
  /** 并行上限 1-4 */
  threads: number
  /** 任务间隔秒 */
  gap: number
  /** 转存→QMS 延迟秒 */
  qms: number
  /** QMS→STRM 延迟秒 */
  strm: number
}

/** ===== 转存配置目录（docs/02 §4，快速转存的依据） ===== */
export interface DdItem {
  id: number
  type: MainDriveType
  /** 账号级作用域 */
  account: string
  /** 快速转存下拉顺序（小在前） */
  sort: number
  name: string
  path: string
  /** 该账号的默认目录（每账号唯一） */
  is_default: boolean
  qms_on: boolean
  /** → QMS 刮削目录 id */
  qms_id: number | null
  /** → STRM 同步目录 id（null = 不生成） */
  strm_id: number | null
}

/** 保存载荷：新建时 id 传 null（后端自增分配），更新时带已入库的正数 id。
 *  ⚠️ 别再用 id:0 之类的魔法值表示新建——0 是合法主键时它就是 bug 温床。 */
export type DdItemDraft = Omit<DdItem, 'id'> & { id?: number | null }

export interface DdQmsPath {
  id: number
  /** 中文媒体类型（电影/剧集），来自 qMediaSync media_type 映射 */
  media_type: string
  source_path: string
}

export interface DdStrmPath {
  id: number
  remote_path: string
}

export interface DdAccount {
  id: string
  label: string
}

/** ===== 转存记录（docs/02 §7，快照） ===== */
export interface RecordTag {
  st: string
  cls: 't-ok' | 't-bad' | 't-off'
}

/** 转存文件清单快照（记录详情「最近结果 → 详情」弹窗；后端 files_json） */
export interface RecordFileSnap {
  /** 分享内相对路径 */
  path: string
  name: string
  size: number
  /** 已转存 | 已在库跳过 | 未勾选 | 未转存 */
  st: string
}

export interface RecordItem {
  n: string
  t: DriveType
  p: string
  st: string
  cls: 't-ok' | 't-bad' | 't-off' | 't-warn'
  tm: string
  qms: RecordTag
  strm: RecordTag
  files?: RecordFileSnap[]
}

/** ===== 网盘连接（docs/02 §8：只回状态不回明文） ===== */
export type AccountStatus = 'connected' | 'expired' | 'unset'

export interface AccountInfo {
  type: MainDriveType
  /** 凭据形态说明（cookie/token），不含明文 */
  cred_kind: string
  status: AccountStatus
  last_check: string
  /** 「失效通知」开关（网盘连接页卡片上控制；探活失败时据此决定是否发 Server 酱） */
  notify: boolean
}

/** 网盘容量 + 会员摘要（卡片容量条 / 会员标签数据源；只含数字，绝不含凭据） */
export interface AccountSummary {
  capacity: { total: number; used: number } | null
  vip: { name: string; expires: string | null } | null
}

/** ===== 搜索结果行 ===== */
export interface SearchResultItem {
  n: string
  t: DriveType
  s: string
  d: string
  ok: boolean
  hot?: boolean
  /** 真实模式：分享链接与提取码（入队真实转存的必要字段；mock 模式为演示假链接） */
  url?: string
  share_code?: string
  source?: string
}

/** ===== 通用树节点（目录树/分享树 mock） ===== */
export interface TreeNode {
  name: string
  path?: string
  size?: string
  kids?: TreeNode[]
}
