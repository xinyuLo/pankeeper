/**
 * 网盘连接领域 API —— 原型 _shell.html「连接卡片」的 mock 实现。
 * 设计红线（docs/03 accounts-settings）：凭据加密存储，接口只回状态、绝不回填明文。
 * 后端就绪后：每个函数把 mockDelay(...) 换成 http 调用（端点写在 TODO 注释里）。
 */
import { mockDelay } from '../http'
import { accountStore, type AccountRow } from '../mock/accounts'
import { MAIN_ORDER } from '../mock/meta'
import type { AccountStatus, MainDriveType } from '@/types/model'

export function listAccounts(): Promise<AccountRow[]> {
  // TODO 后端: GET /api/accounts
  return mockDelay(MAIN_ORDER.map((t) => accountStore.accounts[t]))
}

/** 「检测连通」的返回体：只有状态与提示，永远不含凭据明文 */
export interface CheckResult {
  ok: boolean
  /** toast 语气：成功/失败/尚未配置（提示但不算错误） */
  kind: 'success' | 'error' | 'warning'
  message: string
  /** 检测后的最新状态（真实后端可能把 connected 探成 expired） */
  status: AccountStatus
  /** 本次检测时刻（MM-DD HH:mm，与原型 last_check 同格式） */
  last_check: string
}

function nowStamp(): string {
  const d = new Date()
  const p = (v: number) => String(v).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

export function checkAccount(type: MainDriveType): Promise<CheckResult> {
  // TODO 后端: POST /api/accounts/:type/check（adapter 第 5 件事：凭据有效性自检）
  const row = accountStore.accounts[type]
  // 未配置：没有可检测的东西，不动 last_check（保持「从未配置」）
  if (row.status === 'unset') {
    return mockDelay({ ok: false, kind: 'warning', message: '尚未配置凭据，请先「配置凭据」', status: row.status, last_check: row.last_check })
  }
  const last = nowStamp()
  row.last_check = last
  if (row.status === 'expired') {
    // 检测本身是成功的，但结论是凭据失效 —— 记录检测时间、维持失效态供页面标红
    return mockDelay({ ok: false, kind: 'error', message: '检测失败：Cookie 已过期，请重新配置', status: 'expired', last_check: last })
  }
  return mockDelay({ ok: true, kind: 'success', message: '连通正常', status: 'connected', last_check: last })
}

/** 清空凭据：状态置回未配置（等同删除密文，不可恢复） */
export function clearAccount(type: MainDriveType): Promise<void> {
  // TODO 后端: DELETE /api/accounts/:type/credential
  const row = accountStore.accounts[type]
  row.status = 'unset'
  row.last_check = '从未配置'
  return mockDelay(undefined)
}

/** 打开凭据配置弹窗（原型只 toast 占位；真实实现里凭据只写不读） */
export function configAccount(type: MainDriveType): Promise<void> {
  // TODO 后端: 凭据配置走独立表单接口 POST /api/accounts/:type/credential
  void type
  return mockDelay(undefined)
}
