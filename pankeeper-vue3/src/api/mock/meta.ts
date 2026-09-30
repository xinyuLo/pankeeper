import type { DriveType, MainDriveType } from '@/types/model'

/** 网盘元信息（名称/品牌色/标签色），原型 META 原样移植 */
export const DRIVE_META: Record<DriveType, { name: string; color: string; full: string; tag: string }> = {
  baidu: { name: '百度', color: '#1677ff', full: '百度网盘', tag: 't-baidu' },
  quark: { name: '夸克', color: '#13c2c2', full: '夸克网盘', tag: 't-quark' },
  '115': { name: '115', color: '#722ed1', full: '115 网盘', tag: 't-115' },
  '123': { name: '123', color: '#fa8c16', full: '123 云盘', tag: 't-123' },
  ali: { name: '阿里', color: '#ff6a00', full: '阿里云盘', tag: 't-ali' },
  xunlei: { name: '迅雷', color: '#2db7f5', full: '迅雷网盘', tag: 't-xunlei' },
  uc: { name: 'UC', color: '#597ef7', full: 'UC 网盘', tag: 't-uc' },
}

/** 转存/自动转存固定顺序 */
export const MAIN_ORDER: MainDriveType[] = ['baidu', 'quark', '115']


/** QMS 媒体类型中文 */
export const DD_MEDIA: Record<string, string> = { tv: '剧集', movie: '电影' }
