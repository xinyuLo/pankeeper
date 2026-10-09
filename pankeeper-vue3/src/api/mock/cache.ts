import { reactive } from 'vue';
import type { MainDriveType } from '@/types/model';
export type CacheAct = 'ladder' | 'off' | 'compress';
export interface CacheCfg {
    master: boolean;
    persist: boolean;
    ttl: number;
    ttlUnit: '分钟' | '小时';
    auto: boolean;
    memHigh: 75 | 85 | 95;
    act: CacheAct;
    maxSizeMb: number;
}
export interface CacheTree {
    id: number;
    type: MainDriveType;
    acc: string;
    acc_name?: string;
    path: string;
    entries: number;
    size: string;
    ttlMin: number;
    cachedAt?: number;
}
export interface MemUsage {
    pct: number;
    usedMb: number;
    totalMb: number;
}
export const cacheStore = reactive<{
    cfg: CacheCfg;
    trees: CacheTree[];
    mem: MemUsage;
    seq: number;
}>({
    cfg: {
        master: true,
        persist: true,
        auto: true,
        ttl: 30,
        ttlUnit: '小时',
        memHigh: 85,
        act: 'ladder',
        maxSizeMb: 100,
    },
    trees: [
        { id: 1, type: 'baidu', acc: '主账号 138****6688', path: '/影视', entries: 156, size: '42 KB', ttlMin: 96 },
        { id: 2, type: 'baidu', acc: '主账号 138****6688', path: '/影视/国产剧', entries: 87, size: '25 KB', ttlMin: 210 },
        { id: 3, type: 'baidu', acc: '小号 xinyu_bd', path: '/网盘备份', entries: 23, size: '6 KB', ttlMin: -4 },
        { id: 4, type: 'quark', acc: '主账号 185****2233', path: '/媒体/剧集', entries: 87, size: '25 KB', ttlMin: 410 },
        { id: 5, type: 'quark', acc: '主账号 185****2233', path: '/媒体/电影', entries: 41, size: '12 KB', ttlMin: -38 },
        { id: 6, type: '115', acc: '主账号 xinyu115', path: '/影视/电影', entries: 64, size: '18 KB', ttlMin: 12 },
    ],
    mem: { pct: 62, usedMb: 62, totalMb: 100 },
    seq: 100,
});
export function cacheIsStale(t: CacheTree): boolean {
    return t.ttlMin < 0;
}
export function cacheFullTtl(cfg: CacheCfg): number {
    return cfg.ttl * (cfg.ttlUnit === '小时' ? 60 : 1);
}
