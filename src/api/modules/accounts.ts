/**
 * 网盘连接领域 API —— 双模式。
 * 设计红线（docs/03 accounts-settings）：凭据加密存储，接口只回状态、绝不回填明文。
 */
import { del, get, mockDelay, post, put, USE_MOCK } from '../http'
import { accountStore, type AccountRow } from '../mock/accounts'
import { MAIN_ORDER } from '../mock/meta'
import type { AccountStatus, MainDriveType } from '@/types/model'

/** 容量 + 会员摘要（卡片容量条数据源）；拿不到的字段为 null */
export interface AccountSummary {
  capacity: { total: number; used: number } | null
  vip: { name: string; expires: string | null } | null
}

export function getSummary(type: MainDriveType): Promise<AccountSummary> {
  if (USE_MOCK) {
    return mockDelay({ capacity: null, vip: null })
  }
  return get<AccountSummary>(`/accounts/${type}/summary`)
}

export function listAccounts(): Promise<AccountRow[]> {
  if (USE_MOCK) return mockDelay(MAIN_ORDER.map((t) => accountStore.accounts[t]))
  // 真实模式：后端状态灌进 store（保留前端的品牌元信息），视图照旧读 store
  return get<AccountRow[]>('/accounts').then((rows) => {
    for (const r of rows) {
      const row = accountStore.accounts[r.type as MainDriveType]
      if (!row) continue
      row.status = r.status
      row.last_check = r.last_check
      if (r.base_dir !== undefined) row.base = (r.base_dir as string) || '/'
    }
    return MAIN_ORDER.map((t) => accountStore.accounts[t])
  })
}

/** 「检测连通」的返回体：只有状态与提示，永远不含凭据明文 */
export interface CheckResult {
  ok: boolean
  /** toast 语气：成功/失败/尚未配置（提示但不算错误） */
  kind: 'success' | 'error' | 'warning'
  message: string
  status: AccountStatus
  last_check: string
}

/** 保存默认目标目录；后端凭据已配时会顺手预热该目录的目录树缓存 */
export function saveBaseDir(type: MainDriveType, path: string): Promise<{ ok: boolean; primed: boolean; message: string }> {
  if (USE_MOCK) return mockDelay({ ok: true, primed: false, message: 'mock 已保存' })
  return put<{ ok: boolean; primed: boolean; message: string }>(`/accounts/${type}/base-dir`, { path })
}

export function checkAccount(type: MainDriveType): Promise<CheckResult> {
  if (USE_MOCK) {
    const row = accountStore.accounts[type]
    if (row.status === 'unset') {
      return mockDelay({ ok: false, kind: 'warning', message: '尚未配置凭据，请先「配置凭据」', status: row.status, last_check: row.last_check })
    }
    const last = row.last_check
    if (row.status === 'expired') {
      return mockDelay({ ok: false, kind: 'error', message: '检测失败：Cookie 已过期，请重新配置', status: 'expired', last_check: last })
    }
    return mockDelay({ ok: true, kind: 'success', message: '连通正常', status: 'connected', last_check: last })
  }
  return post<CheckResult>(`/accounts/${type}/check`)
}

/** 清空凭据：状态置回未配置（等同删除密文，不可恢复） */
export async function clearAccount(type: MainDriveType): Promise<void> {
  if (USE_MOCK) {
    const row = accountStore.accounts[type]
    row.status = 'unset'
    row.last_check = '从未配置'
    return mockDelay(undefined)
  }
  await del(`/accounts/${type}/credential`)
}

/** 保存并验证凭据（粘贴整串 Cookie）：后端保存即验证，失败会带原因抛错 */
export async function saveCredential(type: MainDriveType, cookies: string): Promise<{ nickname: string }> {
  const { nickname } = await post<{ ok: boolean; nickname: string }>(`/accounts/${type}/credential`, { cookies })
  return { nickname }
}

/** 凭据配置入口：mock 模式占位（真实模式由页面弹 Cookie 表单调 saveCredential） */
export function configAccount(type: MainDriveType): Promise<void> {
  void type
  return mockDelay(undefined)
}
