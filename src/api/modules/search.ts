/**
 * 搜索转存领域 API —— mock 先行。
 * 真实场景里搜索请求由后端代理转发 PanSou（前端不直连，避免 TG 频道配置与地址暴露）。
 * 后端就绪后：把 mockDelay(...) 换成 http 调用（端点写在 TODO 注释里），页面代码不动。
 */
import { mockDelay } from '../http'
import { searchStore, searchStoreChannels, PANSOU_ADDR, type SearchChannel } from '../mock/search'
import type { SearchResultItem } from '@/types/model'

export type { SearchChannel }

/**
 * 按关键词检索聚合结果。
 * 延迟故意拉到 900ms+：给「正在检索 / 骨架屏 / 扫源计数」动效留出演出的时间。
 */
export function getSearchResults(keyword: string): Promise<SearchResultItem[]> {
  // TODO 后端: GET /api/search/results?kw=
  void keyword
  const dur = 900 + Math.round(Math.random() * 500)
  return mockDelay(searchStore.results, dur)
}

/**
 * 首屏结果集：不走检索动效（等价原型 window.results 页面打开时已在场）。
 * 真实场景对应「上次检索的缓存结果」；点搜索才走 getSearchResults 的完整动效。
 */
export function getInitialResults(): Promise<SearchResultItem[]> {
  // TODO 后端: GET /api/search/results?kw=&cached=1
  return mockDelay(searchStore.results)
}

/** 搜索源频道列表（筛选条勾选项） */
export function getSearchChannels(): Promise<SearchChannel[]> {
  // TODO 后端: GET /api/search/channels
  return mockDelay(searchStoreChannels)
}

/** PanSou 聚合源地址 */
export function getPanSouAddr(): Promise<string> {
  // TODO 后端: GET /api/search/pansou-addr
  return mockDelay(PANSOU_ADDR)
}
