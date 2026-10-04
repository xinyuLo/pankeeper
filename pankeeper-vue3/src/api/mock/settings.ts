import { reactive } from 'vue'

/* =====================================================================
 * 系统设置 mock（搜索源 / 推送通知 / QMS 联动 / 账号安全 四个 tab 的表单值）
 * 内存态 reactive：会话内可变、刷新重置，与原型一致。
 * 页面私有类型放这里（只有设置页用，不进 types/model.ts）。
 * ===================================================================== */

/** 结果缓存选项值：开启（30 分钟）或关闭 */
export type SearchCacheMode = 'on' | 'off'

/** 会话有效期：天 */
export type SessionDays = 1 | 7 | 30

/** ===== tab1 搜索源 ===== */
export interface SearchSrcCfg {
  pansou_url: string
  /** 请求超时（秒） */
  timeout: number
  cache_mode: SearchCacheMode
  /** 搜索频道白名单（空 = 使用 pansou 全部频道） */
  channels: string[]
}

/** ===== tab2 推送通知 ===== */
export interface NotifyCfg {
  enabled: boolean
  /** Server 酱 SendKey（mock 明文存内存；真实系统加密存储、接口不回填明文） */
  sendkey: string
  webhook: string
  /** 推送时机三项：自动转存 / 搜索转存 / 凭据过期告警 */
  on_auto: boolean
  on_search: boolean
  on_cred: boolean
}

/** ===== tab3 QMS 联动（全局连接参数；目录关联已迁到「转存配置」与自动转存任务弹窗） ===== */
export interface QmsCfg {
  enabled: boolean
  url: string
  apikey: string
  /** TMDB v3 api_key：转存完成的富文本推送（封面/剧照）用它查 TMDB */
  tmdb_api_key: string
  /** TMDB 代理（http://host:port）：NAS 直连 api.themoviedb.org 不通时填 */
  tmdb_proxy: string
  /** 触发动作：刮削后生成 STRM / 完成后刷新 Emby */
  act_strm: boolean
  act_emby: boolean
}

/** ===== tab4 账号安全 ===== */
export interface SecurityCfg {
  username: string
  /** 会话有效期（天） */
  session_days: SessionDays
}

export interface SettingsData {
  search: SearchSrcCfg
  notify: NotifyCfg
  qms: QmsCfg
  security: SecurityCfg
  /** 联动后端：qms = QMS 全流程 / litepan = 推送消息给 LitePan（预留） */
  media: { backend: 'qms' | 'litepan' }
}

export const settingsStore = reactive<SettingsData>({
  search: {
    pansou_url: 'http://192.168.2.77:8028',
    timeout: 30,
    cache_mode: 'on',
    channels: [],
  },
  notify: {
    enabled: true,
    sendkey: 'SCT123456abcdefg',
    webhook: '',
    on_auto: true,
    on_search: true,
    on_cred: true,
  },
  qms: {
    enabled: true,
    url: 'http://192.168.2.77:8020',
    apikey: '****-****-****',
    tmdb_api_key: '',
    tmdb_proxy: '',
    act_strm: true,
    act_emby: true,
  },
  security: {
    username: 'admin',
    session_days: 7,
  },
  media: { backend: 'qms' as 'qms' | 'litepan' },
})
