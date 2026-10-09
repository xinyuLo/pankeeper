import { reactive } from 'vue';
import type { AccountInfo, AccountSummary, MainDriveType } from '@/types/model';
export interface AccountRow extends AccountInfo {
    id: number;
    is_default?: boolean;
    nickname: string;
    alias: string;
    short: string;
    color: string;
    note: string;
    summary?: AccountSummary | null;
}
export const accountStore = reactive<{
    accounts: AccountRow[];
}>({
    accounts: [
        { id: 1, type: 'baidu', alias: '', short: '百度', color: '#1677ff', cred_kind: 'Cookie', status: 'connected', last_check: '09-27 01:12', notify: true, nickname: '', note: '百度转存链路已跑通' },
        { id: 2, type: 'quark', alias: '', short: '夸克', color: '#13c2c2', cred_kind: 'Cookie', status: 'expired', last_check: '09-22 18:40', notify: true, nickname: '', note: '夸克转存链路已打通' },
        { id: 3, type: '115', alias: '', short: '115', color: '#722ed1', cred_kind: 'Cookie / 扫码', status: 'unset', last_check: '从未配置', notify: true, nickname: '', note: 'p115client，支持扫码登录与自动续期' },
    ],
});
export function firstAccountOf(type: MainDriveType): AccountRow | undefined {
    return accountStore.accounts.find((a) => a.type === type);
}
export const ACCOUNT_STATUS_VIEW: Record<string, {
    cls: 't-ok' | 't-bad' | 't-off';
    label: string;
    dot: string;
}> = {
    connected: { cls: 't-ok', label: '正常', dot: 'dot-ok' },
    expired: { cls: 't-bad', label: '已失效', dot: 'dot-bad' },
    unset: { cls: 't-off', label: '未配置', dot: 'dot-off' },
};
