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

/** 测试 PanSou 连通。返回响应耗时；失败时 ok=false + message（HTTP 仍是 200） */
export async function testPansou(url: string): Promise<{ ok: boolean; ms: number; message?: string }> {
  if (USE_MOCK) {
    void url
    return mockDelay({ ok: true, ms: 120 + Math.round(Math.random() * 40) })
  }
  return post<{ ok: boolean; ms: number }>('/settings/search/test', { url })
}

/** 发送 Server 酱测试消息。后端发送失败也是 200 + {ok:false,message}，调用方必须看 ok */
export async function testSendkey(sendkey: string): Promise<{ ok: boolean; message?: string }> {
  if (USE_MOCK) {
    void sendkey
    return mockDelay({ ok: true, message: '（mock）测试消息已发送' }, 400)
  }
  return post<{ ok: boolean; message?: string }>('/settings/notify/test', { sendkey })
}

/** 测试 QMS 连接，返回连通结果（ok/message 直接来自后端，供调用方判断）。
 *  url/apikey 传「输入框正在编辑的值」——不传则后端回落到已保存配置。 */
export async function testQms(url: string, apikey: string): Promise<{ ok: boolean; message?: string }> {
  if (USE_MOCK) {
    void url
    void apikey
    return mockDelay({ ok: true, message: 'QMS 连接正常' }, 300)
  }
  return post<{ ok: boolean; message?: string }>('/settings/qms/test', { url, apikey })
}

/** QMS 引擎状态胶囊（设置页用，语义同 /search/health；按已保存配置测） */
export function getQmsHealth(): Promise<{ ok: boolean; message?: string }> {
  if (USE_MOCK) return mockDelay({ ok: true, message: '在线' }, 200)
  return get<{ ok: boolean; message?: string }>('/qms/health')
}

/** 推送历史行（push_logs 快照，一次投递一行） */
export interface PushLogRow {
  id: number
  ts: string
  title: string
  kind: string
  status: 'success' | 'fail'
  error: string
}

/** 推送历史（推送历史页数据源）：items 按时间倒序 + 窗口内成败计数 */
export function getPushLogs(limit = 100): Promise<{ items: PushLogRow[]; delivered: number; failed: number }> {
  if (USE_MOCK) return mockDelay({ items: [], delivered: 0, failed: 0 })
  return get<{ items: PushLogRow[]; delivered: number; failed: number }>('/notify/history', { params: { limit } })
}

/** 修改用户名 + 密码 + 会话有效期。后端同一端点：new_password 为空则只更新用户名/会话 */
export async function saveSecurity(payload: {
  username: string
  old_password: string
  new_password: string
  session_days: SessionDays
}): Promise<void> {
  if (USE_MOCK) {
    settingsStore.security.username = payload.username
    settingsStore.security.session_days = payload.session_days
    return mockDelay(undefined, 300)
  }
  await put('/settings/security', {
    username: payload.username,
    old_password: payload.old_password,
    new_password: payload.new_password,
    session_days: payload.session_days,
  })
}
