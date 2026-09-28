/**
 * 缓存配置领域 API —— mock 实现（目录树缓存：策略 + 内存保护 + 已缓存目录表）。
 * 后端就绪后：每个函数把 mockDelay(...) 换成 http 调用（端点写在 TODO 注释里）。
 */
import { mockDelay } from '../http'
import { cacheStore, cacheFullTtl, cacheIsStale } from '../mock/cache'
import type { CacheCfg, CacheTree, MemUsage } from '../mock/cache'

/** 读取缓存策略 + 内存水位 */
export function getCacheConfig(): Promise<{ cfg: CacheCfg; mem: MemUsage }> {
  // TODO 后端: GET /api/cache/config
  return mockDelay({ cfg: { ...cacheStore.cfg }, mem: { ...cacheStore.mem } })
}

/** 保存缓存策略（策略项改完即生效，原型如此） */
export function saveCacheConfig(cfg: CacheCfg): Promise<void> {
  // TODO 后端: PUT /api/cache/config
  Object.assign(cacheStore.cfg, cfg)
  return mockDelay(undefined, 60)
}

/** 已缓存目录树列表 */
export function listCacheTrees(): Promise<CacheTree[]> {
  // TODO 后端: GET /api/cache/trees
  return mockDelay(cacheStore.trees.map((t) => ({ ...t })))
}

/** 刷新一棵目录树：重置为满血 TTL（真实系统为重拉网盘目录后回填） */
export function refreshCacheTree(id: number): Promise<void> {
  // TODO 后端: POST /api/cache/trees/:id/refresh
  const it = cacheStore.trees.find((x) => x.id === id)
  if (it) it.ttlMin = cacheFullTtl(cacheStore.cfg)
  return mockDelay(undefined, 200)
}

/** 清除一棵目录树缓存 */
export function clearCacheTree(id: number): Promise<void> {
  // TODO 后端: DELETE /api/cache/trees/:id
  cacheStore.trees = cacheStore.trees.filter((x) => x.id !== id)
  return mockDelay(undefined)
}

/** 立即刷新全部：所有树重置为满血 TTL */
export function refreshAllCacheTrees(): Promise<number> {
  // TODO 后端: POST /api/cache/trees/refresh-all
  const ttl = cacheFullTtl(cacheStore.cfg)
  cacheStore.trees.forEach((t) => {
    t.ttlMin = ttl
  })
  return mockDelay(cacheStore.trees.length, 300)
}

/** 清空缓存 */
export function clearAllCacheTrees(): Promise<void> {
  // TODO 后端: DELETE /api/cache/trees
  cacheStore.trees = []
  return mockDelay(undefined)
}

/** 供页面直接复用的过期判定（避免页面各写一份规则） */
export const isStaleTree = cacheIsStale
