# 2026-10-10 PanKeeper：v1.0.5 发布一条龙（双仓库同步 + CI 镜像 + NAS 更新）+ 反转开关刷新回弹修复

> 背景：主战场机器（另一台）10-07~10-09 完成了 STRM 可选拉通 / TMDB 重试 / QMS-STRM 反转等（0b0eefb、68bc0fe），并自行发布了 GitHub v1.0.4 tag + GHCR/DockerHub 1.0.4 镜像。本机今天：修反转开关 bug、定版 1.0.5、全链路发布并部署 NAS。

## 一、反转开关"刷新自己关闭"修复（用户实测踩到）

- 现象：队列配置开 QMS/STRM 反转后刷新页面，开关弹回关闭。实测后端 `reverse:true` 存得好好的——**不是存储问题**。
- 根因：`QueueConfig.vue` 表单用 `pkQueueCfgGet()` 的**同步快照**初始化（真实模式下 = 引擎默认值 reverse:false），异步拉到的后端真值只写回引擎内部 cfgView、从不回填表单。其余四项恰好与默认值相同，只有 reverse 暴露。
- 修复：快照起步后用现成的 `pkQueueCfgFetch()` 异步回填表单；`ready` 标记保证回填不触发写回与"已更新"提示（否则加载时白弹一次 toast）。
- 顺带：删除反转说明里"联动 QMS 必须显式选 STRM 路径 / 搜索历史里 STRM 先行"两句（用户点名）。

## 二、GitHub/Gitea 关系澄清与同步

- 用户明确：**Gitea 是 Gitea，GitHub 是 GitHub**（Gitea=私有开发真相，GitHub=公开发布侧）。
- GitHub 侧是独立压缩历史（init → USAGE/GHCR compose/CI workflow → 同步提交 908bbd0），与 Gitea 全量历史分叉。
- 已用 `merge --allow-unrelated-histories -X ours` 打通：代码保 Gitea 真相，GitHub 独有资产（.github/workflows/docker.yml、docs/USAGE.md、docker-compose.yml、assets/赞赏码）原样并入。**此后两仓库同源，普通 push 互推即可。**
- GitHub main 已推至 `87fecfc`。

## 三、v1.0.5 发布（CI 全自动双 registry）

- 版本规则：GHCR `1.0.4` 已被 10-09 的 CI 占用（源码 908bbd0，无今日修复，徽标还是 v0.1.0）→ 按用户规则 **升 1.0.5**，绝不覆盖已有 tag。
- 徽标三处同步：BasicLayout.vue `pc-ver`、package.json、package-lock.json → 1.0.5。
- 打 tag `v1.0.5`（附注=工作内容精简条目）推 GitHub → 触发 `.github/workflows/docker.yml`（amd64+arm64 双机矩阵）→ **GHCR + Docker Hub 双仓库自动出 1.0.5/latest/87fecfc 标签**，两轮 CI 全绿。
- GitHub Release v1.0.5 已建（四条功能精简）。secrets：DOCKERHUB_USERNAME/TOKEN 已在仓库里配好，CI 直接用。

## 四、NAS 更新

- **部署模式已变**：compose 改为直接拉 `7yueyue/pankeeper:latest`（主战场机器构建推送），不再 NAS 源码构建。更新 = `compose pull && up -d`。
- 已拉新镜像重建容器，站点 200，**线上徽标实测 v1.0.5**，与发布一致；反转开关线上刷新保持（修复在线上生效）。

## 五、流程资产

- 本套发布流程已沉淀为技能 `C:\Users\Administrator\.zcode\skills\pankeeper-release\SKILL.md`：网络判断（内网 192.168.2.77 / 外网隧道 100.66.1.1）、代理挂法、GHCR 占用定版规则、Gitea→GitHub→CI→NAS 全链路命令、坑位清单。

## 六、踩坑

1. 本机网络会变：今天到 192.168.2.x 内网直连全断（本机切到 10.x 网段），但 100.66.1.1 隧道仍通——**每次先实测，别沿用上次的网络结论**。
2. Gitea SSH 偶发拒绝：回退 HTTP（带 URL 编码密码）推送。
3. NAS 上 pankeeper-deploy 克隆会滞后（部署已不走它）；裸 `docker build` 会因缺 `frontend` 附加上下文去 Hub 拉 frontend:latest 而 401——要用源码构建必须带 `--build-context frontend=<clone>/pankeeper-vue3`。
4. 生产 bundle grep 函数名验证不可靠（vite 会混淆标识符），验证改用文案字符串（如删除的句子应 grep=0）。
5. pk.xinyunas.cn:8443 对 python requests 偶发 TLS 握手 EOF，`curl -sk` 反而稳。

## 七、追加：Server酱抖动治理（当晚）

- 用户实测：推送偶发"连接超时"，怀疑加了代理——排查结论：**无代理**（容器 env 干净、代码直连），是 ft07.com 端点本身抖（NAS/本机直连根路径均 12s 无响应；30 推 19 成 1 超时）。以前"没问题"是因为失败被静默吞掉、推送历史上线才显形。
- 修复一：真实推送网络级错误重试 ×2（隔 3s），单次超时 15s；测试按钮统一走同一链路（原单发，口径不一致误导排障）。
- 实测发现新问题：**超时≠没送达**（客户端放弃后 Server酱仍投递）→ 重发造成同一条电影推 2-3 遍（用户连收 3 条测试消息实锤）。
- 修复二（用户定案"宁可多等不能重发"）：单次超时 15s→**30s**；push() 整体挪后台线程（实测调用 0.035s 返回），抖动时不再阻塞转存完成回调。
