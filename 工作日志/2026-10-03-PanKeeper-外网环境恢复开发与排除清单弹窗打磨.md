# 2026-10-03 PanKeeper 外网环境恢复开发 + 排除清单弹窗打磨

## 环境恢复（外网 100.66.1.1）

- 外网 Gitea 只剩合并仓库 `xinyu/pankeeper`（独立仓库 pankeeper-backend/pankeeper-vue3 已删除），
  `pankeeper-merge` remote 已切 `git@100.66.1.1:xinyu/pankeeper.git`，拉到 f4818f4。
- 本地起服务：后端 `pankeeper-merge/pankeeper-backend` 新建 .venv（依赖与旧仓库完全一致）+ `run.py`，
  前端 `pankeeper-vue3` 沿用 node_modules 直接 dev；日志在 pankeeper-merge/{backend,frontend}-dev.log。
- 本地库 admin 密码 = admin#123（与部署站相同）；百度账号 id=3「强盘」连接正常。
- NAS `baidu-autosave` 容器（bd-save-luo 镜像，配置在 /vol1/1001/tools/baiduAutoSave/config.json）
  的兰香如故定时任务已导入本地 pa_tasks（排除清单去掉「兰丨香如故/」前缀——PanKeeper 按裸文件名匹配）。
  NAS 配置原件在 `D:\workspace\fnWork\baidu-autosave-ref\`（含 Cookie/密码，勿入库）。
- 本地设置里 pansou/QMS 还指内网 192.168.2.77:8028/8020，外网联调搜索/刮削要改。

## 排除清单弹窗（ExclModal.vue）改动

- 「一键勾选无 MD5」+「刷新」并排上移标题行（mt-btn-soft chip 风格带图标）；缓存状态条改纯状态胶囊。
- 展示口径（用户拍板）：**只显示正则命中的 + 已勾选（已排除）的**，其余不显示——后端 share-files 接口
  `filtered=false` 拉全量，弹窗侧按 task.regex_pattern 现筛；已排除文件即使正则外也保留（否则取消不了），
  标紫色「正则外」tag（.mt-tag-dim）。刷新时保留口径 = 已排除清单 ∪ 草稿已勾选。
- 一键勾选三态提示；已排除文件不在当前清单时黄字警示「确定后将移出」。

## 踩坑

1. **vite 监听不到 `<style scoped src="./xxx.css">` 的 css 变更**（Windows）：改 mt-modal.css 后
   HMR 日志里始终只有 ExclModal.vue 的更新、无 css 的记录，页面重载也拿旧模块缓存；`touch` 一下 css
   立即触发 4 个模块的 hmr update。以后改共享 css 没生效先 touch，别怀疑自己改错地方。
2. **浏览器坐标点击前必须重新取坐标**：页面刷新后表格列宽变化，沿用旧坐标点「排除」实际点中了
   「立即执行」，把兰香如故真实跑了一遍（新增 2/跳过 29/失败 0，转存链路+Server酱推送全通——算因祸得福
   验证了全链路，但这是误触发）。教训：操作用 evaluate 现算 getBoundingClientRect 再 cua.click。
3. mt- 弹窗的自定义按钮在 Playwright 下偶发 actionability 卡死（元素无遮挡也超时），换 CUA 坐标点击可解。
