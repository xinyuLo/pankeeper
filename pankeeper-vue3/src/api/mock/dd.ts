import { reactive } from 'vue';
import type { DdItem, DdQmsPath, DdStrmPath } from '@/types/model';
export const ddStore = reactive<{
    items: DdItem[];
    seq: number;
}>({
    items: [],
    seq: 100,
});
export const ddQmsPaths: DdQmsPath[] = [
    { id: 1, media_type: 'tv', source_path: '/vol1/1001/media/电视剧' },
    { id: 2, media_type: 'movie', source_path: '/vol1/1001/media/电影' },
    { id: 3, media_type: 'tv', source_path: '/vol1/1001/media/纪录片' },
];
export const ddStrmPaths: DdStrmPath[] = [
    { id: 1, remote_path: '/媒体/剧集' },
    { id: 2, remote_path: '/媒体/电影' },
    { id: 3, remote_path: '/媒体/纪录片' },
];
export function ddGetByType(type: string): DdItem[] {
    return ddStore.items.filter((x) => x.type === type).sort((a, b) => (a.sort || 0) - (b.sort || 0));
}
export function ddGetDefault(type: string): DdItem | null {
    const list = ddGetByType(type);
    return list.find((x) => x.is_default) || list[0] || null;
}
export function ddHasConfig(type: string): boolean {
    return ddGetByType(type).length > 0;
}
export function ddFind(id: number): DdItem | null {
    return ddStore.items.find((x) => x.id === id) || null;
}
export function ddQmsPathFind(id: number | null): DdQmsPath | null {
    if (id == null)
        return null;
    return ddQmsPaths.find((x) => x.id === id) || null;
}
export function ddStrmPathFind(id: number | null): DdStrmPath | null {
    if (id == null)
        return null;
    return ddStrmPaths.find((x) => x.id === id) || null;
}
