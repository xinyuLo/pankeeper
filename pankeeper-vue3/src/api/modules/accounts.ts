import { del, get, mockDelay, post, put, USE_MOCK } from '../http';
import { accountStore, type AccountRow } from '../mock/accounts';
import type { AccountStatus, AccountSummary, MainDriveType } from '@/types/model';
export type { AccountSummary };
export function getSummary(accId: number): Promise<AccountSummary> {
    if (USE_MOCK) {
        return mockDelay({ capacity: null, vip: null });
    }
    return get<AccountSummary>(`/accounts/${accId}/summary`);
}
export function listAccounts(): Promise<AccountRow[]> {
    if (USE_MOCK)
        return mockDelay(accountStore.accounts);
    return get<AccountRow[]>('/accounts').then((rows) => {
        accountStore.accounts.splice(0, accountStore.accounts.length, ...rows.map((r) => ({
            ...r,
            ...(accountStore.accounts.find((x) => x.type === r.type && x.short === r.short) || {}),
            id: r.id,
            type: r.type as MainDriveType,
            alias: r.alias || '',
            status: r.status,
            nickname: r.nickname,
            last_check: r.last_check,
            notify: !!r.notify,
            is_default: !!(r as {
                is_default?: boolean;
            }).is_default,
            summary: r.summary,
        })));
        return accountStore.accounts;
    });
}
export interface CheckResult {
    ok: boolean;
    kind: 'success' | 'error' | 'warning';
    message: string;
    status: AccountStatus;
    last_check: string;
}
export function checkAccount(accId: number): Promise<CheckResult> {
    if (USE_MOCK) {
        const row = accountStore.accounts.find((a) => a.id === accId);
        if (!row || row.status === 'unset') {
            return mockDelay({ ok: false, kind: 'warning', message: '尚未配置凭据，请先「配置凭据」', status: 'unset', last_check: '从未配置' });
        }
        if (row.status === 'expired') {
            return mockDelay({ ok: false, kind: 'error', message: '检测失败：Cookie 已过期，请重新配置', status: 'expired', last_check: row.last_check });
        }
        return mockDelay({ ok: true, kind: 'success', message: '连通正常', status: 'connected', last_check: row.last_check });
    }
    return post<CheckResult>(`/accounts/${accId}/check`);
}
export async function deleteAccount(accId: number): Promise<void> {
    if (USE_MOCK) {
        accountStore.accounts = accountStore.accounts.filter((a) => a.id !== accId);
        return mockDelay(undefined);
    }
    await del(`/accounts/${accId}`);
    await listAccounts();
}
export async function addAccount(type: MainDriveType, cookies: string, alias: string): Promise<{
    nickname: string;
}> {
    const { nickname } = await post<{
        ok: boolean;
        nickname: string;
    }>(`/accounts/${type}`, { cookies, alias });
    await listAccounts();
    return { nickname };
}
export async function saveCredential(accId: number, cookies: string, alias = ''): Promise<{
    nickname: string;
}> {
    const { nickname } = await put<{
        ok: boolean;
        nickname: string;
    }>(`/accounts/${accId}/credential`, { cookies, alias });
    await listAccounts();
    return { nickname };
}
export async function setAlias(accId: number, alias: string): Promise<void> {
    if (USE_MOCK)
        return mockDelay(undefined);
    await put(`/accounts/${accId}/alias`, { alias });
    await listAccounts();
}
export async function setDefaultAccount(accId: number): Promise<void> {
    if (USE_MOCK)
        return mockDelay(undefined);
    await put(`/accounts/${accId}/set-default`);
    await listAccounts();
}
export async function setDriveNotify(accId: number, enabled: boolean): Promise<void> {
    if (USE_MOCK) {
        const row = accountStore.accounts.find((a) => a.id === accId);
        if (row)
            row.notify = enabled;
        return mockDelay(undefined, 200);
    }
    await put(`/accounts/${accId}/notify`, { enabled });
}
export function getRootDirs(): Promise<Record<string, string>> {
    if (USE_MOCK)
        return mockDelay({});
    return get<Record<string, string>>('/accounts/root-dirs');
}
export function setRootDir(type: string, path: string): Promise<Record<string, string>> {
    if (USE_MOCK)
        return mockDelay({ [type]: path });
    return put<Record<string, string>>('/accounts/root-dirs', { type, path });
}
