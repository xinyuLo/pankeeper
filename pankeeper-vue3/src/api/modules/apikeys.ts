/**
 * API Key 领域 API —— 外部调用凭据管理（系统管理 → API Key）。
 * 创建响应里的 key 是完整明文，仅此一次；列表只回前 8 位前缀。
 */
import { get, post, put, del, mockDelay, USE_MOCK } from '../http'

export interface ApiKeyItem {
  id: number
  name: string
  /** 前 8 位明文（pk_ + 5 位随机），用于列表辨认 */
  key_prefix: string
  /** unix 秒；0 = 从未使用 */
  last_used_at: number
  /** unix 秒 */
  created_at: number
  is_active: boolean
}

export interface ApiKeyCreated extends ApiKeyItem {
  /** 完整密钥，仅创建这一次响应返回 */
  key: string
}

function mockItems(): ApiKeyItem[] {
  const now = Math.floor(Date.now() / 1000)
  return [
    { id: 1, name: 'NAS 自动化脚本', key_prefix: 'pk_AUqr8', last_used_at: now - 3600, created_at: now - 86400 * 12, is_active: true },
    { id: 2, name: '测试用', key_prefix: 'pk_KuJg2', last_used_at: now - 86400 * 3, created_at: now - 86400 * 15, is_active: false },
  ]
}

export function listApiKeys(): Promise<{ items: ApiKeyItem[] }> {
  if (USE_MOCK) return mockDelay({ items: mockItems() })
  return get<{ items: ApiKeyItem[] }>('/apikeys')
}

export function createApiKey(name: string): Promise<ApiKeyCreated> {
  if (USE_MOCK) return mockDelay({ ...mockItems()[0], key: 'pk_Mock0123456789012345678' })
  return post<ApiKeyCreated>('/apikeys', { name })
}

export function setApiKeyStatus(id: number, is_active: boolean): Promise<{ ok: boolean }> {
  if (USE_MOCK) return mockDelay({ ok: true })
  return put<{ ok: boolean }>(`/apikeys/${id}/status`, { is_active })
}

export function deleteApiKey(id: number): Promise<{ ok: boolean }> {
  if (USE_MOCK) return mockDelay({ ok: true })
  return del<{ ok: boolean }>(`/apikeys/${id}`)
}
