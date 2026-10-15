---
name: watch-movie
metadata:
  author: xinyuLo
  version: "2.0"
description: "帮用户搜索影视资源并转存到网盘（百度/夸克/115，走 NAS 上的 pankeeper），也支持用户直接甩分享链接转存。当用户说「我想看/我要看 xxx 电影/电视剧/综艺/动漫」「帮我找/存 xxx 资源」「把 xxx 转存到网盘」，或直接发来 pan.baidu.com / pan.quark.cn / 115 分享链接让转存时触发。流程：核实片名（结合 pankeeper 历史与 TMDB 识别）→ 确认电影还是电视节目 → 搜索资源（或用用户给的链接）→ 4K 优先挑正片 → 用户确认 → 调 pankeeper AI 接口转存入队。转存后由 pankeeper 自行完成队列、过滤、QMS 整理和完成通知推送（通道按用户设置：Server酱/Bark/Webhook）。"
---

# watch-movie — 找片转存 SOP

替用户跑通「搜资源 → 转存入队」的前置流程。转存入队成功即交接完成，**后续（队列/过滤/QMS/通知推送）pankeeper 自己跑，skill 不跟踪不轮询**。结束时汇报：存了什么、存哪个盘、结果看哪个通道的推送（通道用 `notify-channel` 查询，别写死）。

**先判入口**：用户消息里带分享链接（pan.baidu.com / pan.quark.cn / 115 等网盘域名）→ 走 **入口 B：链接直转**（第 10 节）；只有片名 → 走默认的搜索流程（第 1-7 节）。

所有接口调用一律通过 `pk.py`（与本文件同目录），只调 pankeeper 的 **AI 专用层 `/api/ai/*`**（2026-10-15 起 API Key 仅允许访问这一层，其他口子对 Key 一律 403）：

```bash
PK=<skills目录>/watch-movie/pk.py   # 即本 SKILL.md 同目录的 pk.py
python3 $PK preflight          # 前置检查（一次拿全：账号+转存配置）
python3 $PK history            # 最近搜索词+转存记录
python3 $PK search "冰川时代" --drives baidu,quark   # 搜索（限流；drives=preflight 已配置的盘，服务端过滤分组）
python3 $PK recognize "冰川时代" # 识别片名→TMDB（=前端「识别」按钮）
python3 $PK check baidu "https://pan.baidu.com/s/xxx" "xxxx"   # 死活预检（限流）
python3 $PK files quark "https://pan.quark.cn/s/xxx" "xxxx"    # 文件树（限流）
echo '{...payload...}' | python3 $PK transfer                  # 转存入队（限流，服务端判断闸）
python3 $PK tmdb-season 12345 1   # 某季集数（走服务端 TMDB 配置，限流）
```

## ⚠️ 限流铁律（最高优先级）

`pk.py` 已在代码层强制：search/check/files/transfer/tmdb-season 全部串行、间隔 ≥3 秒、405/429 自动退避 30-60s 并翻倍间隔。你要做的：
1. **绝不绕过 pk.py 直接 curl pankeeper 的网盘类接口**；
2. **绝不并发**调用 pk.py（一次一个 bash 调用，等上一个返回再发下一个）；
3. 看到 pk.py 输出「限流退避」就耐心等，**宁可慢不能快**。

## ⚠️ 防推送轰炸铁律（2026-10-15 定案，与限流同级）

转存入队的唯一入口是 `transfer`——服务端判断闸（探活/提取码/勾选校验）全过才入队，被拒**不会产生失败推送**。你要做的：
1. **绝不绕过 transfer 直调其他入队接口**（绕过闸 = 坏链进队列 = 每次失败推一条通知轰炸用户手机）；
2. **同一条链接被 transfer 拒绝后，绝不原样重试**——要么按 reason 分支处理（见第 6 步），要么换候选/结束；
3. `pos: -1` = 该链接已在队列，**不是失败**，直接结束本候选，不要换候选重试。

## 第 0 步：前置检查

跑 `preflight`，拿到：
- `drives[]`：已配置网盘（type/acc_id/alias/status/is_default/dd_configured/dd_paths）。**搜索结果只保留这里的 type**；status 非 connected 的盘直接报告用户，不参与转存。
- 全部盘都 `dd_configured=false` → 停止，提示用户先去 pankeeper「转存配置」里配好目标目录/QMS 配对，配好再跑。

