# watch-movie — AI 一句话找片转存 Skill

把「我想看《xxx》」变成一句话的事：AI 帮你搜资源、挑 4K 正片、确认后调 PanKeeper 转存入队，后续队列、过滤、QMS 整理、完成通知推送（Server酱/Bark/Webhook，按你的配置）全部由 PanKeeper 自己完成。

也可以直接把网盘分享链接（百度 / 夸克 / 115）甩给 AI，验证后一键转存。

## 它能做什么

| 你说 | AI 做的事 |
| --- | --- |
| 「我想看冰川时代」 | 核实片名（TMDB 识别 + PanKeeper 历史）→ 搜索资源 → 4K 优先挑正片 → 和你确认 → 调 PanKeeper 转存入队 |
| 「冰河时代 1-3 都存了」 | 锁定片单，逐部串行搜索转存 |
| 「存夸克」 | 指定目标网盘；没指定时默认上次的盘 |
| 直接发 `pan.quark.cn/s/xxx` 分享链接 | 验活 → 拉文件树 → 识别片名 → 确认 → 转存 |

挑片规则与 PanKeeper 前端「快速转存」完全同款：建壳转存（`with_shell` + 标准名 rename，QMS 靠壳名刮削）、电影只转最大的 4K 正片、电视节目按集数核对挑 4K、TMDB 验证不过的宁可不带年份。内置限流保护（串行 + 间隔 ≥3 秒 + 自动退避），不会触发网盘风控。

## 安装

1. 把整个 `watch-movie/` 文件夹放进 AI 助手的 skills 目录：
   - 用户级：`~/.zcode/skills/`（所有项目可用）
   - 项目级：`.zcode/skills/`（仅当前项目）
2. 重开会话即可。对 AI 说「我想看 xxx」或甩个分享链接就会触发。

依赖：仅 Python 3 标准库，无任何第三方包。

## 首次配置（问一次就记住）

首次使用时 AI 会依次向你要一次，存进同目录的 `pk_config.json`（600 权限），之后所有会话直接用：

```bash
python3 <skills目录>/watch-movie/pk.py config base_url http://<PanKeeper主机IP>:8031   # 必须
python3 <skills目录>/watch-movie/pk.py config api_key pk_xxxxxxxx                      # API Key（推荐）
python3 <skills目录>/watch-movie/pk.py config tmdb_proxy http://127.0.0.1:7890         # 可选：代理（与下一项二选一）
python3 <skills目录>/watch-movie/pk.py config tmdb_ip 162.159.140.227                  # 可选：TMDB 映射 IP
python3 <skills目录>/watch-movie/pk.py config                                          # 查看配置（敏感项打码）
```

- **base_url**：PanKeeper 的地址（默认端口 8031）。
- **api_key**：在 PanKeeper 系统设置里生成的 API Key；没有的话 AI 会退而问账号密码（走 JWT 登录）。
- **tmdb_proxy / tmdb_ip**：选填，电视节目集数核对时直连 TMDB 用；都没配也能跑（集数改为按文件名推断）。

## 版本要求

需要 PanKeeper 支持 **X-API-Key 鉴权**与 **`/api/queue/tasks` 建壳转存**（2026-10-11 之后的版本，v1.0.8+）。

## 隐私说明

- 仓库里的 `pk_config.json` / `state.json` 是**全空的模板**，不含任何真实地址、密钥、令牌。
- 你配置的 API Key、密码、登录令牌只存在本机 skill 目录里（`pk_config.json` 建议 600 权限，`state.json` 存临时令牌），不会被上传到任何地方。
- AI 与 PanKeeper 的全部交互都发生在你自己的网络内。

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `SKILL.md` | AI 读取的执行手册（SOP）：入口判断、限流铁律、逐流程规则 |
| `pk.py` | 唯一的接口调用出口：preflight / history / search / recognize / check / files / enqueue / config |
| `pk_config.json` | 用户配置模板（地址、密钥、TMDB 可选项） |
| `state.json` | 运行时状态（登录令牌缓存、上次使用的网盘） |
