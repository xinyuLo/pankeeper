import { reactive } from 'vue'
import type { QueueCfg, QueueState, QueueTask } from '@/types/model'

/* =====================================================================
 * 转存队列引擎 —— 原型 parts/queue-core.js 的 TS 移植，逻辑 1:1：
 * - 所有转存动作只做一件事：入队（enqueue）
 * - 后台串行慢跑：transfer → waitqms → qms → waitstrm → strm → done（全链占线程位）
 * - 状态存 localStorage `pkq_v2` 跨页面共享；lastTick 心跳防多页双倍速
 * - 节奏参数读 `pkq_cfg`（队列配置页维护），改完即时生效
 * - 已完成任务保留 30 分钟后自动出队
 * localStorage 是唯一真源；queueView / cfgView 是给 Vue 渲染用的响应式镜像。
 * 后端就绪后：整块换成轮询/推送后端队列接口，enqueue 改为 POST。
 * ===================================================================== */

const KEY = 'pkq_v2'
const CFG_KEY = 'pkq_cfg'
const TICK = 600 // 模拟节拍
const KEEP_DONE = 30 * 60 * 1000 // 已完成任务保留时长
const CFG_DEF: QueueCfg = { threads: 1, gap: 5, qms: 10, strm: 10 }

type Listener = (s: QueueState) => void
const listeners: Listener[] = []
const cfgListeners: Listener[] = []

/** 渲染用响应式镜像（每拍同步） */
export const queueView = reactive<QueueState>({ seq: 0, lastTick: 0, lastDone: 0, tasks: [] })
/** 队列配置响应式镜像 */
export const cfgView = reactive<QueueCfg>({ ...CFG_DEF })

function load(): QueueState | null {
  try {
    const raw = localStorage.getItem(KEY)
    return raw ? (JSON.parse(raw) as QueueState) : null
  } catch {
    return null
  }
}

function syncView(s: QueueState) {
  queueView.seq = s.seq
  queueView.lastTick = s.lastTick
  queueView.lastDone = s.lastDone
  queueView.tasks.splice(0, queueView.tasks.length, ...s.tasks)
}

function save(s: QueueState) {
  try {
    localStorage.setItem(KEY, JSON.stringify(s))
  } catch {
    /* ignore */
  }
  syncView(s)
}

// 首次使用先放两条演示任务，让「队列」分段开箱就有东西看（一条转存中、一条排队）
function seed(s: QueueState) {
  s.tasks = [
    {
      id: ++s.seq,
      name: '庆余年.第二季.4K.HDR.国粤双语.简繁特效字幕',
      type: 'baidu',
      path: '/影视/国产剧',
      files: 36,
      size: '82.4 GB',
      status: 'run',
      phase: 'transfer',
      phaseStart: 0,
      progress: 68,
      flags: { start: 1, half: 1 },
      doneAt: 0,
      logs: [
        { lv: 'STEP', txt: '解析分享链接完成，share_id=1xxxxxx' },
        { lv: 'INFO', txt: '获取分享内文件清单，共 36 项' },
        { lv: 'INFO', txt: '开始转存：庆余年.S02E01.2160p.HDR.mkv' },
        { lv: 'STEP', txt: '正在转存 24/36 …' },
      ],
    },
    {
      id: ++s.seq,
      name: '庆余年第二季.1080P.腾讯版.全36集',
      type: 'quark',
      path: '/媒体/剧集',
      files: 36,
      size: '46.1 GB',
      status: 'wait',
      phase: '',
      phaseStart: 0,
      progress: 0,
      flags: {},
      doneAt: 0,
      logs: [],
    },
  ]
}

export function state(): QueueState {
  let s = load()
  if (!s) {
    s = { seq: 0, tasks: [], lastTick: 0, lastDone: 0 }
    seed(s)
    save(s)
  }
  return s
}

function pushLog(t: QueueTask, lv: QueueTask['logs'][number]['lv'], txt: string) {
  t.logs.push({ lv, txt })
  if (t.logs.length > 40) t.logs.shift() // 只留最近 40 行
}

// 已完成超过半小时的任务自动出队（历史去「全部记录」查）
function prune(s: QueueState, now: number): boolean {
  const before = s.tasks.length
  s.tasks = s.tasks.filter((t) => !(t.status === 'done' && t.doneAt && now - t.doneAt > KEEP_DONE))
  return s.tasks.length !== before
}

/** 单任务阶段推进：transfer → waitqms → qms → waitstrm → strm → done */
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
    const c = JSON.parse(localStorage.getItem(CFG_KEY) || '')
    if (c && c.threads) return { ...CFG_DEF, ...c }
  } catch {
    /* ignore */
  }
  return { ...CFG_DEF }
}

function tick() {
  const s = state()
  const now = Date.now()
  if (now - (s.lastTick || 0) < TICK - 80) return // 心跳：多页同开只让一个推进
  s.lastTick = now
  prune(s, now)

  const cfg = loadCfgRaw()
  Object.assign(cfgView, cfg)
  let running = 0
  for (const t of s.tasks) {
    if (t.status !== 'run') continue
    running++
    advance(t, cfg, now, s)
  }
  // 有空闲线程且距上个任务完成已过「转存触发间隔」→ 提一个排队的上场
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
  save(s)
  listeners.forEach((fn) => {
    try {
      fn(s)
    } catch {
      /* ignore */
    }
  })
}

/** 新任务开跑时的钩子（队列看板用它清空日志展开钉选） */
let onNewTaskStart: () => void = () => {}
export function setOnNewTaskStart(fn: () => void) {
  onNewTaskStart = fn
}

setInterval(tick, TICK)

export const pkQueue = {
  state,
  /** 入队，返回排队位次（第几位） */
  enqueue(item: { name?: string; type?: string; path?: string; files?: number; size?: string }): number {
    const s = state()
    const t: QueueTask = {
      id: ++s.seq,
      name: item.name || '未命名资源',
      type: (item.type as QueueTask['type']) || 'baidu',
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
    save(s)
    const pos = s.tasks.filter((x) => x.status === 'wait' || x.status === 'run').length
    listeners.forEach((fn) => {
      try {
        fn(s)
      } catch {
        /* ignore */
      }
    })
    return pos
  },
  onChange(fn: Listener) {
    listeners.push(fn)
  },
}

export function pkQueueCfgGet(): QueueCfg {
  const c = loadCfgRaw()
  Object.assign(cfgView, c)
  return c
}

export function pkQueueCfgSet(c: QueueCfg) {
  try {
    localStorage.setItem(CFG_KEY, JSON.stringify(c))
  } catch {
    /* ignore */
  }
  Object.assign(cfgView, c)
  cfgListeners.forEach((fn) => {
    try {
      fn(state())
    } catch {
      /* ignore */
    }
  })
}

export function onCfgChange(fn: Listener) {
  cfgListeners.push(fn)
}

// 初始化：载入现有状态 + 配置到响应式镜像
syncView(state())
Object.assign(cfgView, loadCfgRaw())
