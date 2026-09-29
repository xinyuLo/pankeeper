/**
 * 系统设置领域 API —— 双模式。
 * 注意：SendKey/API Key 加密存储、接口只回掩码；前端回传 `****` 开头的值时后端保留旧值。
 */
import { get, mockDelay, post, put, USE_MOCK } from '../http'
import { settingsStore, type SettingsData, type SessionDays } from '../mock/settings'

/** 读取全部设置（四个 tab 一把抓，表单值少没必要拆接口） */
export function getSettings(): Promise<SettingsData> {
  if (USE_MOCK) {
    return mockDelay<SettingsData>({
      search: { ...settingsStore.search },
      notify: { ...settingsStore.notify },
      qms: { ...settingsStore.qms },
      security: { ...settingsStore.security },
    })
  }
  return get<SettingsData>('/settings')
}

/** 保存搜索源配置 */
export async function saveSearchSrc(cfg: SettingsData['search']): Promise<void> {
  if (USE_MOCK) {
    Object.assign(settingsStore.search, cfg)
    return mockDelay(undefined)
  }
  await put('/settings/search', cfg)
}

/** 保存推送通知配置 */
export async function saveNotify(cfg: SettingsData['notify']): Promise<void> {
  if (USE_MOCK) {
    Object.assign(settingsStore.notify, cfg)
    return mockDelay(undefined)
  }
  await put('/settings/notify', cfg)
}

/** 保存 QMS 联动配置 */
export async function saveQms(cfg: SettingsData['qms']): Promise<void> {
  if (USE_MOCK) {
    Object.assign(settingsStore.qms, cfg)
    return mockDelay(undefined)
  }
  await put('/settings/qms', cfg)
}

/** 测试 PanSou 连通，返回响应耗时（毫秒） */
export async function testPansou(url: string): Promise<{ ok: boolean; ms: number }> {
  if (USE_MOCK) {
    void url
    return mockDelay({ ok: true, ms: 120 + Math.round(Math.random() * 40) })
  }
  return post<{ ok: boolean; ms: number }>('/settings/search/test', { url })
}

/** 发送 Server 酱测试消息 */
export async function testSendkey(sendkey: string): Promise<void> {
  if (USE_MOCK) {
    void sendkey
    return mockDelay(undefined, 400)
  }
  await post('/settings/notify/test', { sendkey })
}

/** 测试 QMS 连接 */
export async function testQms(url: string): Promise<void> {
  if (USE_MOCK) {
    void url
    return mockDelay(undefined, 300)
  }
  await post('/settings/qms/test', { url })
}

/** 查看推送历史（最近 50 条的投递结果） */
export function pushHistory(): Promise<{ delivered: number; failed: number }> {
  if (USE_MOCK) return mockDelay({ delivered: 128, failed: 2 })
  return get<{ delivered: number; failed: number }>('/notify/history', { params: { limit: 50 } })
}

/** 修改密码 + 会话有效期。后端同一端点：new_password 为空则只更新会话/用户名 */
export async function saveSecurity(payload: {
  old_password: string
  new_password: string
  session_days: SessionDays
}): Promise<void> {
  if (USE_MOCK) {
    settingsStore.security.session_days = payload.session_days
    return mockDelay(undefined, 300)
  }
  await put('/settings/security', {
    username: 'admin',
    old_password: payload.old_password,
    new_password: payload.new_password,
    session_days: payload.session_days,
  })
}
