import { del, get, mockDelay, post, put, USE_MOCK } from '../http';
import { settingsStore, type SettingsData, type SessionDays } from '../mock/settings';
export function getSettings(): Promise<SettingsData> {
    if (USE_MOCK) {
        return mockDelay<SettingsData>({
            search: { ...settingsStore.search },
            notify: { ...settingsStore.notify },
            qms: { ...settingsStore.qms },
            litepan: { ...settingsStore.litepan },
            security: { ...settingsStore.security },
            media: { ...settingsStore.media },
        });
    }
    return get<SettingsData>('/settings');
}
export async function saveSearchSrc(cfg: SettingsData['search']): Promise<void> {
    if (USE_MOCK) {
        Object.assign(settingsStore.search, cfg);
        return mockDelay(undefined);
    }
    await put('/settings/search', cfg);
}
export async function saveNotify(cfg: SettingsData['notify']): Promise<void> {
    if (USE_MOCK) {
        Object.assign(settingsStore.notify, cfg);
        return mockDelay(undefined);
    }
    await put('/settings/notify', cfg);
}
export async function saveQms(cfg: SettingsData['qms']): Promise<void> {
    if (USE_MOCK) {
        Object.assign(settingsStore.qms, cfg);
        return mockDelay(undefined);
    }
    await put('/settings/qms', cfg);
}
export async function saveMediaBackend(backend: 'qms' | 'litepan'): Promise<void> {
    if (USE_MOCK) {
        settingsStore.media.backend = backend;
        return mockDelay(undefined);
    }
    await put('/settings/media', { backend });
}
export async function saveLitePan(cfg: SettingsData['litepan']): Promise<void> {
    if (USE_MOCK) {
        Object.assign(settingsStore.litepan, cfg);
        return mockDelay(undefined);
    }
    await put('/settings/litepan', cfg);
}
export async function testLitePan(url: string, apikey: string): Promise<{
    ok: boolean;
    message?: string;
}> {
    if (USE_MOCK) {
        void url;
        void apikey;
        return mockDelay({ ok: true, message: '（mock）连通正常' }, 300);
    }
    return post<{
        ok: boolean;
        message?: string;
    }>('/settings/litepan/test', { webhook_url: url, apikey });
}
export async function testPansou(url: string): Promise<{
    ok: boolean;
    ms: number;
    message?: string;
}> {
    if (USE_MOCK) {
        void url;
        return mockDelay({ ok: true, ms: 120 + Math.round(Math.random() * 40) });
    }
    return post<{
        ok: boolean;
        ms: number;
    }>('/settings/search/test', { url });
}
export async function testSendkey(sendkey: string): Promise<{
    ok: boolean;
    message?: string;
}> {
    if (USE_MOCK) {
        void sendkey;
        return mockDelay({ ok: true, message: '（mock）测试消息已发送' }, 400);
    }
    return post<{
        ok: boolean;
        message?: string;
    }>('/settings/notify/test', { sendkey });
}
export async function testQms(url: string, apikey: string): Promise<{
    ok: boolean;
    message?: string;
}> {
    if (USE_MOCK) {
        void url;
        void apikey;
        return mockDelay({ ok: true, message: 'QMS 连接正常' }, 300);
    }
    return post<{
        ok: boolean;
        message?: string;
    }>('/settings/qms/test', { url, apikey });
}
export async function testTmdb(cfg: {
    mode: string;
    proxy: string;
    hosts: {
        ip: string;
        host: string;
    }[];
    skip_tls: boolean;
    api_key: string;
}): Promise<{
    ok: boolean;
    ms?: number;
    winner?: string;
    message?: string;
    results?: {
        target: string;
        desc: string;
        ok: boolean;
        ms?: number;
        error?: string;
    }[];
}> {
    if (USE_MOCK) {
        void cfg;
        return mockDelay({ ok: true, ms: 220, message: '（mock）TMDB 连通正常' }, 300);
    }
    return post<{
        ok: boolean;
        ms?: number;
        winner?: string;
        message?: string;
        results?: {
            target: string;
            desc: string;
            ok: boolean;
            ms?: number;
            error?: string;
        }[];
    }>('/settings/tmdb/test', cfg);
}
export function getQmsHealth(): Promise<{
    ok: boolean;
    message?: string;
}> {
    if (USE_MOCK)
        return mockDelay({ ok: true, message: '在线' }, 200);
    return get<{
        ok: boolean;
        message?: string;
    }>('/qms/health');
}
export function getLitePanHealth(): Promise<{
    ok: boolean;
    message?: string;
}> {
    if (USE_MOCK)
        return mockDelay({ ok: true, message: '在线' }, 200);
    return get<{
        ok: boolean;
        message?: string;
    }>('/litepan/health');
}
export interface PushLogRow {
    id: number;
    ts: string;
    title: string;
    kind: string;
    status: 'success' | 'fail';
    error: string;
    content: string;
    has_more: boolean;
}
export function getPushLogs(limit = 100): Promise<{
    items: PushLogRow[];
    delivered: number;
    failed: number;
}> {
    if (USE_MOCK)
        return mockDelay({ items: [], delivered: 0, failed: 0 });
    return get<{
        items: PushLogRow[];
        delivered: number;
        failed: number;
    }>('/notify/history', { params: { limit } });
}
export interface PushLogDetail extends Omit<PushLogRow, 'has_more'> {
    content: string;
}
export function getPushLogDetail(id: number): Promise<PushLogDetail> {
    if (USE_MOCK)
        return mockDelay({ id, ts: '', title: '（mock）', kind: 'info', status: 'success', error: '', content: 'mock 正文' });
    return get<PushLogDetail>(`/notify/history/${id}`);
}
export async function saveSecurity(payload: {
    username: string;
    old_password: string;
    new_password: string;
    session_days: SessionDays;
}): Promise<void> {
    if (USE_MOCK) {
        settingsStore.security.username = payload.username;
        settingsStore.security.session_days = payload.session_days;
        return mockDelay(undefined, 300);
    }
    await put('/settings/security', {
        username: payload.username,
        old_password: payload.old_password,
        new_password: payload.new_password,
        session_days: payload.session_days,
    });
}
export async function clearPushLogs(before: string): Promise<number> {
    if (USE_MOCK)
        return mockDelay(0);
    const { count } = await del<{
        count: number;
    }>(`/notify/history?before=${encodeURIComponent(before)}`);
    return count;
}
