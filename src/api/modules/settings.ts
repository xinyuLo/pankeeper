/**
 * 系统设置领域 API —— mock 实现。
 * 后端就绪后：每个函数把 mockDelay(...) 换成 http 调用（端点写在 TODO 注释里）。
 * 注意：SendKey/API Key 真实系统须加密存储、接口只回掩码，不回填明文。
 */
import { mockDelay } from '../http'
import { settingsStore } from '../mock/settings'
import type { SettingsData, SessionDays } from '../mock/settings'

/** 读取全部设置（四个 tab 一把抓，表单值少没必要拆接口） */
export function getSettings(): Promise<SettingsData> {
  // TODO 后端: GET /api/settings
  return mockDelay<SettingsData>({
    search: { ...settingsStore.search },
    notify: { ...settingsStore.notify },
    qms: { ...settingsStore.qms },
    security: { ...settingsStore.security },
  })
}

/** 保存搜索源配置 */
export function saveSearchSrc(cfg: SettingsData['search']): Promise<void> {
  // TODO 后端: PUT /api/settings/search
  Object.assign(settingsStore.search, cfg)
  return mockDelay(undefined)
}

/** 保存推送通知配置 */
export function saveNotify(cfg: SettingsData['notify']): Promise<void> {
  // TODO 后端: PUT /api/settings/notify
  Object.assign(settingsStore.notify, cfg)
  return mockDelay(undefined)
}

/** 保存 QMS 联动配置 */
export function saveQms(cfg: SettingsData['qms']): Promise<void> {
  // TODO 后端: PUT /api/settings/qms
  Object.assign(settingsStore.qms, cfg)
  return mockDelay(undefined)
}

/** 测试 PanSou 连通，返回响应耗时（毫秒） */
export function testPansou(url: string): Promise<{ ok: boolean; ms: number }> {
  // TODO 后端: POST /api/settings/search/test  body: { url }
  const ms = 120 + Math.round(Math.random() * 40) // 演示：130ms 上下浮动
  void url
  return mockDelay({ ok: true, ms })
}

/** 发送 Server 酱测试消息 */
export function testSendkey(sendkey: string): Promise<void> {
  // TODO 后端: POST /api/settings/notify/test  body: { sendkey }
  void sendkey
  return mockDelay(undefined, 400)
}

/** 测试 QMS 连接 */
export function testQms(url: string): Promise<void> {
  // TODO 后端: POST /api/settings/qms/test  body: { url }
  void url
  return mockDelay(undefined, 300)
}

/** 查看推送历史（最近 50 条的投递结果） */
export function pushHistory(): Promise<{ delivered: number; failed: number }> {
  // TODO 后端: GET /api/notify/history?limit=50
  return mockDelay({ delivered: 128, failed: 2 })
}

/** 修改密码 + 会话有效期 */
export function saveSecurity(payload: {
  old_password: string
  new_password: string
  session_days: SessionDays
}): Promise<void> {
  // TODO 后端: PUT /api/settings/security
  settingsStore.security.session_days = payload.session_days
  return mockDelay(undefined, 300)
}
