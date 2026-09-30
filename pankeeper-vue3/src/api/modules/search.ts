/**
 * 搜索转存领域 API —— 双模式。
 * 真实模式：后端代理 pansou（前端不直连，频道配置与地址不暴露）。
 * 真实结果行带 url/share_code/source（入队真实转存的必要字段）。
 */
import { get, mockDelay, USE_MOCK } from '../http'
import { searchStore, searchStoreChannels, PANSOU_ADDR, type SearchChannel } from '../mock/search'
import type { SearchResultItem } from '@/types/model'

export type { SearchChannel }

/** mock 结果补上假链接：让「入队带 url」的链路在 mock 模式也可走通（类型对齐真实模式） */
function mockRows(): SearchResultItem[] {
  return searchStore.results.map((r, i) => ({
    ...r,
    url: `https://pan.${r.t === 'ali' ? 'alipan' : r.t}.example.com/s/mock${i}`,
    share_code: i % 3 === 0 ? 'ab12' : '',
    source: 'mock',
  }))
}

/**
 * 按关键词检索聚合结果。
 * mock 模式延迟故意拉长：给「正在检索 / 骨架屏 / 扫源计数」动效留出演出的时间。
 */
export function getSearchResults(keyword: string): Promise<SearchResultItem[]> {
  if (USE_MOCK) {
    const dur = 900 + Math.round(Math.random() * 500)
    return mockDelay(mockRows(), dur)
  }
  return get<SearchResultItem[]>('/search/results', { params: { kw: keyword } })
}

/** 首屏结果集：mock 直接给缓存结果；真实模式返回 []（等用户搜索） */
export function getInitialResults(): Promise<SearchResultItem[]> {
  if (USE_MOCK) return mockDelay(mockRows())
  return Promise.resolve([])
}

export function getSearchChannels(): Promise<SearchChannel[]> {
  if (USE_MOCK) return mockDelay(searchStoreChannels)
  return get<{ name: string; on: boolean }[]>('/search/channels').then((list) =>
    list.map((x) => ({ name: x.name, on: x.on })),
  )
}

export interface EngineHealth {
  ok: boolean
  plugins: number | null
  channels: number | null
}

/** 检索引擎健康度（不含地址，IP 属隐私）。实时探测，结果由后端写进缓存 */
export function getEngineHealth(): Promise<EngineHealth> {
  if (USE_MOCK) return mockDelay({ ok: true, plugins: 76, channels: 90 })
  return get<EngineHealth>('/search/health')
}

/** 上一次探测的缓存状态（每日探活/设置页测试时刷新）：首屏渲染用，不现场打网盘 */
export function getEngineHealthCached(): Promise<{ ok: boolean | null; checked_at: string }> {
  if (USE_MOCK) return mockDelay({ ok: true, checked_at: '' })
  return get<{ ok: boolean | null; checked_at: string }>('/search/health-cached')
}

export function getPanSouAddr(): Promise<string> {
  if (USE_MOCK) return mockDelay(PANSOU_ADDR)
  return get<string>('/search/pansou-addr')
}
