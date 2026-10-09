import { get, mockDelay, post, USE_MOCK } from '../http';
import { searchStore, searchStoreChannels, PANSOU_ADDR, type SearchChannel } from '../mock/search';
import type { SearchResultItem } from '@/types/model';
import type { ShareFilesMeta } from './tasks';
export type { SearchChannel };
function mockRows(): SearchResultItem[] {
    return searchStore.results.map((r, i) => ({
        ...r,
        url: `https://pan.${r.t === 'ali' ? 'alipan' : r.t}.example.com/s/mock${i}`,
        share_code: i % 3 === 0 ? 'ab12' : '',
        source: 'mock',
    }));
}
export function getSearchResults(keyword: string): Promise<SearchResultItem[]> {
    if (USE_MOCK) {
        const dur = 900 + Math.round(Math.random() * 500);
        return mockDelay(mockRows(), dur);
    }
    return get<SearchResultItem[]>('/search/results', { params: { kw: keyword } });
}
export interface LinkCheckResult {
    state: 'ok' | 'bad' | 'locked' | 'uncertain' | 'unknown';
    summary: string;
}
export function checkShareLink(type: string, url: string, shareCode = ''): Promise<LinkCheckResult> {
    if (USE_MOCK)
        return mockDelay({ state: 'ok', summary: '（mock）链接有效' });
    return post<LinkCheckResult>('/search/check-link', { type, url, share_code: shareCode });
}
export function getInitialResults(): Promise<SearchResultItem[]> {
    if (USE_MOCK)
        return mockDelay(mockRows());
    return Promise.resolve([]);
}
export function getSearchChannels(): Promise<SearchChannel[]> {
    if (USE_MOCK)
        return mockDelay(searchStoreChannels);
    return get<{
        name: string;
        on: boolean;
    }[]>('/search/channels').then((list) => list.map((x) => ({ name: x.name, on: x.on })));
}
export interface EngineHealth {
    ok: boolean;
    plugins: number | null;
    channels: number | null;
    ms?: number;
    message?: string;
}
export function getEngineHealth(): Promise<EngineHealth> {
    if (USE_MOCK)
        return mockDelay({ ok: true, plugins: 76, channels: 90 });
    return get<EngineHealth>('/search/health');
}
export function getRecentKeywords(limit = 5): Promise<string[]> {
    if (USE_MOCK)
        return mockDelay(['狂飙', '哪吒2']);
    return get<string[]>('/search/recent-keywords', { params: { limit } });
}
export function getEngineHealthCached(): Promise<{
    ok: boolean | null;
    checked_at: string;
}> {
    if (USE_MOCK)
        return mockDelay({ ok: true, checked_at: '' });
    return get<{
        ok: boolean | null;
        checked_at: string;
    }>('/search/health-cached');
}
export function getPanSouAddr(): Promise<string> {
    if (USE_MOCK)
        return mockDelay(PANSOU_ADDR);
    return get<string>('/search/pansou-addr');
}
export function getSearchShareFiles(type: string, url: string, code = '', refresh = false): Promise<ShareFilesMeta> {
    if (USE_MOCK)
        return Promise.resolve({ total: 0, tree: [], files: [], cached_at: 0, fresh: false });
    return get<ShareFilesMeta>('/search/share-files', { params: { type, url, code, refresh } });
}
