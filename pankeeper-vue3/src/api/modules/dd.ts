import { del, get, mockDelay, post, put, USE_MOCK } from '../http';
import { ddStore, ddQmsPaths, ddStrmPaths, ddGetDefault, ddHasConfig, ddFind } from '../mock/dd';
import type { DdItem, DdItemDraft, DdQmsPath, DdStrmPath } from '@/types/model';
export function listDdItems(): Promise<DdItem[]> {
    if (USE_MOCK)
        return mockDelay(ddStore.items);
    return get<DdItem[]>('/dd/items').then((rows) => {
        ddStore.items.splice(0, ddStore.items.length, ...rows);
        return rows;
    });
}
export function getDefaultDir(type: string): Promise<DdItem | null> {
    if (USE_MOCK)
        return mockDelay(ddGetDefault(type));
    return get<DdItem | null>('/dd/default', { params: { type } });
}
export function hasDirConfig(type: string): Promise<boolean> {
    if (USE_MOCK)
        return mockDelay(ddHasConfig(type));
    return get<boolean>('/dd/has-config', { params: { type } });
}
export function findDdItem(id: number): Promise<DdItem | null> {
    if (USE_MOCK)
        return mockDelay(ddFind(id));
    return get<DdItem | null>(`/dd/items/${id}`);
}
export async function saveDdItem(item: DdItemDraft): Promise<void> {
    if (USE_MOCK) {
        if (item.id != null) {
            const idx = ddStore.items.findIndex((x) => x.id === item.id);
            if (idx >= 0)
                ddStore.items[idx] = item as DdItem;
            else
                ddStore.items.push({ ...(item as DdItem), id: ++ddStore.seq });
        }
        else {
            ddStore.items.push({ ...(item as DdItem), id: ++ddStore.seq });
        }
        return mockDelay(undefined);
    }
    if (item.id != null)
        await put(`/dd/items/${item.id}`, item);
    else
        await post('/dd/items', item);
    await listDdItems();
}
export async function deleteDdItem(id: number): Promise<void> {
    if (USE_MOCK) {
        ddStore.items = ddStore.items.filter((x) => x.id !== id);
        return mockDelay(undefined);
    }
    await del(`/dd/items/${id}`);
    await listDdItems();
}
export async function setDefaultDir(id: number): Promise<void> {
    if (USE_MOCK) {
        const target = ddStore.items.find((x) => x.id === id);
        if (target) {
            for (const x of ddStore.items) {
                if (x.type === target.type && x.account === target.account)
                    x.is_default = x.id === id;
            }
        }
        return mockDelay(undefined);
    }
    await put(`/dd/items/${id}/default`);
    await listDdItems();
}
export function listQmsPaths(): Promise<DdQmsPath[]> {
    if (USE_MOCK)
        return mockDelay(ddQmsPaths);
    return get<DdQmsPath[]>('/qms/scrape-pathes');
}
export function listStrmPaths(): Promise<DdStrmPath[]> {
    if (USE_MOCK)
        return mockDelay(ddStrmPaths);
    return get<DdStrmPath[]>('/qms/sync-pathes');
}
