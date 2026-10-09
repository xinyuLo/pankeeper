# 2026-10-09 PanKeeper 识别接口 500（img_url 缺失）与开源镜像翻车复盘

## 事故

用户搜"白蛇2"点识别按钮，没出电影候选。复现：`POST /api/recognize` 对带年份/短名字直接
**500**（0.9s），日志 `AttributeError: module 'app.services.tmdb' has no attribute 'img_url'`。

## 根因（我的 bug）

昨天 fa15cb7 重写 tmdb.py 做「多 IP failover」时，全量 Write 的新文件**漏掉了 img_url 函数**，
而 media_push（4 处）与 media_recognize（1 处，候选卡片海报）共 5 处调用它。
当时部署验证只测了 /settings/tmdb/test（ping/_get，不走 img_url），雷没爆。
识别候选 10-06 就存在，用户一按识别按钮 → 构建候选卡片 → `tmdb.img_url()` → 500。

## 为什么当时没拦住

- 桩测试只覆盖 _build_clients/ping/_get，没测 img_url
- compileall/vue-tsc 只查语法；CSS 清空（另一 bug）vue-tsc 也不查
- "产物 hash 对比"被有意 mock 中和混淆，错误归因

**教训：全量重写文件后，必须 grep 旧文件的全部公开函数名逐一核对（或先 diff 再删旧文件）。**

## 连环坑（同一晚）

1. CSS 去注释脚本 `'w'` 先截断后读 → pk.css/mt-modal.css 清空（开源仓库）→ 整页裸样式
2. 开源镜像 `7yueyue/pankeeper:latest` 由用户从 GitHub 下载版构建 → CSS + img_url 双缺
3. compose 被切成镜像模式，源码修复推送后容器不重建（镜像没换）

## 修复

- img_url 补回 merge（bbd23c6）与 pankeeper-git（454a6db，amend 进 init）
- NAS compose 备份镜像版为 docker-compose.image.yml，切回**源码构建**（pankeeper-deploy）
  重建后 img_url/no-cache/36KB 完整 CSS 全部到位
- 实测四个识别案例：两个正经分享名 confident=True 直接出《白蛇2：青蛇劫起 (2021)》；
  裸"白蛇2"出候选卡（青蛇劫起第一）；纯标签汤名（[白蛇2青蛇劫起][动画…]）依旧"未识别到"
  ——clean_work 不剥 【】标签汤，TMDB 查不了，属既有边界非 bug

## 待办

- 用户把修复版 pankeeper-git 同步 GitHub → 重建推送 Docker Hub/ghcr → NAS compose 换回
  docker-compose.image.yml + pull（镜像模式才能带上修复）
- magnet 测试断言遗留（主仓库同样失败，磁力类型已支持未更新测试）

## 连环坑之二：返回护栏嵌套冲突（识别候选卡把转存弹窗带走了）

img_url 修好后用户终于走到识别候选卡这一步，立刻踩到第二个 bug：**点候选卡片，
快速转存弹窗整个被关掉**。根因：快速转存弹窗与识别候选弹窗（RecognizePicker）各自
挂了一个 useBackGuard（10-08 批次全站接入的侧滑护栏），嵌套打开时历史栈压了两层
占位；候选关闭执行 history.back() 弹掉**自己那层**，popstate 被外层护栏也听到，
外层误判"用户按返回"→ 把转存弹窗关了。

**修法**：占位 state 写入实例唯一 id（guardSeq 递增），popstate 时只有"当前栈顶
不是我的占位"（= 我的占位被弹掉）才关自己；弹掉的是更上层的占位一律忽略。
嵌套任意层互不误伤。merge 9a749b4 / pankeeper-git df8f70e。

**验证坑**：生产构建压缩会改掉变量名，用源码变量名（myId）grep 产物判断新旧=无效；
要用**字符串字面量**（pkBackGuard: 后面是 1=旧逻辑、是压缩变量=新逻辑）。
另：仓库根 docker-compose.yml 已改为镜像模式（用户拉取用），**NAS 源码构建必须走
显式 docker build --build-context frontend=...**，compose build 在该目录是空操作
（无 build 段），且 tag 会打到旧镜像上——1.0.2 首推就是 stale 的，重推后才正确。

## 端到端验收（NAS 1.0.2 真实实例）

狂飙（10-06 歧义实锤案例）→ 快速转存 → 识别 → 2 候选卡 → 点选 F1 → 候选关闭、
**转存弹窗保持打开**、更名回填「F1：狂飙飞车 (2025)」。白蛇2 高置信案例直接回填
《白蛇2：青蛇劫起 (2021)》。全程未触发真实转存。

## 遗留

- ghcr.io/xinyulo/pankeeper:1.0.1 带护栏 bug（已发布数小时），1.0.2 为修复版；
  1.0.1 标签要不要删由用户决定
- ✅ Docker Hub/ghcr 已全部推完（1.0.3 = 单例护栏版，digest 050b64a5，双仓库一致）。
  **关键：给 NAS docker 守护进程配了 systemd 代理**（/etc/systemd/system/docker.service.d/proxy.conf
  → 127.0.0.1:7890，NO_PROXY=localhost,127.0.0.1），push/pull 全走 clash；live-restore 下重启
  docker 零闪断。**代理挂了 docker 拉推会跟着失败**，删除该 conf + daemon-reload + restart 即恢复直连
- （已解决）Docker Hub 推送：NAS 到 registry-1.docker.io 被墙，24h 自动重试循环挂在
  /tmp/dh_push_retry.sh（日志 /tmp/dh_push_retry.log），通了自动推 1.0.2+latest；
  用户也可在昨晚推成功过的那台电脑手动 push
- ghcr 包默认私有，开源需在 GitHub Packages 设置改 Public
