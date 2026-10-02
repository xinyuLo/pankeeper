import { get } from '../http'
import { USE_MOCK } from '../http'

export interface DirItem {
  fid: string
  name: string
  is_dir: boolean
  size: number
}

/**
 * 网盘目录浏览（转存弹窗/配置浏览共用）：按父目录拉一层，后端带目录树缓存。
 * type 支持 quark / baidu；115 适配器待实现（会返回 400）。
 */
export function getFilesList(
  type: string,
  parent = '0',
  path = '',
  force = false,
  accId: number | null = null,
): Promise<DirItem[]> {
  return getFilesListMeta(type, parent, path, force, accId).then((r) => r.items)
}

/** 带 cached 标记的版本：cached=false = 这层真打了网盘（调用方据此做风控节流） */
export async function getFilesListMeta(
  type: string,
  parent = '0',
  path = '',
  force = false,
  accId: number | null = null,
): Promise<{ cached: boolean; items: DirItem[] }> {
  if (USE_MOCK) throw new Error('mock 模式无真实目录，请切换真实后端')
  const r = await get<{ cached: boolean; items: DirItem[] }>('/files/list', {
    params: { type, parent, path, force_refresh: force, acc_id: accId || undefined },
  })
  return { cached: !!r?.cached, items: r?.items || [] }
}
