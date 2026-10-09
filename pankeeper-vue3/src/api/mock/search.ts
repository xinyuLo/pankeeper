import { reactive } from 'vue'
import type { SearchResultItem } from '@/types/model'

/**
 * 搜索结果 mock —— 原型 _shell.html 的 window.results 数组原样移植。
 * 内存态即可（与原型一致：刷新重置）。
 * 字段含义：n=资源名 t=网盘类型 s=大小 d=分享时间 ok=是否有凭据 hot=「极速」标。
 */
export const searchStore = reactive<{ results: SearchResultItem[] }>({
  results: [
    { n: '庆余年.第二季.4K.HDR.国粤双语.简繁特效字幕', t: 'baidu', s: '82.4 GB', d: '2 天前', ok: true, hot: true },
    { n: '庆余年第二季.1080P.腾讯版.全36集', t: 'quark', s: '46.1 GB', d: '5 小时前', ok: true },
    { n: '庆余年 第二季 S02 2160P TrueHD Atmos 原盘', t: '115', s: '198 GB', d: '1 周前', ok: true },
    { n: '庆余年 第二季 4K 原盘 DIY 国语简繁', t: '123', s: '210 GB', d: '3 天前', ok: false },
    { n: '庆余年第二季 幕后花絮 + 预告特辑合集', t: 'baidu', s: '6.2 GB', d: '12 天前', ok: true },
    { n: '庆余年 第二季 4K 高码率 收藏版', t: 'ali', s: '164 GB', d: '6 天前', ok: true },
    { n: '庆余年第二季 全集 1080P 内嵌字幕', t: 'xunlei', s: '38.7 GB', d: '1 天前', ok: false },
    { n: '庆余年 第二季 2160P HDR10 杜比视界', t: 'uc', s: '176 GB', d: '9 天前', ok: true },
    { n: '庆余年第二季 加长版 含删减片段', t: 'quark', s: '52.3 GB', d: '3 小时前', ok: true },
    { n: '庆余年 S02 全集 1080P 无删减', t: 'baidu', s: '44.5 GB', d: '8 天前', ok: true },
    { n: '庆余年 第二季 4K 60帧 补帧版', t: '115', s: '264 GB', d: '2 周前', ok: true },
    { n: '庆余年第二季 台配国语 繁中字幕', t: 'quark', s: '41.8 GB', d: '4 天前', ok: true },
    { n: '庆余年 第二季 原声大碟 OST 无损', t: 'ali', s: '3.4 GB', d: '15 天前', ok: true },
    { n: '庆余年第二季 4K 原盘 REMUX 简体', t: '123', s: '195 GB', d: '5 天前', ok: false },
    { n: '庆余年 第二季 2160P 十bit 压制版', t: 'baidu', s: '92.1 GB', d: '11 天前', ok: true },
    { n: '庆余年第二季 全36集 4K 收藏打包', t: '115', s: '188 GB', d: '1 周前', ok: true },
    { n: '庆余年 第二季 1080P 小巧版 手机看', t: 'uc', s: '18.9 GB', d: '20 小时前', ok: true },
    { n: '庆余年第二季 4K HDR 多音轨 简繁英', t: 'quark', s: '88.6 GB', d: '2 天前', ok: true },
    { n: '庆余年 第二季 未删减 4K 合集', t: 'baidu', s: '96.3 GB', d: '6 天前', ok: true },
    { n: '庆余年第二季 1080P 高码 全集 附花絮', t: 'xunlei', s: '57.2 GB', d: '3 天前', ok: false },
    { n: '庆余年 第二季 2160P 原盘 国语简繁', t: 'ali', s: '203 GB', d: '2 周前', ok: true },
    { n: '庆余年第二季 4K 修复版 色彩增强', t: '115', s: '121 GB', d: '9 天前', ok: true },
    { n: '庆余年 第二季 合集 1080P 附字幕包', t: '123', s: '43.7 GB', d: '13 天前', ok: true },
    { n: '庆余年第二季 4K 杜比全景声 收藏', t: 'quark', s: '171 GB', d: '7 天前', ok: true },
    { n: '庆余年 第二季 全季 1080P 内封字幕', t: 'baidu', s: '45.9 GB', d: '4 天前', ok: true },
    { n: '庆余年第二季 番外篇 特别篇 合集', t: 'uc', s: '9.8 GB', d: '18 天前', ok: true },
    { n: '庆余年 第二季 4K 原盘 ISO 双碟', t: '115', s: '312 GB', d: '3 周前', ok: true },
    { n: '庆余年第二季 1080P 双字幕 简繁', t: 'xunlei', s: '40.2 GB', d: '16 小时前', ok: false },
    { n: '庆余年 第二季 4K HDR 精校字幕版', t: 'ali', s: '98.4 GB', d: '5 天前', ok: true },
    { n: '庆余年第二季 全集 4K 无广告', t: 'quark', s: '85.1 GB', d: '1 天前', ok: true },
  ],
})

/** 搜索源频道（筛选条勾选项） */
export interface SearchChannel {
  name: string
  /** 是否勾选参与检索 */
  on: boolean
}

/** 频道 mock（前三个默认勾选，与原型一致） */
export const searchStoreChannels = reactive<SearchChannel[]>([
  { name: '影视资源共享', on: true },
  { name: '4K 原盘收藏', on: true },
  { name: '国产剧每日更新', on: true },
  { name: '纪录片合集', on: false },
])

/** PanSou 聚合源地址（筛选条右侧展示用） */
export const PANSOU_ADDR = '192.168.2.77:8028'
