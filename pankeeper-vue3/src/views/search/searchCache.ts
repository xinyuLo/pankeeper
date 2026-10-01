/* 搜索状态跨页保留：切到转存记录再回来不清空关键词/结果/tab/耗时。
 * 模块级缓存——组件随路由重建，但模块变量一直在。SearchTransfer 专用。 */
import type { DriveType, SearchResultItem } from '@/types/model'

let savedKw = ''
let savedResults: SearchResultItem[] | null = null
let savedActive: 'all' | DriveType = 'all'
let savedElapsed: string | null = null

export function saveSearchCache(s: {
  kw: string
  results: SearchResultItem[]
  active: 'all' | DriveType
  elapsed: string | null
}) {
  savedKw = s.kw
  savedResults = s.results
  savedActive = s.active
  savedElapsed = s.elapsed
}

export function loadSearchCache() {
  return savedResults
    ? { kw: savedKw, results: savedResults, active: savedActive, elapsed: savedElapsed }
    : null
}
