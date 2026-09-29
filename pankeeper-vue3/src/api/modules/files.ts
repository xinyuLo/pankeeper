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
 * type 目前支持 quark（baidu/115 adapter 在 M3）。
 */
export function getFilesList(
  type: string,
  parent = '0',
  path = '',
  force = false,
): Promise<DirItem[]> {
  if (USE_MOCK) return Promise.reject(new Error('mock 模式无真实目录，请切换真实后端'))
  return get<DirItem[]>('/files/list', { params: { type, parent, path, force_refresh: force } })
}
