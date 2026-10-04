import { reactive } from 'vue'
import { get, post, put, USE_MOCK } from '@/api/http'
import type { QueueCfg, QueueState, QueueTask } from '@/types/model'

/* =====================================================================
 * 转存队列引擎（双模式，导出接口对两种模式完全一致，组件层无感）：
 * - mock 模式（VITE_USE_MOCK!=='false'）：本地 600ms tick 模拟（原型 queue-core.js 移植，
 *   localStorage `pkq_v2` / `pkq_cfg`）；
 * - 真实模式：消费后端队列（SSE /queue/events 推变化为主，/queue/state 仅作兜底轮询，
 *   POST /queue/tasks 入队，GET/PUT /queue/config），状态同步进 queueView 响应式镜像，
 *   QueueBoard/QueueBadge 照常渲染。
 * 阶段机契约（两种模式一致）：transfer→waitqms→qms→waitstrm→strm→done，完成保留 30 分钟。
 * ===================================================================== */

const CFG_DEF: QueueCfg = { threads: 1, gap: 5, qms: 10, strm: 10 }
const KEEP_DONE = 60 * 60 * 1000 // 完成任务保留 1 小时，之后出队——历史去「转存记录」查

type Listener = (s: QueueState) => void
const listeners: Listener[] = []
const cfgListeners: Listener[] = []

/** 渲染用响应式镜像 */
export const queueView = reactive<QueueState>({ seq: 0, lastTick: 0, lastDone: 0, tasks: [] })

/**
 * 该队列项是否来自「自动转存」（定时任务）。
 *
 * 前台分区约定（2026-10-04 用户要求）：自动转存与搜索转存共用同一套后端队列
 * （串行 / 限速门 / 熔断都在引擎里，这是刻意的），但**前台不混着展示**——
 * 「转存队列」浮标与看板只列手动（搜索转存）任务；自动转存的执行看
 * 「自动转存 → 转存历史」页和任务行的「转存日志」，以及点击执行时的执行监控弹窗。
 *
 * 判据用 paTaskId：后端 enqueue 会写 source，但 restore（重启恢复）重建任务时
 * 不带 source，只带 pa_task_id——所以 paTaskId 才是跨重启稳定的标识。
 */
export function isAutoQueued(t: QueueTask): boolean {
  return t.paTaskId != null || t.source === 'auto'
}
/** 队列配置响应式镜像 */
export const cfgView = reactive<QueueCfg>({ ...CFG_DEF })

/** 新任务开跑钩子（QueueBoard 用它清空日志展开钉选） */
let onNewTaskStart: () => void = () => {}
export function setOnNewTaskStart(fn: () => void) {
  onNewTaskStart = fn
}

function fireListeners() {
  for (const fn of listeners) {
    try {
      fn(queueView)
    } catch {
      /* ignore */
    }
  }
}

/* =================================================================== */
/* mock 模式：本地 tick 引擎（原 queue-core.js 逻辑 1:1）                 */
/* =================================================================== */

const LOCAL_KEY = 'pkq_v2'
const LOCAL_CFG_KEY = 'pkq_cfg'
const TICK = 600
let pinnedLocalSeq = 0

function loadLocal(): QueueState | null {
  try {
    const raw = localStorage.getItem(LOCAL_KEY)
    return raw ? (JSON.parse(raw) as QueueState) : null
  } catch {
    return null
  }
}

function seed(s: QueueState) {
  s.tasks = [
    {
      id: ++s.seq, name: '庆余年.第二季.4K.HDR.国粤双语.简繁特效字幕', type: 'baidu', path: '/影视/国产剧',
      files: 36, size: '82.4 GB', status: 'run', phase: 'transfer', phaseStart: 0, progress: 68,
      flags: { start: 1, half: 1 }, doneAt: 0,
      logs: [
        { lv: 'STEP', txt: '解析分享链接完成，share_id=1xxxxxx' },
        { lv: 'INFO', txt: '获取分享内文件清单，共 36 项' },
        { lv: 'INFO', txt: '开始转存：庆余年.S02E01.2160p.HDR.mkv' },
        { lv: 'STEP', txt: '正在转存 24/36 …' },
      ],
    },
    {
      id: ++s.seq, name: '庆余年第二季.1080P.腾讯版.全36集', type: 'quark', path: '/媒体/剧集',
      files: 36, size: '46.1 GB', status: 'wait', phase: '', phaseStart: 0, progress: 0, flags: {}, doneAt: 0, logs: [],
    },
  ]
}

function syncView(s: QueueState) {
  queueView.seq = s.seq
  queueView.lastTick = s.lastTick
  queueView.lastDone = s.lastDone
  queueView.tasks.splice(0, queueView.tasks.length, ...s.tasks)
}

function saveLocal(s: QueueState) {
  try {
    localStorage.setItem(LOCAL_KEY, JSON.stringify(s))
  } catch {
    /* ignore */
  }
  syncView(s)
}

