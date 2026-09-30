/**
 * 网盘连接领域 API —— 多账号版（每平台可多行，同时在线）。
 * 设计红线（docs/03 accounts-settings）：凭据加密存储，接口只回状态、绝不回填明文。
 */
import { del, get, mockDelay, post, put, USE_MOCK } from '../http'
import { accountStore, type AccountRow } from '../mock/accounts'
import type { AccountStatus, AccountSummary, MainDriveType } from '@/types/model'

/** 容量 + 会员摘要（类型定义在 types/model.ts，这里 re-export 保持既有引用可用） */
export type { AccountSummary }

export function getSummary(accId: number): Promise<AccountSummary> {
  if (USE_MOCK) {
    return mockDelay({ capacity: null, vip: null })
  }
  return get<AccountSummary>(`/accounts/${accId}/summary`)
}

export function listAccounts(): Promise<AccountRow[]> {
  if (USE_MOCK) return mockDelay(accountStore.accounts)
  // 真实模式：后端数组整体替换 store（保留 mock 里的品牌元信息补齐）
  return get<AccountRow[]>('/accounts').then((rows) => {
    accountStore.accounts.splice(
      0,
      accountStore.accounts.length,
      ...rows.map((r) => ({
        ...r,
        // 后端没有的展示字段用 mock 元信息兜底（type 变了也安全）
        ...(accountStore.accounts.find((x) => x.type === r.type && x.short === r.short) || {}),
        id: r.id,
        type: r.type as MainDriveType,
        alias: r.alias || '',
        status: r.status,
        nickname: r.nickname,
        last_check: r.last_check,
        notify: !!r.notify,
        summary: r.summary,
      })),
    )
    return accountStore.accounts
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

export function checkAccount(accId: number): Promise<CheckResult> {
  if (USE_MOCK) {
    const row = accountStore.accounts.find((a) => a.id === accId)
    if (!row || row.status === 'unset') {
      return mockDelay({ ok: false, kind: 'warning', message: '尚未配置凭据，请先「配置凭据」', status: 'unset', last_check: '从未配置' })
    }
    if (row.status === 'expired') {
      return mockDelay({ ok: false, kind: 'error', message: '检测失败：Cookie 已过期，请重新配置', status: 'expired', last_check: row.last_check })
    }
    return mockDelay({ ok: true, kind: 'success', message: '连通正常', status: 'connected', last_check: row.last_check })
  }
  return post<CheckResult>(`/accounts/${accId}/check`)
}

/** 删除整个账号（卡片随之消失）。没有凭据的空壳卡片不保留——清空即删除 */
export async function deleteAccount(accId: number): Promise<void> {
  if (USE_MOCK) {
    accountStore.accounts = accountStore.accounts.filter((a) => a.id !== accId)
    return mockDelay(undefined)
  }
  await del(`/accounts/${accId}`)
  await listAccounts()
}

/** 新增账号（选平台 + 粘贴 Cookie + 可选别名）：后端保存即验证，失败带原因抛错 */
export async function addAccount(type: MainDriveType, cookies: string, alias: string): Promise<{ nickname: string }> {
  const { nickname } = await post<{ ok: boolean; nickname: string }>(`/accounts/${type}`, { cookies, alias })
  await listAccounts()
  return { nickname }
}

/** 保存并验证凭据（粘贴整串 Cookie）：后端保存即验证，失败会带原因抛错 */
export async function saveCredential(accId: number, cookies: string, alias = ''): Promise<{ nickname: string }> {
  const { nickname } = await put<{ ok: boolean; nickname: string }>(`/accounts/${accId}/credential`, { cookies, alias })
  await listAccounts()
  return { nickname }
}

/** 账号粒度的「失效通知」开关：探活失败时是否发 Server 酱。
 *  注意它只是粒度开关，总闸仍在「系统设置 → 推送通知」（enabled + on_cred）。 */
export async function setDriveNotify(accId: number, enabled: boolean): Promise<void> {
  if (USE_MOCK) {
    const row = accountStore.accounts.find((a) => a.id === accId)
    if (row) row.notify = enabled
    return mockDelay(undefined, 200)
  }
  await put(`/accounts/${accId}/notify`, { enabled })
}
