import { reactive } from 'vue'
import type { AccountInfo, MainDriveType } from '@/types/model'

/**
 * 网盘连接 mock —— 原型 _shell.html accs 数据移植（接口只回状态不回明文）。
 * status: ok=已连接 / bad=凭据已失效 / off=未配置（AccountInfo.status 的展示映射在视图层做）。
 */
export interface AccountRow extends AccountInfo {
  /** 默认目标目录（后端 base_dir 设置，卡片可编辑） */
  base_dir?: string
  short: string
  color: string
  base: string
  note: string
}

export const accountStore = reactive<{
  accounts: Record<MainDriveType, AccountRow>
}>({
  accounts: {
    baidu: {
      type: 'baidu', short: '百度', color: '#1677ff', cred_kind: 'BDUSS / STOKEN',
      status: 'connected', last_check: '09-27 01:12', base: '/影视',
      note: '复用你在 bdsavepro 里跑通的 storage.py 逻辑',
    },
    quark: {
      type: 'quark', short: '夸克', color: '#13c2c2', cred_kind: 'Cookie',
      status: 'expired', last_check: '09-22 18:40', base: '/剧集',
      note: '接口参考 quark-auto-save 的实现',
    },
    '115': {
      type: '115', short: '115', color: '#722ed1', cred_kind: 'Cookie / 扫码',
      status: 'unset', last_check: '从未配置', base: '—',
      note: 'p115client，支持扫码登录与自动续期',
    },
  },
})

export const ACCOUNT_STATUS_VIEW: Record<string, { cls: 't-ok' | 't-bad' | 't-off'; label: string; dot: string }> = {
  connected: { cls: 't-ok', label: '正常', dot: 'dot-ok' },
  expired: { cls: 't-bad', label: '已失效', dot: 'dot-bad' },
  unset: { cls: 't-off', label: '未配置', dot: 'dot-off' },
}