function pushLog(t: QueueTask, lv: QueueTask['logs'][number]['lv'], txt: string) {
  t.logs.push({ lv, txt })
  if (t.logs.length > 40) t.logs.shift()
}

function advance(t: QueueTask, cfg: QueueCfg, now: number, s: QueueState) {
  switch (t.phase || 'transfer') {
    case 'transfer':
      if (!t.flags.start) {
        t.flags.start = 1
        pushLog(t, 'INFO', '开始转存：' + t.name.split('.')[0] + ' …')
      }
      t.progress = Math.min(100, t.progress + 6 + Math.round(Math.random() * 7))
      if (t.progress >= 55 && !t.flags.half) {
        t.flags.half = 1
        pushLog(t, 'INFO', '正在转存 ' + Math.max(2, Math.round(t.files / 2)) + '/' + t.files + ' …')
      }
      if (t.progress >= 100) {
        pushLog(t, 'INFO', '转存完成：成功 ' + t.files + ' / 失败 0')
        pushLog(t, 'STEP', '等待 ' + cfg.qms + ' 秒后触发 QMS 刮削')
        t.phase = 'waitqms'
        t.phaseStart = now
      }
      break
    case 'waitqms':
      if (now - t.phaseStart >= cfg.qms * 1000) {
        pushLog(t, 'STEP', '触发 QMS 刮削任务 #' + (t.id + 11))
        t.phase = 'qms'
        t.phaseStart = now
      }
      break
    case 'qms':
      if (now - t.phaseStart >= 1200) {
        pushLog(t, 'INFO', 'QMS 刮削完成，新入库 ' + t.files + ' 条')
        t.phase = 'waitstrm'
        t.phaseStart = now
      }
      break
    case 'waitstrm':
      if (now - t.phaseStart >= cfg.strm * 1000) {
        pushLog(t, 'STEP', '触发 STRM 生成 → 通知 Emby 刷新媒体库')
        t.phase = 'strm'
        t.phaseStart = now
      }
      break
    case 'strm':
      if (now - t.phaseStart >= 800) {
        t.status = 'done'
        t.doneAt = now
        pushLog(t, 'INFO', '任务完成：转存 + QMS + STRM 全链路结束')
        s.lastDone = now
      }
      break
  }
}

function loadCfgRaw(): QueueCfg {
  try {
    const c = JSON.parse(localStorage.getItem(LOCAL_CFG_KEY) || '')
    if (c && c.threads) return { ...CFG_DEF, ...c }
  } catch {
    /* ignore */
  }
  return { ...CFG_DEF }
}

function tick() {
  const s = loadLocal() ?? { seq: 0, tasks: [], lastTick: 0, lastDone: 0 }
  const now = Date.now()
  if (now - (s.lastTick || 0) < TICK - 80) return
  s.lastTick = now
  s.tasks = s.tasks.filter((t) => !(t.status === 'done' && t.doneAt && now - t.doneAt > KEEP_DONE))

  const cfg = loadCfgRaw()
  Object.assign(cfgView, cfg)
  let running = 0
  for (const t of s.tasks) {
    if (t.status !== 'run') continue
    running++
    advance(t, cfg, now, s)
  }
  if (running < cfg.threads && now - (s.lastDone || 0) >= cfg.gap * 1000) {
    for (const t of s.tasks) {
      if (t.status === 'wait') {
        t.status = 'run'
        t.phase = 'transfer'
        t.phaseStart = now
        pushLog(t, 'STEP', '轮到它了，开始转存')
        onNewTaskStart()
        break
      }
    }
  }
  saveLocal(s)
  fireListeners()
}

/* =================================================================== */
/* 真实模式：消费后端                                                    */
/* =================================================================== */

let lastStateJson = ''
let lastRunIdSeen = 0

function applyRemoteState(s: QueueState) {
  const json = JSON.stringify(s)
  if (json === lastStateJson) return
  lastStateJson = json
  // 检测新任务开跑（前端日志单选焦点切换用）
  for (const t of s.tasks) {
    if (t.status === 'run' && t.id > lastRunIdSeen) {
      if (lastRunIdSeen > 0) onNewTaskStart()
      lastRunIdSeen = t.id
    }
  }
  syncView(s)
  fireListeners()
}

async function pollRemote() {
  try {
    applyRemoteState(await get<QueueState>('/queue/state'))
  } catch {
    /* 后端暂不可达：保留上一帧状态，下拍再试 */
  }
}

/* =================================================================== */
/* 对外 API（双模式统一）                                               */
/* =================================================================== */

function stateMock(): QueueState {
  const s = loadLocal()
  if (!s) {
    const fresh = { seq: 0, tasks: [], lastTick: 0, lastDone: 0 }
    seed(fresh)
    saveLocal(fresh)
    return fresh
  }
  return s
}

