import { del, get, mockDelay, post, USE_MOCK } from '../http';
import { recordLogOf, recordsStore } from '../mock/records';
import type { RecordRow } from '../mock/records';
import type { QueueLogLine } from '@/types/model';
import type { ShareFilesMeta } from './tasks';
export function getRecordShareFiles(recordId: number, refresh = false): Promise<ShareFilesMeta> {
    if (USE_MOCK)
        return Promise.resolve({ total: 0, tree: [], files: [], cached_at: 0, fresh: false });
    return get<ShareFilesMeta>(`/records/${recordId}/share-files`, { params: { refresh } });
}
export function listRecords(): Promise<RecordRow[]> {
    if (USE_MOCK)
        return mockDelay(recordsStore.items);
    return get<RecordRow[]>('/records').then((rows) => {
        recordsStore.items.splice(0, recordsStore.items.length, ...rows);
        return rows;
    });
}
export function getRecordLog(r: RecordRow): Promise<QueueLogLine[]> {
    if (USE_MOCK)
        return mockDelay(recordLogOf(r));
    return get<QueueLogLine[]>(`/records/${r.id}/logs`);
}
export async function deleteRecord(id: number): Promise<void> {
    if (USE_MOCK) {
        recordsStore.items = recordsStore.items.filter((x) => x.id !== id);
        return mockDelay(undefined);
    }
    await del(`/records/${id}`);
    recordsStore.items = recordsStore.items.filter((x) => x.id !== id);
}
export async function clearRecords(before: string): Promise<number> {
    if (USE_MOCK) {
        let removed = 0;
        if (!before) {
            removed = recordsStore.items.length;
            recordsStore.items = [];
        }
        else {
            const y = new Date().getFullYear();
            const keep: RecordRow[] = [];
            for (const r of recordsStore.items) {
                const t = new Date(`${y}-${before.replace(' ', 'T')}`).getTime();
                const m = /^(\d{2})-(\d{2}) (\d{2}):(\d{2})$/.exec(r.tm);
                const rt = m ? new Date(y, Number(m[1]) - 1, Number(m[2]), Number(m[3]), Number(m[4])).getTime() : 0;
                if (rt && rt < t)
                    removed++;
                else
                    keep.push(r);
            }
            recordsStore.items = keep;
        }
        return mockDelay(removed);
    }
    const { count } = await del<{
        count: number;
    }>(`/records?before=${encodeURIComponent(before)}`);
    recordsStore.items = recordsStore.items.filter((x) => (before ? x.tm < before : false));
    await listRecords();
    return count;
}
export async function retryFailedItems(r: RecordRow): Promise<number> {
    if (USE_MOCK) {
        const m = /(\d+)\s*\/\s*(\d+)/.exec(r.st);
        return mockDelay(m ? Math.max(0, Number(m[2]) - Number(m[1])) : 0);
    }
    const { count } = await post<{
        count: number;
    }>(`/records/${r.id}/retry-failed`);
    return count;
}
export async function retrigQms(r: RecordRow): Promise<void> {
    if (USE_MOCK) {
        void r;
        return mockDelay(undefined);
    }
    await post(`/records/${r.id}/retrigger-qms`);
}
export async function retriggerRecord(recordId: number): Promise<{
    ok: boolean;
    message: string;
}> {
    if (USE_MOCK)
        return mockDelay({ ok: true, message: '（mock）已触发' });
    return post<{
        ok: boolean;
        message: string;
    }>(`/records/${recordId}/retrigger`);
}
export async function triggerQms(qmsId: number, label: string): Promise<void> {
    if (USE_MOCK) {
        recordsStore.trigLog.push('QMS → ' + label);
        return mockDelay(undefined);
    }
    await post('/qms/trigger', { id: qmsId });
}
export async function triggerStrm(strmId: number, label: string): Promise<void> {
    if (USE_MOCK) {
        recordsStore.trigLog.push('STRM → ' + label);
        return mockDelay(undefined);
    }
    await post('/strm/trigger', { id: strmId });
}