## 第 1 步：片名核实 + 类型确认

1. `history` 看最近搜索词和转存记录判断语境（今天刚转过《冰川时代4》，用户说"冰河时代123 3部" → 要的是《冰川时代》1-3）。
2. 上网搜索用户说的片名，拿标准译名/原名/年份；再跑 `recognize "用户说的名字"`（返回 `media_name` 即「标题 (年份)」格式、`candidates`、`confident`）。
3. **类型二分类**：只有「电影」和「电视节目」两类，**除电影外全归电视节目**（剧集/综艺/动画/纪录片）。
4. **和你确认**：名字有更正 / 同名多候选（recognize doubt=true）/ 部数对不上 / 类型拿不准 → 必须列出让你确认；精确无歧义 → 一句话带过（「开始找《冰川时代》(2002)」）直接往下走。
5. 多部影片（如 1-3 部）→ 锁定成片单：`[{标准名, 年份, 季}]`，后面**逐部串行**跑第 2-6 步。

## 第 2 步：搜索

`search "确认后的片名" --drives baidu,quark`（drives = preflight 里已配置的盘，逗号拼接）——**搜索词就用确认好的名字，不带年份**（用户说"年会不能停2"就搜"年会不能停2"，搜"年会不能停！2 (2026)"结果会很少）。年份只在转存 `rename` 时拼进「标题 (年份)」，搜索阶段不用。

带 `--drives` 后服务端已按盘过滤分组，不用再自己筛：
- `storable[]`：已配置盘的候选，**只从这里挑**。字段：`n`(名称) `t`(盘类型) `d`(日期) `url` `share_code` `source`；
- `magnets[]`：磁力链接，**留着**（兜底规则见下）；
- `dropped`：未知盘丢弃条数（不用管）。

- 电视节目搜不到 → 降级重搜：`标准名 第X季` → `纯标准名`。
- 候选按名称打标排序（见下），同档新的优先。
- **兜底：`storable` 为空** → 停下问用户：「没找到能直接转存的资源，只有 X 条磁力链接，要吗？」要 → 把磁力链接列给用户（pankeeper 转存不支持磁力，需用户自行拿去网盘离线，如 115 离线下载）；不要 → 本部结束。

**质量打标（电影）**：第一排序键 4K/2160p 总是优先；4K 内部 杜比视界(DV) ≥ HDR10+ > HDR10 > SDR；来源 原盘REMUX > WEB-DL > WEBRip > HDTV；音轨 全景声/TrueHD/DTS-HD > 5.1 > 2.0；Cam/TS 枪版直接淘汰。
**电视节目**：门槛只有「是不是 4K」。

## 第 3 步：问网盘（能不问就不问）

- 只配了一个盘 → 直接用，不问。
- 用户指令已带盘名（如"存夸克"）→ 按用户说的。
- 多盘没说 → 问一次：「找到 N 个资源，4K 的 X 个：百度 a / 夸克 b。存哪个盘？」默认建议 = state.json 里的上次选择；用户可答顺序（"百度优先夸克其次"）作为降级查找顺序。**用户答复后更新 state.json 的 last_drive。**

## 第 4 步：挑文件（逐候选：check → files）

按盘顺序逐个候选（每个候选之间 pk.py 自动限流）：
1. `check <type> <url> <code>` → 按 `state` 分流：
   - `ok` 继续；`bad` 跳下一个候选；`uncertain/unknown` 放行（与 pankeeper 前端同策略）。
   - `locked` = **链接活着但要提取码**（2026-10-15 起后端真实现）：`has_code=false`（链接和搜索结果都没带码）→ 别急着跳候选，问用户一句「这个分享需要提取码，你有吗？」——用户给码就带码重跑 check/files 往下走，没有就跳下一个候选；`has_code=true` → 码不对，同样问用户核对。
