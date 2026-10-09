import { get, post } from '../http';
import { USE_MOCK } from '../http';
export interface DirItem {
    fid: string;
    name: string;
    is_dir: boolean;
    size: number;
}
export function getFilesList(type: string, parent = '0', path = '', force = false, accId: number | null = null): Promise<DirItem[]> {
    return getFilesListMeta(type, parent, path, force, accId).then((r) => r.items);
}
export async function getFilesListMeta(type: string, parent = '0', path = '', force = false, accId: number | null = null): Promise<{
    cached: boolean;
    items: DirItem[];
}> {
    if (USE_MOCK)
        throw new Error('mock 模式无真实目录，请切换真实后端');
    const r = await get<{
        cached: boolean;
        items: DirItem[];
    }>('/files/list', {
        params: { type, parent, path, force_refresh: force, acc_id: accId || undefined },
    });
    return { cached: !!r?.cached, items: r?.items || [] };
}
export function createDir(payload: {
    type: string;
    accId: number | null;
    parentPath: string;
    parentFid?: string;
    cacheKey: string;
    name: string;
}): Promise<{
    fid: string;
    path: string;
}> {
    return post<{
        fid: string;
        path: string;
    }>('/files/dir', {
        type: payload.type,
        acc_id: payload.accId || undefined,
        parent_path: payload.parentPath,
        parent_fid: payload.parentFid || '',
        cache_key: payload.cacheKey,
        name: payload.name,
    });
}
export function renameDir(payload: {
    type: string;
    accId: number | null;
    path: string;
    fid?: string;
    cacheKey: string;
    newName: string;
}): Promise<{
    fid: string;
    path: string;
}> {
    return post<{
        fid: string;
        path: string;
    }>('/files/dir/rename', {
        type: payload.type,
        acc_id: payload.accId || undefined,
        path: payload.path,
        fid: payload.fid || '',
        cache_key: payload.cacheKey,
        new_name: payload.newName,
    });
}
export function deleteDir(payload: {
    type: string;
    accId: number | null;
    path: string;
    fid?: string;
    cacheKey: string;
}): Promise<{
    ok: boolean;
}> {
    return post<{
        ok: boolean;
    }>('/files/dir/delete', {
        type: payload.type,
        acc_id: payload.accId || undefined,
        path: payload.path,
        fid: payload.fid || '',
        cache_key: payload.cacheKey,
    });
}
