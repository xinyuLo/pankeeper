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
}

/** 启动某网盘的全树预热（后台执行立即返回；已在跑则回当前进度） */
export function warmTrees(type: string): Promise<WarmStatus> {
  if (USE_MOCK) return mockDelay({ status: 'done', done: 0, total: 0 })
  return post<WarmStatus>('/cache/trees/warm', { type })
}

/** 查询预热进度 */
export function warmStatus(type: string): Promise<WarmStatus> {
  if (USE_MOCK) return mockDelay({ status: 'done', done: 0, total: 0 })
  return get<WarmStatus>('/cache/trees/warm/status', { params: { type } })
}