2. `files <type> <url> <code>` 拉文件树。`files[]` 每项 `{path, name, is_dir, size(字节), md5}`。
3. **挑选规则（对齐用户手动习惯）**：
   - **同一部影片多版本混装**（DV/HDR/SDR/小体积等同片多版本）→ **直接挑最大的视频文件**（最大的通常就是 DV/最高档），不必再去比对其他候选或别的分享；文件名里没片名也没关系——建壳转存的壳名就是标准名，QMS 靠壳名刮削。
   - 多格式混装 → 挑最大的视频文件 = 4K 原片，大小接近时杜比优先。
   - 电影：**只转一个影视文件**（单文件 .mkv/.mp4）；杂物（nfo/海报/sample）不用管——pankeeper 转存配置已开「过滤其他文件」。
   - 大小复核：电影"4K"单文件 <10GB 按假 4K 降级；电视单集 <2GB 降级。
   - 电视节目：TMDB 核对（`recognize` 拿 tmdb_id → `tmdb-season` 拿每季集数，走服务端配置无需本机 key），按集挑 4K 正片（每集挑最大的），缺集/只有 1080p → 列情况问用户。彩蛋/花絮/预告/漫画/小说/sample 一律不勾。
   - 没把握（认不准目标文件/疑似枪版/年份对不上）→ **不自动选**，把候选拎出来让用户挑。

## 第 5 步：无 4K → 先问

该盘及备选盘翻完没 4K → 按打标排序选最高档，停下来问：「没有 4K，最高是《xxx》1080p REMUX，单文件 14GB，转这个吗？」（很老的电影直接推荐 1080p 最高档。）点头才转。

## 第 6 步：入队（一句话交接）

**所有转存一律带 `with_shell: true` + `rename`（与前端快速转存确认按钮完全同款，payload 即确认按钮的完整语义）**——按 rename 新建标准名壳文件夹、内容剥壳平铺转进去，配合用户已配的「转存时过滤其他文件」自动只进视频。**建壳分支支持 `file_paths` 勾选**（壳内只转勾选的文件）：

```json
{
  "name": "沙丘 2",
  "type": "quark",
  "share_url": "https://pan.quark.cn/s/xxxx",
  "share_code": "ab12",
  "path": "/影视/待整理-电影",
  "rename": "沙丘 2",
  "with_shell": true,
  "file_paths": ["沙丘2.4K.mkv"],
  "acc_id": 2
}
```

**服务端判断闸（transfer 内置，绕不过去）**：type 配过转存目录、path 是该盘已配置目录、acc_id 属于该 type、建壳必须带 rename → 探活（bad/locked 拒）→ 实拉清单（缺码在这显形）→ file_paths 逐条必须在清单里 → 全过才入队。**被拒不产生任何失败推送**，所以：
- **绝不原样重试被拒的 payload**（换个说法重试也是原样重试）；
- 每部影片最多 3 个候选，候选之间换链接是允许的。

**退出码与输出**：
- `{"ok": true, "pos": N}`（N≥1）→ 入队成功，N=当前队列位次 → **交接完成**。
- `{"ok": true, "pos": -1}` → **没有入队**：队列里已有同一个 `share_url` 的任务（等待中或执行中），防重复转存 → 这**不是失败**，向用户汇报「该链接已经在队列里了，不用重复转」，本部影片到此结束（不要换候选重试——链接已经在转了）。
- 退出码 2 + `{"ok": false, "reason": ..., "message": ...}` → 闸拒绝，**按 reason 分支**：

| reason | 含义 | 你的动作 |
| --- | --- | --- |
| `need_code` | 链接活着但要提取码（缺码或码错） | 向用户要提取码 → 带码重跑 `check`/`files` → 确认清单后重新 transfer（这不算原样重试：payload 的 share_code 变了）。用户没码 → 换下一候选 |
| `bad_link` | 死链/清单为空 | 换下一候选（≤3 个） |
| `file_missing` | 勾选的文件不在清单里 | 重跑 `files` 核对路径后再 transfer；还不对说明分享主改了文件，换候选 |
| `no_dd` / `bad_path` / `bad_acc` | 转存配置/参数问题 | **停下**，告诉用户去 pankeeper「转存配置」检查，不要自行换目录 |
| `no_rename` | 建壳没带壳名 | 补 rename（这是你自己的 payload 问题） |
| `cred_expired` / `adapter_error` | 网盘凭据过期/网盘接口异常 | 停下如实报告用户 |

- `rename`（壳文件夹名，QMS 靠它刮削）——年份必须先过 TMDB 验证：
  - recognize 返回 `ok: true` 的 `media_name`（「标题 (年份)」）本身就是 TMDB 搜出来的结果，直接用；
  - 用户纠正过片名、或 recognize `doubt: true` → 用纠正后的名字重跑 `recognize` 再验一次；
  - **TMDB 搜不到（ok=false）或条目没有年份 → `rename` 去掉年份，只存标题**（如「某综艺名义上的第一季」）。宁可没年份，也不要存一个 TMDB 上不存在的组合。
