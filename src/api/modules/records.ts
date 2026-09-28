/**
 * 转存记录领域 API —— 双模式：mock（内存 store）/ 真实（后端 /api/records）。
 * 记录是快照数据：列表 + 详情日志 + 清理 + 重试/触发动作。
 */
import { del, get, mockDelay, post, USE_MOCK } from '../http'
import { recordLogOf, recordsStore } from '../mock/records'
import type { RecordRow } from '../mock/records'
import type { QueueLogLine } from '@/types/model'

export function listRecords(): Promise<RecordRow[]> {
  if (USE_MOCK) return mockDelay(recordsStore.items)
  return get<RecordRow[]>('/records')
}

export function getRecordLog(r: RecordRow): Promise<QueueLogLine[]> {
  if (USE_MOCK) return mockDelay(recordLogOf(r))
  return get<QueueLogLine[]>(`/records/${r.id}/logs`)
}

export async function deleteRecord(id: number): Promise<void> {
  if (USE_MOCK) {
    recordsStore.items = recordsStore.items.filter((x) => x.id !== id)
    return mockDelay(undefined)
  }
  await del(`/records/${id}`)
}

/** 清空三月前记录，返回清掉的条数（0 = 没有三月前数据，页面据此提示） */
export async function clearRecords3MonthsAgo(): Promise<number> {
  if (USE_MOCK) {
    const cutoff = Date.now() - 90 * 24 * 3600 * 1000
    const y = new Date().getFullYear()
    const keep: RecordRow[] = []
    let removed = 0
    for (const r of recordsStore.items) {
      const m = /^(\d{2})-(\d{2}) (\d{2}):(\d{2})$/.exec(r.tm)
      const t = m ? new Date(y, Number(m[1]) - 1, Number(m[2]), Number(m[3]), Number(m[4])).getTime() : 0
      if (t && t < cutoff) removed++
      else keep.push(r)
    }
    recordsStore.items = keep
    return mockDelay(removed)
  }
  const before = new Date(Date.now() - 90 * 24 * 3600 * 1000).toISOString()
  const { count } = await del<{ count: number }>('/records', { params: { before } })
  return count
}

/** 重试失败项：真实模式把记录重新入队，返回排队位次（页面 toast 用） */
export async function retryFailedItems(r: RecordRow): Promise<number> {
  if (USE_MOCK) {
    const m = /(\d+)\s*\/\s*(\d+)/.exec(r.st)
    return mockDelay(m ? Math.max(0, Number(m[2]) - Number(m[1])) : 0)
  }
  const { count } = await post<{ count: number }>(`/records/${r.id}/retry-failed`)
  return count
}

/** 抽屉里「再次触发 QMS」 */
export async function retrigQms(r: RecordRow): Promise<void> {
  if (USE_MOCK) {
    void r
    return mockDelay(undefined)
  }
  await post(`/records/${r.id}/retrigger-qms`)
}

/** 手动触发弹窗：触发 QMS 刮削。label 只做留痕 */
export async function triggerQms(qmsId: number, label: string): Promise<void> {
  if (USE_MOCK) {
    recordsStore.trigLog.push('QMS → ' + label)
    return mockDelay(undefined)
  }
  await post('/qms/trigger', { id: qmsId })
}

/** 手动触发弹窗：触发 STRM 生成 */
export async function triggerStrm(strmId: number, label: string): Promise<void> {
  if (USE_MOCK) {
    recordsStore.trigLog.push('STRM → ' + label)
    return mockDelay(undefined)
  }
  await post('/strm/trigger', { id: strmId })
}
