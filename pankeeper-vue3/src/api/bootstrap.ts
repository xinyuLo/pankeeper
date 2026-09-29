/**
 * 数据灌注：真实模式把后端数据灌进各 reactive store（视图层照旧读 store，零改动）。
 * 触发时机：①App 启动且已登录；②登录成功后（启动时未登录会 401，必须补一次）。
 */
import { USE_MOCK } from './http'
import { listDdItems } from './modules/dd'
import { listAccounts } from './modules/accounts'
import { listPaTasks } from './modules/tasks'

export function hydrateAll() {
  if (USE_MOCK) return
  listDdItems().catch(() => {})
  listAccounts().catch(() => {})
  listPaTasks().catch(() => {})
}
