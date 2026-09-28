/**
 * 转存配置（默认目录）领域 API —— mock 实现参考样板。
 * 后端就绪后：每个函数把 mockDelay(...) 换成 http 调用（端点写在 TODO 注释里）。
 */
import { mockDelay } from '../http'
import { ddStore, ddQmsPaths, ddStrmPaths, ddGetByType, ddGetDefault, ddHasConfig, ddFind, ddQmsPathFind, ddStrmPathFind } from '../mock/dd'
import type { DdItem, DdQmsPath, DdStrmPath } from '@/types/model'

export function listDdItems(): Promise<DdItem[]> {
  // TODO 后端: GET /api/dd/items
  return mockDelay(ddStore.items)
}

export function getDefaultDir(type: string): Promise<DdItem | null> {
  // TODO 后端: GET /api/dd/default?type=
  return mockDelay(ddGetDefault(type))
}

export function hasDirConfig(type: string): Promise<boolean> {
  // TODO 后端: GET /api/dd/has-config?type=
  return mockDelay(ddHasConfig(type))
}

export function findDdItem(id: number): Promise<DdItem | null> {
  // TODO 后端: GET /api/dd/items/:id
  return mockDelay(ddFind(id))
}

export function saveDdItem(item: DdItem): Promise<void> {
  // TODO 后端: POST /api/dd/items | PUT /api/dd/items/:id
  const idx = ddStore.items.findIndex((x) => x.id === item.id)
  if (idx >= 0) ddStore.items[idx] = { ...item }
  else ddStore.items.push({ ...item, id: ++ddStore.seq })
  return mockDelay(undefined)
}

export function deleteDdItem(id: number): Promise<void> {
  // TODO 后端: DELETE /api/dd/items/:id
  ddStore.items = ddStore.items.filter((x) => x.id !== id)
  return mockDelay(undefined)
}

export function setDefaultDir(id: number): Promise<void> {
  // TODO 后端: PUT /api/dd/items/:id/default
  const target = ddStore.items.find((x) => x.id === id)
  if (target) {
    for (const x of ddStore.items) {
      if (x.type === target.type && x.account === target.account) x.is_default = x.id === id
    }
  }
  return mockDelay(undefined)
}

export function listQmsPaths(): Promise<DdQmsPath[]> {
  // TODO 后端: GET /api/qms/paths
  return mockDelay(ddQmsPaths)
}

export function listStrmPaths(): Promise<DdStrmPath[]> {
  // TODO 后端: GET /api/strm/paths
  return mockDelay(ddStrmPaths)
}
