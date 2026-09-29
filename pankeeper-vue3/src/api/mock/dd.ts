import { reactive } from 'vue'
import type { DdItem, DdQmsPath, DdStrmPath } from '@/types/model'

/**
 * 转存配置目录 mock（快速转存的依据）—— 原型 page-default-dir.html 数据原样移植。
 * 内存态即可（原型同样是会话内可变、不落 localStorage）。
 */
export const ddStore = reactive<{ items: DdItem[]; seq: number }>({
  items: [
    { id: 1, type: 'baidu', account: 'bd_main', sort: 1, name: '电视剧', path: '/影视/国产剧', is_default: true, qms_on: true, qms_id: 1, strm_id: 1 },
    { id: 2, type: 'baidu', account: 'bd_main', sort: 2, name: '电影', path: '/影视/电影', is_default: false, qms_on: false, qms_id: null, strm_id: null },
    { id: 3, type: 'baidu', account: 'bd_main', sort: 3, name: '纪录片', path: '/影视/纪录片', is_default: false, qms_on: true, qms_id: 3, strm_id: 3 },
    { id: 4, type: 'baidu', account: 'bd_sub', sort: 1, name: '备份', path: '/网盘备份', is_default: true, qms_on: false, qms_id: null, strm_id: null },
    { id: 5, type: 'quark', account: 'qk_main', sort: 1, name: '电视剧', path: '/媒体/剧集', is_default: true, qms_on: true, qms_id: 1, strm_id: 1 },
    { id: 6, type: 'quark', account: 'qk_main', sort: 2, name: '动漫', path: '/媒体/动漫', is_default: false, qms_on: false, qms_id: null, strm_id: null },
    { id: 7, type: '115', account: 'p115_main', sort: 1, name: '电影', path: '/影视/电影', is_default: true, qms_on: false, qms_id: null, strm_id: null },
  ],
  seq: 100,
})

export const ddQmsPaths: DdQmsPath[] = [
  { id: 1, media_type: 'tv', source_path: '/vol1/1001/media/电视剧' },
  { id: 2, media_type: 'movie', source_path: '/vol1/1001/media/电影' },
  { id: 3, media_type: 'tv', source_path: '/vol1/1001/media/纪录片' },
]

export const ddStrmPaths: DdStrmPath[] = [
  { id: 1, remote_path: '/媒体/剧集' },
  { id: 2, remote_path: '/媒体/电影' },
  { id: 3, remote_path: '/媒体/纪录片' },
]

export function ddGetByType(type: string): DdItem[] {
  return ddStore.items.filter((x) => x.type === type).sort((a, b) => (a.sort || 0) - (b.sort || 0))
}

export function ddGetDefault(type: string): DdItem | null {
  const list = ddGetByType(type)
  return list.find((x) => x.is_default) || list[0] || null
}

export function ddHasConfig(type: string): boolean {
  return ddGetByType(type).length > 0
}

export function ddFind(id: number): DdItem | null {
  return ddStore.items.find((x) => x.id === id) || null
}

export function ddQmsPathFind(id: number | null): DdQmsPath | null {
  if (id == null) return null
  return ddQmsPaths.find((x) => x.id === id) || null
}

export function ddStrmPathFind(id: number | null): DdStrmPath | null {
  if (id == null) return null
  return ddStrmPaths.find((x) => x.id === id) || null
}
