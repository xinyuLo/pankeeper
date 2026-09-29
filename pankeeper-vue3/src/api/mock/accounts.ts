import { reactive } from 'vue'
import type { AccountInfo, AccountSummary, MainDriveType } from '@/types/model'

/**
 * 网盘连接 store —— 多账号（每平台可多行，同时在线）。
 * 真实模式下由 modules/accounts.ts 的 listAccounts() 灌入后端数据。
 * status: ok=已连接 / bad=凭据已失效 / off=未配置（展示映射在视图层做）。
 */
export interface AccountRow extends AccountInfo {
  /** 账号 id（后端主键，凭据/检测/通知/摘要都按它） */
  id: number
  /** 网盘昵称（探活时后端取回） */
  nickname: string
  /** 别名（用户起，如「百度-大号」）；空则展示用昵称 */
  alias: string
  short: string
  color: string
  note: string
  /** 容量/会员摘要缓存（后端持久化，刷新页面即可立现） */
  summary?: AccountSummary | null
}

export const accountStore = reactive<{
  /** 多账号列表（跨平台平铺，按 type + id 排） */
  accounts: AccountRow[]
}>({
  accounts: [
    { id: 1, type: 'baidu', alias: '', short: '百度', color: '#1677ff', cred_kind: 'Cookie', status: 'connected', last_check: '09-27 01:12', notify: true, nickname: '', note: '复用你在 bdsavepro 里跑通的 storage.py 逻辑' },
    { id: 2, type: 'quark', alias: '', short: '夸克', color: '#13c2c2', cred_kind: 'Cookie', status: 'expired', last_check: '09-22 18:40', notify: true, nickname: '', note: '接口参考 quark-auto-save 的实现' },
    { id: 3, type: '115', alias: '', short: '115', color: '#722ed1', cred_kind: 'Cookie / 扫码', status: 'unset', last_check: '从未配置', notify: true, nickname: '', note: 'p115client，支持扫码登录与自动续期' },
  ],
})

/** 每平台第一个账号（首页单卡展示 / 尚未接账号维度的组件过渡用） */
export function firstAccountOf(type: MainDriveType): AccountRow | undefined {
  return accountStore.accounts.find((a) => a.type === type)
}

export const ACCOUNT_STATUS_VIEW: Record<string, { cls: 't-ok' | 't-bad' | 't-off'; label: string; dot: string }> = {
  connected: { cls: 't-ok', label: '正常', dot: 'dot-ok' },
  expired: { cls: 't-bad', label: '已失效', dot: 'dot-bad' },
  unset: { cls: 't-off', label: '未配置', dot: 'dot-off' },
}
