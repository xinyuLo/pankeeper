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