- **`file_paths` 省略或空数组 = 分享内容全转**（单影片分享就这样，所有版本都会进来——所以同片多版本混装**必须勾选**，见第 4 步：只勾最大的那个，否则 4 个版本全进来）。
- **`file_paths` 里的相对路径来自 `files` 命令输出的 `path` 字段**。
- 多部影片混装：`file_paths` 只勾目标影片的文件；电视节目：勾选中的 4K 正片各集（同名成对外挂字幕可一并勾），壳名=「剧名 (年份)」。
- `acc_id`：目标盘默认账号（preflight 里 is_default 的）；`path`：该盘 dd_paths 里的转存目标目录。

## 第 7 步：结束汇报（唯一输出格式）

「✅ 已提交转存：《冰川时代》(2002) 4K → 夸克网盘，rename=冰川时代 (2002)，队列位置 2。结果看 Bark 推送。」

**末句的推送通道不写死**：先跑 `python pk.py notify-channel`（返回 `{"ok":true,"channel":"bark","label":"Bark","configured":true}` 这类），按 `label` 说「结果看 {label} 推送」；
- `configured:false` → 加一句「{label} 通道还没配 Key，收不到的话去 PanKeeper 设置页补上」；
- `ok:false`（通知总开关没开）→ 不提推送，改说「结果可在 PanKeeper 记录页查看」。

多部影片逐行列出；有被跳过的候选（死链/缺码等）附一句原因。

## 首次配置（问一次就记住，以后不再问）

pk.py 报「pankeeper 地址未配置」或配置缺失时，按顺序向用户要一次，用 `config` 子命令存进 pk_config.json（600 权限），之后所有会话直接用：

```bash
python3 $PK config base_url http://192.168.1.100:8031   # ① pankeeper 的 IP+端口（必须，示例）
python3 $PK config api_key pk_xxxxxxxx                  # ② API Key（有就直接用，不再问密码）
python3 $PK config                                      # 查看当前配置（敏感项打码）
```
- base_url 没配 → **问用户要 IP+端口**，存好再继续，不要自己去猜/扫端口。
- api_key 没配 → 问用户要 key；用户说没有 → 问账号密码（`config username` / `config password`，走 JWT 登录）。
- **不再需要任何 TMDB 配置**：识别走 pankeeper 自己的 TMDB 通道，`tmdb-season` 走服务端配置（设置页「代理配置」），本机零依赖。

## 第 10 节：入口 B — 链接直转（用户直接甩分享链接）

跳过搜索，链接即资源。**片名以用户口述为准**（文件树文件名只做识别消歧参考，不当名字来源）。流程按序走，一步不过就停：

1. **识别盘类型**（自动，不打扰用户）：域名判断——pan.baidu.com→baidu，pan.quark.cn→quark，115.com/115cdn/anxia→115。
2. **问用户片名**（必须，不能跳）：直接问「这是什么片子？」——即使用户消息里带了名字也要一句话核实（「你说的是《xxx》对吧？」）。拿到名字跑 `recognize` 核实（TMDB 验证拿 media_name/年份；同名多候选列出来让用户挑）；类型二分类同第 1 节：除电影外全是电视节目。recognize `ok:false` → `rename` 去掉年份只存标题，同时如实告诉用户「TMDB 没搜到这个名字」。
3. **查网盘配置**：链接所属盘必须在 preflight 的已配置清单里（有账号且 `dd_configured`）→ 没配就停下告诉用户先去 pankeeper「转存配置」配好这个盘，转不了。
4. **验活 + 拉文件树**：`check` → `files`（URL 里带 `?pwd=xxx`/`?password=xxx` 的把码作为 code 传入）。`state=locked`（要提取码）→ 问用户要码，给不了就到此为止；死链直接告诉用户，没有降级候选（用户给的链接不做主替换）。
5. **挑文件**：规则同第 4 步——电影单影片分享直接建壳（非视频靠过滤配置）；混装分享挑最大 4K 正片用勾选；电视节目按 TMDB/集数挑 4K 集。
6. **入队**：同第 6 步——`with_shell: true` + `rename`=核实过的 media_name；混装用 `file_paths` 勾选。目标盘 = 链接所属盘（不能跨盘转存，不存在问盘）。
7. **汇报**：同第 7 步格式。
