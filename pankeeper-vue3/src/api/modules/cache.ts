import { del, get, mockDelay, post, put, USE_MOCK } from '../http';
import { cacheStore, cacheFullTtl, cacheIsStale } from '../mock/cache';
import type { CacheCfg, CacheTree, MemUsage } from '../mock/cache';
export function getCacheConfig(): Promise<{
    cfg: CacheCfg;
    mem: MemUsage;
}> {
    if (USE_MOCK)
        return mockDelay({ cfg: { ...cacheStore.cfg }, mem: { ...cacheStore.mem } });
    return get<{
        cfg: CacheCfg;
        mem: MemUsage;
    }>('/cache/config');
}
export async function saveCacheConfig(cfg: CacheCfg): Promise<void> {
    if (USE_MOCK) {
        Object.assign(cacheStore.cfg, cfg);
        return mockDelay(undefined, 60);
    }
    await put('/cache/config', cfg);
}
export function listCacheTrees(): Promise<CacheTree[]> {
    if (USE_MOCK)
        return mockDelay(cacheStore.trees.map((x) => ({ ...x })));
    return get<CacheTree[]>('/cache/trees');
}
export async function refreshCacheTree(id: number | string): Promise<void> {
    if (USE_MOCK) {
        const it = cacheStore.trees.find((x) => x.id === id);
        if (it)
            it.ttlMin = cacheFullTtl(cacheStore.cfg);
        return mockDelay(undefined, 200);
    }
    await post(`/cache/trees/${id}/refresh`);
}
export async function clearCacheTree(id: number | string): Promise<void> {
    if (USE_MOCK) {
        cacheStore.trees = cacheStore.trees.filter((x) => x.id !== id);
        return mockDelay(undefined);
    }
    await del(`/cache/trees/${id}`);
}
export async function refreshAllCacheTrees(): Promise<number> {
    if (USE_MOCK) {
        const ttl = cacheFullTtl(cacheStore.cfg);
        cacheStore.trees.forEach((t) => {
            t.ttlMin = ttl;
        });
        return mockDelay(cacheStore.trees.length, 300);
    }
    const { count } = await post<{
        count: number;
    }>('/cache/trees/refresh-all');
    return count;
}
export async function clearAllCacheTrees(): Promise<void> {
    if (USE_MOCK) {
        cacheStore.trees = [];
        return mockDelay(undefined);
    }
    await del('/cache/trees');
}
export const isStaleTree = cacheIsStale;
export interface WarmStatus {
    status: 'idle' | 'queued' | 'running' | 'done' | 'error';
    done: number;
    total: number;
    message?: string;
    type?: string;
    acc_name?: string;
}
export function warmTrees(type: string, accId?: number | null): Promise<WarmStatus> {
    if (USE_MOCK)
        return mockDelay({ status: 'done', done: 0, total: 0 });
    return post<WarmStatus>('/cache/trees/warm', { type, acc_id: accId ?? null });
}
export function warmStatus(type: string, accId?: number | null): Promise<WarmStatus> {
    if (USE_MOCK)
        return mockDelay({ status: 'done', done: 0, total: 0 });
    return get<WarmStatus>('/cache/trees/warm/status', { params: { type, acc_id: accId ?? null } });
}
export interface WarmAllStatus {
    total_jobs: number;
    done_jobs: number;
    failed_jobs: number;
    queued: number;
    current: (WarmStatus & {
        type: string;
        acc_name?: string;
    }) | null;
    jobs: (WarmStatus & {
        type: string;
        acc_name?: string;
    })[];
}
export function warmAllAccounts(): Promise<{
    count: number;
}> {
    if (USE_MOCK)
        return mockDelay({ count: 0 });
    return post<{
        count: number;
    }>('/cache/trees/warm-all');
}
export function warmAllStatus(): Promise<WarmAllStatus> {
    if (USE_MOCK)
        return mockDelay({ total_jobs: 0, done_jobs: 0, failed_jobs: 0, queued: 0, current: null, jobs: [] });
    return get<WarmAllStatus>('/cache/trees/warm-all/status');
}
