import { defineStore } from 'pinia';
import { del, get, post, put, USE_MOCK } from '@/api/http';
const KEY = 'pk-auth';
function load(): string {
    try {
        return localStorage.getItem(KEY) || '';
    }
    catch {
        return '';
    }
}
function usernameFromToken(token: string): string {
    if (!token)
        return '';
    try {
        const part = (token.split('.')[1] || '').replace(/-/g, '+').replace(/_/g, '/');
        const bytes = Uint8Array.from(atob(part), (c) => c.charCodeAt(0));
        const payload = JSON.parse(new TextDecoder().decode(bytes));
        return typeof payload.sub === 'string' ? payload.sub : '';
    }
    catch {
        return '';
    }
}
export const useAuthStore = defineStore('auth', {
    state: () => {
        const token = load();
        return {
            token,
            username: usernameFromToken(token),
            avatar: '',
        };
    },
    getters: {
        logged: (s) => !!s.token,
        initial: (s) => (s.username || '?').slice(0, 1).toUpperCase(),
    },
    actions: {
        async login(username: string, password: string) {
            if (USE_MOCK) {
                this.token = 'mock-token-' + Date.now();
                this.username = username || 'admin';
                try {
                    localStorage.setItem(KEY, this.token);
                }
                catch {
                }
                return;
            }
            const res = await post<{
                token: string;
                username: string;
            }>('/auth/login', { username, password });
            this.token = res.token;
            this.username = res.username || usernameFromToken(res.token);
            try {
                localStorage.setItem(KEY, this.token);
            }
            catch {
            }
            await this.loadAvatar();
        },
        logout() {
            this.token = '';
            this.username = '';
            this.avatar = '';
            try {
                localStorage.removeItem(KEY);
            }
            catch {
            }
        },
        async loadAvatar() {
            if (USE_MOCK || !this.token)
                return;
            try {
                const res = await get<{
                    data: string;
                }>('/settings/avatar');
                this.avatar = res?.data || '';
            }
            catch {
                this.avatar = '';
            }
        },
        async saveAvatar(dataUrl: string) {
            if (USE_MOCK) {
                this.avatar = dataUrl;
                return;
            }
            await put('/settings/avatar', { data: dataUrl });
            this.avatar = dataUrl;
        },
        async removeAvatar() {
            if (USE_MOCK) {
                this.avatar = '';
                return;
            }
            await del('/settings/avatar');
            this.avatar = '';
        },
    },
});
