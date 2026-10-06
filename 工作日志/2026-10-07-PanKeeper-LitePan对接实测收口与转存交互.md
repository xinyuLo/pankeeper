# 2026-10-07 批次：LitePan 实测联调收口 + 快速转存交互 + 引擎异常根修

> 时段：10-06 深夜 ~ 10-07 凌晨。前置：10-06 三批（LitePan 对接/推送根修/识别候选）已部署至 aa23587 之后本批未部署。

## 一、LitePan 实测联调（用户本机 NAS 部署 LitePan 实例，端口 5545→5211）

- **协议实测修正**：响应信封 = `{"success", "message", "data"}`（非源码推测的 `{code,data}`）；
  失败 HTTP 401（缺 Authorization）/ 404（文件不存在=key 无效），均带人话 message。
  适配层按 success 字段重写解析（旧形状兜底保留）。
- **webhook 地址智能补全**：用户填基地址（`http://192.168.2.77:5545`）即可，后端自动补
  `/api/open/automation/events`。
- **全局通知来源（source）**：用户规则里 source=PanKeeper，我们发 pankeeper 大小写敏感
  不匹配（matched=0 实锤）。设置页 API Key 下加「通知来源」输入框：**填了才传，留空不传**
  （规则 source 留空=不限来源）；test 事件同行为。
- **健康胶囊**：设置页 LitePan 启用联动行加绿/红「LitePan 在线/离线」胶囊（/litepan/health，
  打基地址 /api/health 免认证）；PanSou 配置行同款胶囊（/search/health 实时探测，带耗时+插件数）。
- **端到端**：真实 Key 推 transfer.done/百度-电影 事件 → LitePan 命中「百度-电影」规则触发 ✓
  （用户侧运行记录可查）。
- **Key 类型结论**（源码核实）：readonly「只读查询」Key 当前**无任何路由消费**（占位类型）；
  PanKeeper 必须用「任务执行」类型 Key。

## 二、LitePan 联动交互打磨（按用户逐条定稿）

- **无全局事件名兜底**：事件名只在转存配置目录（lp_event）与任务弹窗配，**没填就不联动**；
  resolve_litepan_link 三道闸：设置页「启用联动」总闸 → 目录 lp_on 总闸 → 事件名必填。
- 设置页：提示条上移至联动后端下拉上方（两模式共用）、LitePan 加「启用联动」开关
  （门槛同 QMS：地址没填不让开/连通失败弹回）、事件名行删除、说明文字分行/并回 ctl 行内。
- 转存配置目录加 **media_type（电影/电视节目）必选下拉**（名称缩 60% 并排；新建初始空+
  淡红提醒+保存拦截；存量按名称推断初值）。**搜索行的类型下拉方案已回滚**（用户纠正：
  类型是目录属性）。
- 记录列表 LitePan 模式列适配：整理列淡紫「LitePan 接管」徽标、STRM 列 '—'、
  表头「整理」/「STRM」；行级「触发」按联动后端分流（litepan=重发 Webhook / qms=重触发
  刮削）；顶栏「触发 QMS / STRM」litepan 下隐藏。
- **触发按记录当时的 backend 分流**（records 加 backend 列，转存落库时写当时的
  media.backend；老记录空值回落当前设置）——用户定稿"根当时的记录来"。
- 记录列表错位根修：.pl-err(-webkit-box) 挂 td 破坏 table-cell 布局 → 样式挪内部 span
  （DOM 实测 5 列 td 高度全等）。

## 三、快速转存交互（多次往返后定稿版本）

- 搜索结果行**不加**任何下拉（一版误加已回滚）。
- 点快速转存 → 按所选目录 media_type 分流：
  - **电视节目目录**：照旧直接转（无内容判断）；
  - **电影目录**：自动拉清单判断内容——**多文件 → 展开「检测到多个文件，勾选要转存的」
    多选框列表**（固定高度滚动、文件名+大小、不预勾）；单文件 → 直接转；清单失败 → 直接转。
- 清单拉取走 share_list_cache 联动（点过「查看文件」的分享毫秒级，loading 态提示）。
- ⚠️ **转存按钮行为未定稿**（用户："改完我们再说转存按钮"）：当前点「开始转存」仍是
  建壳全转，多选框的勾选**尚未接入** file_paths——下一轮定。
- 适配器 with_shell+only_paths 组合已就位（baidu/quark/pan115；pan115 勾选模式禁用
  整目录接收防带上没勾的文件）。
- ⚠️ 一版误加的"文件夹更名必填"已回滚（更名留空=用资源名建壳，原有兜底不需要强制）。

## 四、引擎异常根修（用户实锤：夸克转存 LitePan 全绿，PanKeeper 报引擎异常）

- 根因：_finish 落库新加 `backend=get_group(...)` 与同函数**条件分支内**的
  `from ..services.settings_svc import get_group` 局部导入冲突——Python 判 get_group 为
  局部名，分支未走 → UnboundLocalError → 队列引擎兜底「引擎异常」（转存本体成功、
  状态落库失败）。
- 修：manual/auto 顶部模块级导入 get_group，函数内局部导入移除。
  真实入队回归：落库正常、backend=litepan 写入 ✓。

## 五、其他

- 死活预检换**网盘适配器实拉清单**判定（pansou check 对百度无提取码老链判不了
  need verify → 误放行实锤）；清单缓存命中毫秒级；预检 300ms 延迟 loading
  「正在检测链接有效性…」（用户实锤无反馈像卡死）。
- 搜索空态重做：雷达扫描图标（中心放大镜+扫描弧+涟漪，淡紫被否回退蓝紫）+ 可点
  胶囊=**真实最近搜索历史**（/search/recent-keywords；search_history 表首次启用写入）。
- favicon.ico 补真文件（fnOS 桌面获取图标只认 /favicon.ico 且不认 SVG，此前该路径
  返回 SPA HTML）；ico 排 HTML 第一个 icon 声明。
- 「默认管理员已创建」文案只在真创建时打印（部署时误导实锤）。
- engine「引擎异常」修复见上；识别并行化提速（10~25s→2.1~2.6s）+ 分阶段提示 +
  候选凑数过滤（top-30 分差）+ 30 分钟识别缓存。
- **未部署批次合计**：本文件全部改动（aa23587 之后 ~ 3b39076），含 10-06 三批。
