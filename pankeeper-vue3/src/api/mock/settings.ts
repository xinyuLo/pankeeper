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
  /** TMDB v3 api_key：转存完成的富文本推送（封面/剧照）用它查 TMDB（代理配置 tab） */
  tmdb_api_key: string
  /** TMDB 连通模式：proxy=HTTP 代理 / host=按 hosts 表直连指定 IP */
  tmdb_mode: 'proxy' | 'host'
  /** 代理模式的代理地址（http://host:port），留空直连 */
  tmdb_proxy: string
  /** host 模式的域名→IP 表（同 hosts 文件；现消费方 api.themoviedb.org，见后端 tmdb.py） */
  tmdb_hosts: TmdbHostEntry[]
  /** host 模式跳过 HTTPS 证书校验（自建反代/中转证书对不上域名时开） */
  tmdb_skip_tls: boolean
  /** 触发动作：刮削后生成 STRM / 完成后刷新 Emby */
  act_strm: boolean
  act_emby: boolean
}

/** host 模式的一行映射：左 IP 右域名（空行保存/查找时忽略） */
export interface TmdbHostEntry {
  ip: string
  host: string
}

/** ===== tab3 LitePan 对接（联动后端=litepan 时生效：HTTP Webhook 推转存完成消息） ===== */
export interface LitePanCfg {
  /** 总闸：关 = 所有目录的 LitePan 联动都不推送 */
  enabled: boolean
  /** 完整 Webhook 地址（填 LitePan 基地址即可，后端自动补 webhook 路径） */
  webhook_url: string
  /** LitePan API Key（真实系统加密存储、接口只回掩码）。事件名按目录/任务配，没填不联动 */
  apikey: string
  /** 全局通知来源（source）：填了才随事件传（LitePan 规则按 source 大小写敏感精确匹配），留空不传 */
  source: string
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
  litepan: LitePanCfg
  security: SecurityCfg
  /** 联动后端：qms = QMS 全流程 / litepan = 转存完 Webhook 推给 LitePan，整理由其自理 */
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
    tmdb_mode: 'proxy' as 'proxy' | 'host',
    tmdb_proxy: '',
    tmdb_hosts: [],
    tmdb_skip_tls: false,
    act_strm: true,
    act_emby: true,
  },
  litepan: {
    enabled: true,
    webhook_url: 'http://192.168.2.77:8030/api/open/automation/events',
    apikey: '****-****-****',
    source: 'PanKeeper',
  },
  security: {
    username: 'admin',
    session_days: 7,
  },
  media: { backend: 'qms' as 'qms' | 'litepan' },
})
