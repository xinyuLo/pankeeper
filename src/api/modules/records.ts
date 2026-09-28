/**
 * 转存记录领域 API —— mock 实现，切换后端时把 mockDelay(...) 换成本文件补的 http 调用即可。
 * 记录是快照数据：列表 + 详情日志 + 清理 + 重试/触发动作。
 */
import { mockDelay } from '../http'
import { recordLogOf, recordsStore } from '../mock/records'
import type { RecordRow } from '../mock/records'
import type { QueueLogLine } from '@/types/model'

export function listRecords(): Promise<RecordRow[]> {
  // TODO 后端: GET /api/records
  return mockDelay(recordsStore.items)
}

export function getRecordLog(r: RecordRow): Promise<QueueLogLine[]> {
  // TODO 后端: GET /api/records/:id/logs
  return mockDelay(recordLogOf(r))
}

export function deleteRecord(id: number): Promise<void> {
  // TODO 后端: DELETE /api/records/:id
  recordsStore.items = recordsStore.items.filter((x) => x.id !== id)
  return mockDelay(undefined)
}

/** 清空三月前记录，返回清掉的条数（0 = 没有三月前数据，页面据此提示）。
 *  mock 时间只有「MM-DD HH:mm」，按当前年补全再比 cutoff。 */
export function clearRecords3MonthsAgo(): Promise<number> {
  // TODO 后端: DELETE /api/records?before=<ISO 时间>
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

/** 重试失败项，返回失败项个数（0 = 没有失败项），页面据此给不同 toast */
export function retryFailedItems(r: RecordRow): Promise<number> {
  // TODO 后端: POST /api/records/:id/retry-failed
  const m = /(\d+)\s*\/\s*(\d+)/.exec(r.st)
  return mockDelay(m ? Math.max(0, Number(m[2]) - Number(m[1])) : 0)
}

/** 抽屉里「再次触发 QMS」 */
export function retrigQms(r: RecordRow): Promise<void> {
  // TODO 后端: POST /api/records/:id/retrigger-qms
  void r
  return mockDelay(undefined)
}

/** 手动触发弹窗：触发 QMS 刮削。label 只做留痕，方便调试核对触发了什么 */
export function triggerQms(qmsId: number, label: string): Promise<void> {
  // TODO 后端: POST /api/qms/trigger { id }
  recordsStore.trigLog.push('QMS → ' + label)
  return mockDelay(undefined)
}

/** 手动触发弹窗：触发 STRM 生成 */
export function triggerStrm(strmId: number, label: string): Promise<void> {
  // TODO 后端: POST /api/strm/trigger { id }
  recordsStore.trigLog.push('STRM → ' + label)
  return mockDelay(undefined)
}
