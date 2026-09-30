import { reactive } from 'vue'
import type { MainDriveType } from '@/types/model'

/* =====================================================================
 * 缓存配置 mock（cache-config 页，cc- 前缀）—— 原型 page-cache.html 数据原样移植并扩到 6 条。
 * 内存态 reactive：刷新/清除直接改数组，会话内有效、刷新页面重置，与原型一致。
 * 页面私有类型放这里（只有缓存配置页用，不进 types/model.ts）。
 * ===================================================================== */

/** 降级动作三档：逐级降级 / 直接关闭缓存 / 只压缩条目 */
export type CacheAct = 'ladder' | 'off' | 'compress'

/** ===== 缓存策略 + 内存保护配置 ===== */
export interface CacheCfg {
  /** 目录树缓存总开关 */
  master: boolean
  /** 缓存持久化：条目写穿 SQLite，重启不丢、恢复零网盘请求 */
  persist: boolean
  /** 缓存失效时间（数值与单位分开存，默认 30 小时） */
  ttl: number
  ttlUnit: '分钟' | '小时'
  /** 自动刷新（过期前后台预取下一版） */
  auto: boolean
  /** 内存水位阈值（超过开始降级） */
  memHigh: 75 | 85 | 95
  act: CacheAct
  /** 缓存大小上限（MB），超出按 LRU 淘汰 */
  maxSizeMb: number
}

/** ===== 已缓存目录树一行 ===== */
export interface CacheTree {
  id: number
  type: MainDriveType
  /** 缓存键里的账号占位（main / 账号 id），内部用 */
  acc: string
  /** 账号显示名（别名/昵称，后端解析好的），列表展示用 */
  acc_name?: string
  path: string
  entries: number
  size: string
  /** 距过期的分钟数：>=0 缓存中（fresh），<0 已过期（stale） */
  ttlMin: number
  /** 缓存写入时间（秒级时间戳；旧后端可能不带） */
  cachedAt?: number
}

/** 内存水位（mock 固定值：62% · 4.9 GB / 8 GB，与原型一致） */
export interface MemUsage {
  pct: number
  /** 已用 / 上限（MB） */
  usedMb: number
  totalMb: number
}

export const cacheStore = reactive<{
  cfg: CacheCfg
  trees: CacheTree[]
  mem: MemUsage
  seq: number
}>({
  cfg: {
    master: true,
    persist: true,
    auto: true,
    ttl: 30,
    ttlUnit: '小时',
    memHigh: 85,
    act: 'ladder',
    maxSizeMb: 800,
  },
  trees: [
    { id: 1, type: 'baidu', acc: '主账号 138****6688', path: '/影视', entries: 156, size: '42 KB', ttlMin: 96 },
    { id: 2, type: 'baidu', acc: '主账号 138****6688', path: '/影视/国产剧', entries: 87, size: '25 KB', ttlMin: 210 },
    { id: 3, type: 'baidu', acc: '小号 xinyu_bd', path: '/网盘备份', entries: 23, size: '6 KB', ttlMin: -4 },
    { id: 4, type: 'quark', acc: '主账号 185****2233', path: '/媒体/剧集', entries: 87, size: '25 KB', ttlMin: 410 },
    { id: 5, type: 'quark', acc: '主账号 185****2233', path: '/媒体/电影', entries: 41, size: '12 KB', ttlMin: -38 },
    { id: 6, type: '115', acc: '主账号 xinyu115', path: '/影视/电影', entries: 64, size: '18 KB', ttlMin: 12 },
  ],
  mem: { pct: 62, usedMb: 490, totalMb: 800 },
  seq: 100,
})

/** 过期判定（与表格状态列共用一条规则） */
export function cacheIsStale(t: CacheTree): boolean {
  return t.ttlMin < 0
}

/** 按当前配置算一棵树的满血 TTL（分钟） */
export function cacheFullTtl(cfg: CacheCfg): number {
  return cfg.ttl * (cfg.ttlUnit === '小时' ? 60 : 1)
}
