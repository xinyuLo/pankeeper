/**
 * 网盘日志领域 API —— 双模式。
 * 数据源：后端按「日期 + 网盘」累计的 HTTP 请求次数（风控预警用）。
 * 阈值（绿/橙/红分界）由后端下发，前端不写死。
 */
import { get, mockDelay, USE_MOCK } from '../http'
import { MAIN_ORDER } from '../mock/meta'

export type LogLevel = 'ok' | 'warn' | 'danger'

export interface DriveLogCard {
  drive: string
  count: number
  level: LogLevel
}

export interface DriveLogTrendDay {
  date: string
  total: number
  by: Record<string, number>
}

export interface DriveLogData {
  today: { date: string; total: number; drives: DriveLogCard[] }
  trend: DriveLogTrendDay[]
  thresholds: { warn: number; danger: number }
  /** 统计保留天数（默认 180＝半年），供页面说明区展示 */
  retain_days: number
}

/** 档位 → 文案（配色走 CSS 类） */
export const LEVEL_META: Record<LogLevel, { label: string; hint: string }> = {
  ok: { label: '正常', hint: '请求量在正常自用区间' },
  warn: { label: '偏多', hint: '请求偏多，留意是否跑得太频繁' },
  danger: { label: '频繁', hint: '请求过于频繁，有触发网盘风控的风险' },
}

/** mock 阈值，与后端 reqstat.WARN_AT/DANGER_AT 保持一致 */
const MOCK_WARN = 150
const MOCK_DANGER = 400

function lvl(n: number): LogLevel {
  if (n >= MOCK_DANGER) return 'danger'
  if (n >= MOCK_WARN) return 'warn'
  return 'ok'
}

function mockData(days: number): DriveLogData {
  const base: Record<string, number> = { baidu: 18, quark: 46, '115': 5 }
  const trend: DriveLogTrendDay[] = Array.from({ length: days }, (_, i) => {
    const d = new Date(Date.now() - (days - 1 - i) * 86400000)
    const by: Record<string, number> = {}
    for (const t of MAIN_ORDER) by[t] = Math.round((base[t] || 0) * (0.55 + Math.random() * 0.9))
    return {
      date: d.toISOString().slice(0, 10),
      total: Object.values(by).reduce((a, b) => a + b, 0),
      by,
    }
  })
  const last = trend[trend.length - 1]
  return {
    today: {
      date: last.date,
      total: last.total,
      drives: MAIN_ORDER.map((t) => ({
        drive: t,
        count: last.by[t] || 0,
        level: lvl(last.by[t] || 0),
      })),
    },
    trend,
    thresholds: { warn: MOCK_WARN, danger: MOCK_DANGER },
  }
}

/** 今日各网盘请求数 + 近 N 天趋势 */
export function getDriveLogs(days = 7): Promise<DriveLogData> {
  if (USE_MOCK) return mockDelay(mockData(days), 240)
  return get<DriveLogData>('/drive-logs', { params: { days } })
}