export const pkQueue = {
  state: (): QueueState => (USE_MOCK ? stateMock() : queueView),
  onChange(fn: Listener) {
    listeners.push(fn)
  },
  /** 入队，返回排队位次（第几位）。真实转存必须带 share_url/share_code。
   *  acc_id：指定转存账号（来自转存配置条目的 account）；空=该类型默认账号。
   *  file_paths：勾选清单（分享内相对路径）；空=全部。 */
  enqueue(item: {
    name?: string
    type?: string
    path?: string
    files?: number
    size?: string
    share_url?: string
    share_code?: string
    include_subdirs?: boolean
    acc_id?: number | null
    file_paths?: string[]
    /* 「建壳转存」（快速转存弹窗）：按资源名/更名值新建文件夹，分享内容剥壳转入 */
    rename?: string
    with_shell?: boolean
    /* 显式 QMS 联动目标（普通转存弹窗下拉）：空 = 按目标目录前缀匹配转存配置。
       STRM 不再单独指定——后端与 QMS 自动配对（同一条转存配置的 strm_id） */
    qms_id?: number | null
    /* 明确关闭联动（开关关掉）：连目录前缀匹配都不做 */
    media_off?: boolean
  }): number {
    if (USE_MOCK) {
      const s = stateMock()
      const t: QueueTask = {
        id: ++s.seq,
        name: item.name || '未命名资源',
        type: (item.type as QueueTask['type']) || 'quark',
        path: item.path || '/',
        files: item.files || 12,
        size: item.size || '—',
        status: 'wait',
        phase: '',
        phaseStart: 0,
        progress: 0,
        flags: {},
        doneAt: 0,
        logs: [],
      }
      s.tasks.push(t)
      saveLocal(s)
      pinnedLocalSeq = 0
      onNewTaskStart()
      const pos = s.tasks.filter((x) => x.status === 'wait' || x.status === 'run').length
      fireListeners()
      return pos
    }
    // 真实模式：同步入队体验（后端立即返回位次，轮询负责把任务带回来）
    const seqAtCall = queueView.seq
    post<{ pos: number }>('/queue/tasks', item)
      .then(() => pollRemote())
      .catch(() => {})
    void seqAtCall
    // 位次以本地当前 wait+run 数 +1 估算，下一拍轮询会带回权威值
    return queueView.tasks.filter((x) => x.status === 'wait' || x.status === 'run').length + 1
  },
}

export function pkQueueCfgGet(): QueueCfg {
  if (USE_MOCK) {
    const c = loadCfgRaw()
    Object.assign(cfgView, c)
    return c
  }
  get<QueueCfg>('/queue/config')
    .then((c) => {
      Object.assign(cfgView, c)
    })
    .catch(() => {})
  return { ...cfgView }
}

export function pkQueueCfgSet(c: QueueCfg) {
  Object.assign(cfgView, c)
  if (USE_MOCK) {
    try {
      localStorage.setItem(LOCAL_CFG_KEY, JSON.stringify(c))
    } catch {
      /* ignore */
    }
  } else {
    put<QueueCfg>('/queue/config', c)
      .then((saved) => Object.assign(cfgView, saved))
      .catch(() => {})
  }
  for (const fn of cfgListeners) {
    try {
      fn(queueView)
    } catch {
      /* ignore */
    }
  }
}

export function onCfgChange(fn: Listener) {
  cfgListeners.push(fn)
}

// ---- 启动 ----
if (USE_MOCK) {
  syncView(stateMock())
  Object.assign(cfgView, loadCfgRaw())
  setInterval(tick, TICK)
} else {
  pkQueueCfgGet()
  // SSE 事件流为主：状态变化才推（连接挂着，零轮询、零磁盘 IO）；
  // 断线自动重连（指数退避），并保留低频轮询作兜底（SSE 静默丢包时数据仍能自愈）
  let pollTimer = 0
  const startPolling = (ms: number) => {
    window.clearInterval(pollTimer)
    pollTimer = window.setInterval(() => void pollRemote(), ms)
  }
  // 兜底轮询只在「SSE 静默」时才该发生：收到任何服务端动静（状态帧 / 心跳）都把定时器往后推。
  // 间隔必须大于后端心跳周期（~30s），否则心跳还没到、定时器先触发了，白白多打一次 /queue/state。
  const SSE_IDLE_POLL = 45000
  const connectSse = () => {
    const es = new EventSource(`/api/queue/events?token=${encodeURIComponent(localStorage.getItem('pk-auth') || '')}`)
    es.onmessage = (ev) => {
      try {
        applyRemoteState(JSON.parse(ev.data) as QueueState)
        startPolling(SSE_IDLE_POLL)
      } catch {
        /* ignore */
      }
    }
    // 后端 ~30s 一个具名心跳事件：连接还活着的证据 → 重置兜底定时器（空闲时几乎不再轮询）
    es.addEventListener('ping', () => startPolling(SSE_IDLE_POLL))
    es.onerror = () => {
      es.close()
      startPolling(2500) // 断线：退回 2.5s 轮询自愈
      window.setTimeout(connectSse, 10000) // 10s 后再试 SSE
    }
  }
  void pollRemote().then(connectSse)
}
