/**
 * 缓存配置领域 API —— 双模式（目录树缓存：策略 + 内存保护 + 已缓存目录表）。
 * 真实模式后端按目录粒度缓存，id 是 "type/account/cid" 形式的键（mock 是数字），两者都透传。
 */
import { del, get, mockDelay, post, put, USE_MOCK } from '../http'
import { cacheStore, cacheFullTtl, cacheIsStale } from '../mock/cache'
import type { CacheCfg, CacheTree, MemUsage } from '../mock/cache'

/** 读取缓存策略 + 内存水位 */
export function getCacheConfig(): Promise<{ cfg: CacheCfg; mem: MemUsage }> {
  if (USE_MOCK) return mockDelay({ cfg: { ...cacheStore.cfg }, mem: { ...cacheStore.mem } })
  return get<{ cfg: CacheCfg; mem: MemUsage }>('/cache/config')
}

/** 保存缓存策略（策略项改完即生效） */
export async function saveCacheConfig(cfg: CacheCfg): Promise<void> {
  if (USE_MOCK) {
    Object.assign(cacheStore.cfg, cfg)
    return mockDelay(undefined, 60)
  }
  await put('/cache/config', cfg)
}

/** 已缓存目录树列表 */
export function listCacheTrees(): Promise<CacheTree[]> {
  if (USE_MOCK) return mockDelay(cacheStore.trees.map((x) => ({ ...x })))
  return get<CacheTree[]>('/cache/trees')
}

/** 刷新一棵目录树缓存（真实 = 失效该 key，下次浏览直连重拉） */
export async function refreshCacheTree(id: number | string): Promise<void> {
  if (USE_MOCK) {
    const it = cacheStore.trees.find((x) => x.id === id)
    if (it) it.ttlMin = cacheFullTtl(cacheStore.cfg)
    return mockDelay(undefined, 200)
  }
  await post(`/cache/trees/${id}/refresh`)
}

/** 清除一棵目录树缓存 */
export async function clearCacheTree(id: number | string): Promise<void> {
  if (USE_MOCK) {
    cacheStore.trees = cacheStore.trees.filter((x) => x.id !== id)
    return mockDelay(undefined)
  }
  await del(`/cache/trees/${id}`)
}

/** 立即刷新全部（真实 = 全部失效），返回受影响的树数 */
export async function refreshAllCacheTrees(): Promise<number> {
  if (USE_MOCK) {
    const ttl = cacheFullTtl(cacheStore.cfg)
    cacheStore.trees.forEach((t) => {
      t.ttlMin = ttl
    })
    return mockDelay(cacheStore.trees.length, 300)
  }
  const { count } = await post<{ count: number }>('/cache/trees/refresh-all')
  return count
}

/** 清空缓存 */
export async function clearAllCacheTrees(): Promise<void> {
  if (USE_MOCK) {
    cacheStore.trees = []
    return mockDelay(undefined)
  }
  await del('/cache/trees')
}

/** 供页面直接复用的过期判定（避免页面各写一份规则） */
export const isStaleTree = cacheIsStale

/* ===== 全树预热：保存 Cookie 验证通过后触发，后台跑，前端轮询进度 ===== */

export interface WarmStatus {
  status: 'idle' | 'queued' | 'running' | 'done' | 'error'
  done: number
  total: number
  message?: string
  /** 后端队列任务附带的展示字段（缓存预热队列化后返回） */
  type?: string
  acc_name?: string
}

/** 启动某网盘的全树预热（后台执行立即返回；同账号已在排队/在跑则回当前进度）。
 *  accId 空 = 该类型默认账号。全局单工队列：所有任务串行跑。 */
export function warmTrees(type: string, accId?: number | null): Promise<WarmStatus> {
  if (USE_MOCK) return mockDelay({ status: 'done', done: 0, total: 0 })
  return post<WarmStatus>('/cache/trees/warm', { type, acc_id: accId ?? null })
}

/** 查询预热进度（accId 口径要和 warmTrees 一致，否则查不到那个任务） */
export function warmStatus(type: string, accId?: number | null): Promise<WarmStatus> {
  if (USE_MOCK) return mockDelay({ status: 'done', done: 0, total: 0 })
  return get<WarmStatus>('/cache/trees/warm/status', { params: { type, acc_id: accId ?? null } })
}

/** ===== 缓存配置页「缓存预热」：全部已连接账号入队，单工队列串行跑 ===== */

export interface WarmAllStatus {
  total_jobs: number
  done_jobs: number
  failed_jobs: number
  queued: number
  current: (WarmStatus & { type: string; acc_name?: string }) | null
  jobs: (WarmStatus & { type: string; acc_name?: string })[]
}

/** 全部已连接账号入队预热（后端队列串行，同网盘多账号绝不并行——防风控） */
export function warmAllAccounts(): Promise<{ count: number }> {
  if (USE_MOCK) return mockDelay({ count: 0 })
  return post<{ count: number }>('/cache/trees/warm-all')
}

/** 预热队列总览（排队数/在跑任务/完成数） */
export function warmAllStatus(): Promise<WarmAllStatus> {
  if (USE_MOCK)
    return mockDelay({ total_jobs: 0, done_jobs: 0, failed_jobs: 0, queued: 0, current: null, jobs: [] })
  return get<WarmAllStatus>('/cache/trees/warm-all/status')
}
