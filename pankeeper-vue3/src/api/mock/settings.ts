import { reactive } from 'vue';
export type SearchCacheMode = 'on' | 'off';
export type SessionDays = 1 | 7 | 30;
export interface SearchSrcCfg {
    pansou_url: string;
    timeout: number;
    cache_mode: SearchCacheMode;
    channels: string[];
}
export interface NotifyCfg {
    enabled: boolean;
    sendkey: string;
    webhook: string;
    on_auto: boolean;
    on_search: boolean;
    on_cred: boolean;
}
export interface QmsCfg {
    enabled: boolean;
    url: string;
    apikey: string;
    tmdb_api_key: string;
    tmdb_mode: 'proxy' | 'host';
    tmdb_proxy: string;
    tmdb_hosts: TmdbHostEntry[];
    tmdb_skip_tls: boolean;
    act_strm: boolean;
    act_emby: boolean;
}
export interface TmdbHostEntry {
    ip: string;
    host: string;
}
export interface LitePanCfg {
    enabled: boolean;
    webhook_url: string;
    apikey: string;
    source: string;
}
export interface SecurityCfg {
    username: string;
    session_days: SessionDays;
}
export interface SettingsData {
    search: SearchSrcCfg;
    notify: NotifyCfg;
    qms: QmsCfg;
    litepan: LitePanCfg;
    security: SecurityCfg;
    media: {
        backend: 'qms' | 'litepan';
    };
}
export const settingsStore = reactive<SettingsData>({
    search: {
        pansou_url: '',
        timeout: 30,
        cache_mode: 'on',
        channels: [],
    },
    notify: {
        enabled: false,
        sendkey: '',
        webhook: '',
        on_auto: true,
        on_search: true,
        on_cred: true,
    },
    qms: {
        enabled: true,
        url: '',
        apikey: '****-****-****',
        tmdb_api_key: '',
        tmdb_mode: 'proxy' as 'proxy' | 'host',
        tmdb_proxy: '',
        tmdb_hosts: [],
        tmdb_skip_tls: false,
        act_strm: true,
        act_emby: true,
    },
    litepan: {
        enabled: true,
        webhook_url: '',
        apikey: '****-****-****',
        source: 'PanKeeper',
    },
    security: {
        username: 'admin',
        session_days: 7,
    },
    media: { backend: 'qms' as 'qms' | 'litepan' },
});
