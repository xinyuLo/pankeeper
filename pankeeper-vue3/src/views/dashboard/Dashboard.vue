<script setup lang="ts">
/* 首页 —— 原型 parts/page-dashboard.html 的 Vue 组合式移植（db- 前缀原样保留）。
 * 数据源：paStore（自动任务）+ accountStore（网盘连接状态），computed 直读保持响应式，
 * 任务在「自动转存」页改动后回到本页即自动更新——不落 ref 快照、不走一次性异步取数。
 * 关键契约：总览「下一次触发」跨所有网盘取最早；任务区每个网盘只展示最近一条要触发的任务，
 * 排序必须比 cron 估算出的时间戳 atMs，不能比格式化字符串（"今天 03:00" vs "02:30" 字符串序是错的）。 */
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import type { CSSProperties } from 'vue'
import type { AccountStatus, MainDriveType, PaTask } from '@/types/model'
import { paStore } from '@/api/mock/tasks'
import { accountStore } from '@/api/mock/accounts'
import { useThemeStore } from '@/store/theme'

const router = useRouter()
const theme = useThemeStore()

const DB_TYPES: MainDriveType[] = ['baidu', 'quark', '115']

/* ---------- 工具 ---------- */

function tasksOf(type: MainDriveType): PaTask[] {
  return paStore.tasks.filter((t) => t.type === type)
}

/* 连接状态文案（对齐原型 DB_ACC 的措辞；状态值来自 accountStore） */
const ACC_TEXT: Record<AccountStatus, string> = {
  connected: '连接正常',
  expired: '凭据已失效',
  unset: '未配置',
}

/* ---------- 下次触发：从 cron '分 时 * * *' 估算首个命中时刻 ----------
 * 返回里带 atMs（真实的下次触发时间戳），排序用它，别用格式化后的字符串比大小。 */
interface NextRun {
  none: boolean
  /** 一小时内触发时标橙提个醒 */
  near: boolean
  /** 人话主行：「今天 03:00」 */
  main: string
  /** 倒计时次行：「3 小时 20 分钟后」 */
  sub: string
  atMs: number
}

function nextRunOf(t: PaTask): NextRun {
  if (!t.enabled) return { none: true, near: false, main: '已停用', sub: '', atMs: Infinity }
  if (!t.cron || !t.cron.trim())
    return { none: true, near: false, main: '未开启定时', sub: '仅手动触发', atMs: Infinity }
  const p = t.cron.trim().split(/\s+/)
  if (p.length !== 5) return { none: true, near: false, main: t.cron, sub: '', atMs: Infinity }
  const mi = parseInt(p[0], 10)
  const hh = parseInt(p[1], 10)
  if (isNaN(mi) || isNaN(hh)) return { none: true, near: false, main: t.cron, sub: '', atMs: Infinity }
  const now = new Date()
  const at = new Date(now.getFullYear(), now.getMonth(), now.getDate(), hh, mi, 0, 0)
  if (at.getTime() <= now.getTime()) at.setDate(at.getDate() + 1) // 今天已过 → 明天
  const diff = at.getTime() - now.getTime()
  const hleft = Math.floor(diff / 3600000)
  const mleft = Math.round((diff % 3600000) / 60000)
  const sub = hleft >= 1 ? hleft + ' 小时 ' + mleft + ' 分钟后' : mleft + ' 分钟后'
  const hm = ('0' + hh).slice(-2) + ':' + ('0' + mi).slice(-2)
  const isToday = at.getDate() === now.getDate()
  return { none: false, near: diff < 3600000, main: (isToday ? '今天 ' : '明天 ') + hm, sub, atMs: at.getTime() }
}

/* ---------- 排序：启用在前 → 按下次触发时间戳升序 ----------
 * ⚠️ 必须比 atMs（时间戳），不能比 main 字符串——
 *    "今天 03:00" > "今天 02:30" 的字符串比较会把晚的排前面（原型踩过）。 */
function sortTasks(list: PaTask[]): PaTask[] {
  return list.slice().sort((a, b) => {
    if (a.enabled !== b.enabled) return a.enabled ? -1 : 1
    return nextRunOf(a).atMs - nextRunOf(b).atMs
  })
}

/* ---------- 状态药丸（移植 dbPill） ---------- */
function pillOf(t: PaTask): { cls: string; text: string } {
  if (!t.enabled) return { cls: 's-off', text: '已停用' }
  if (t.last_status === 'success') return { cls: 's-ok', text: '成功' }
  if (t.last_status === 'fail') return { cls: 's-fail', text: '失败' }
  if (t.last_status === 'running') return { cls: 's-run', text: '执行中' }
  return { cls: 's-never', text: '从未执行' }
}

/* ---------- 结果文案：把「新增 2 / 跳过 34 / 失败 0」拆成可染色片段（移植 dbResult） ---------- */
interface ResSeg {
  t: string
  /** r-new=新增数染绿 / r-fail=失败数染红，无则纯文本 */
  c?: string
}
function resultSegs(t: PaTask): { plain: string; segs: ResSeg[] } {
  const r = t.last_result || '—'
  if (t.last_status === 'never' || r === '—') return { plain: '尚未执行过', segs: [] }
  if (t.last_status === 'running') return { plain: '执行中…', segs: [] }
  const m = r.match(/新增\s*(\d+)\s*\/\s*跳过\s*(\d+)\s*\/\s*失败\s*(\d+)/)
  if (!m) return { plain: r, segs: [] }
  const nf = parseInt(m[3], 10)
  return {
    plain: '',
    segs: [
      { t: '新增 ' },
      { t: m[1], c: 'r-new' },
      { t: ' · 跳过 ' + m[2] + (nf > 0 ? ' · 失败 ' : ' · 失败 0') },
      ...(nf > 0 ? [{ t: m[3], c: 'r-fail' }] : []),
    ],
  }
}

/* ---------- 某网盘的任务汇总（移植 dbSummary） ---------- */
interface PanSummary {
  total: number
  on: number
  ok: number
  fail: number
  /** 最近一次执行过的任务（last_run 字符串倒序取首） */
  latest: PaTask | null
}

function summaryOf(type: MainDriveType): PanSummary {
  const list = tasksOf(type)
  const ok = list.filter((t) => t.enabled && t.last_status === 'success').length
  const fail = list.filter((t) => t.enabled && t.last_status === 'fail').length
  const ran = list
    .filter((t) => t.last_status !== 'never')
    .sort((a, b) => String(b.last_run).localeCompare(String(a.last_run)))
  return {
    total: list.length,
    on: list.filter((t) => t.enabled).length,
    ok,
    fail,
    latest: ran[0] || null,
  }
}

/* ---------- 总览数字条（跨所有网盘汇总） ---------- */
const overview = computed(() => {
  const all = DB_TYPES.reduce<PaTask[]>((acc, t) => acc.concat(tasksOf(t)), [])
  const enabled = all.filter((t) => t.enabled).length
  // 「下一次触发」跨所有网盘取最早——不是当前 tab 的首行（原型有断言钉住）
  const cands = all
    .filter((t) => t.enabled)
    .map((t) => ({ t, n: nextRunOf(t) }))
    .filter((o) => !o.n.none)
    .sort((a, b) => a.n.atMs - b.n.atMs)
  return {
    total: all.length,
    enabled,
    paused: all.length - enabled,
    ok: all.filter((t) => t.enabled && t.last_status === 'success').length,
    fails: all.filter((t) => t.enabled && t.last_status === 'fail').length,
    nextTxt: cands.length ? cands[0].n.main : '—',
    nextSub: cands.length ? cands[0].t.name + ' · ' + cands[0].n.sub : '暂无启用的定时任务',
  }
})

/* ---------- 网盘状态卡视图模型（原型 dbPanHtml 的数据部分） ---------- */
interface PanView {
  type: MainDriveType
  name: string
  short: string
  color: string
  status: AccountStatus
  statusText: string
  lastCheck: string
  total: number
  on: number
  ok: number
  fail: number
  latest: PaTask | null
  pill: { cls: string; text: string } | null
  res: { plain: string; segs: ResSeg[] } | null
}

const pans = computed<PanView[]>(() =>
  DB_TYPES.map((type) => {
    const acct = accountStore.accounts[type]
    const s = summaryOf(type)
    const lt = s.latest
    return {
      type,
      // 卡片全名 = 短名 + 网盘（对齐原型 paMeta：百度网盘/夸克网盘/115网盘）
      name: acct.short + '网盘',
      short: acct.short,
      color: acct.color,
      status: acct.status,
      statusText: ACC_TEXT[acct.status],
      lastCheck: acct.last_check,
      total: s.total,
      on: s.on,
      ok: s.ok,
      fail: s.fail,
      latest: lt,
      pill: lt ? pillOf(lt) : null,
      res: lt ? resultSegs(lt) : null,
    }
  }),
)

/* ---------- 定时任务卡：tab + 当前 tab 只展示最近一条要触发的任务 ---------- */
interface TabView {
  type: MainDriveType
  /** tab 文本：全名去掉「网盘」后缀（对齐原型 dbTabsHtml） */
  label: string
  on: number
  fail: number
}

const tabViews = computed<TabView[]>(() =>
  DB_TYPES.map((type) => {
    const s = summaryOf(type)
    return {
      type,
      label: accountStore.accounts[type].short,
      on: s.on,
      fail: s.fail,
    }
  }),
)

const curTab = ref<MainDriveType>('baidu')
const tasksCard = ref<HTMLElement | null>(null)

/* tab 切换的唯一实现：tab 点击与卡片点击都走这里，行为才一致。
 * 卡片点击时把任务卡滚进视野（窄屏下它在折叠线以下，点了要看得见反馈）。 */
function switchTab(type: MainDriveType, scroll = false) {
  if (curTab.value !== type) curTab.value = type
  if (scroll) nextTick(() => tasksCard.value?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }))
}

/* 「去管理」：真正的页面跳转入口；模板里 .stop 防止卡片把这次点击消费成 tab 切换 */
function goManage(type: MainDriveType) {
  router.push('/auto/' + type)
}

const curList = computed(() => tasksOf(curTab.value))
const curSummary = computed(() => summaryOf(curTab.value))

/* 排序后第一个启用项 = 这个网盘最近一条要触发的任务（首页不摆全量表格） */
const curRow = computed(() => {
  const t = sortTasks(curList.value).filter((x) => x.enabled)[0] || null
  if (!t) return null
  const n = nextRunOf(t)
  return { t, n, pill: pillOf(t), res: resultSegs(t) }
})

/* ---------- 内联样式助手：CSS 变量经 style 传给伪元素（色标条/品牌色细条） ---------- */
const accent = (c: string): CSSProperties => ({ '--db-accent': c })
const brand = (c: string): CSSProperties => ({ '--db-c': c })

/* 持久化主题没有统一应用入口，驾驶舱作为登录后的首页兜底同步一次（幂等） */
onMounted(() => theme.apply())
</script>

<template>
  <div class="db-wrap">
    <!-- ===== 1. 总览数字条 ===== -->
    <div class="db-stats">
      <div class="db-stat" :style="accent('var(--primary)')">
        <b>{{ overview.enabled }}</b><span>启用中的任务</span>
        <div class="db-stat-sub">共 {{ overview.total }} 个，{{ overview.paused }} 个已停用</div>
      </div>
      <div class="db-stat" :style="accent('var(--success)')">
        <b>{{ overview.ok }}</b><span>最近执行成功</span>
      </div>
      <div class="db-stat" :style="accent('var(--error)')">
        <b>{{ overview.fails }}</b><span>最近执行失败</span>
        <div v-if="overview.fails" class="db-stat-sub">建议到对应网盘页查看</div>
      </div>
      <!-- 第 4 格是「今天 03:00」这类文本，is-text 单独降字号让四格齐平 -->
      <div class="db-stat is-text" :style="accent('var(--warning)')">
        <b>{{ overview.nextTxt }}</b><span>下一次触发</span>
        <div class="db-stat-sub">{{ overview.nextSub }}</div>
      </div>
    </div>

    <!-- ===== 2. 网盘状态卡 ===== -->
    <div>
      <div class="db-sec-hd">
        <h3>网盘状态</h3>
        <span class="db-sec-tip">点卡片切换下方任务列表；「去管理」进入自动转存页</span>
      </div>
      <div class="db-pans">
        <!-- 整卡可点 = 切换下方任务 tab；键盘可达（Enter/Space） -->
        <div
          v-for="p in pans"
          :key="p.type"
          class="db-pan"
          :class="{ 'is-off': p.status === 'unset' }"
          :data-st="p.status"
          :style="brand(p.color)"
          role="button"
          tabindex="0"
          :title="'点击查看 ' + p.name + ' 的定时任务'"
          @click="switchTab(p.type, true)"
          @keydown.enter.prevent="switchTab(p.type, true)"
          @keydown.space.prevent="switchTab(p.type, true)"
        >
          <div class="db-pan-hd">
            <!-- 光环核心：双环旋转（外环虚线慢转 / 内环按连接状态着色快转） -->
            <div class="db-pan-ic">
              <i class="db-ring-b" aria-hidden="true"></i>
              <span class="db-pan-ic-core">{{ p.short }}</span>
            </div>
            <div class="db-pan-nm">
              <b>{{ p.name }}</b>
              <span>{{ p.statusText }} · 上次检测 {{ p.lastCheck }}</span>
            </div>
          </div>
          <div class="db-pan-body">
            <!-- 最近一条任务的简报 -->
            <div v-if="p.latest && p.pill && p.res" class="db-last">
              <div class="db-last-hd">
                <span class="db-pill" :class="p.pill.cls"><i></i>{{ p.pill.text }}</span>
                <span class="db-last-tm">{{ p.latest.last_run }}</span>
              </div>
              <div class="db-last-nm" :title="p.latest.name">{{ p.latest.name }}</div>
              <div class="db-last-res">
                <template v-if="p.res.segs.length">
                  <template v-for="(s, i) in p.res.segs" :key="i">
                    <b v-if="s.c" :class="s.c">{{ s.t }}</b>
                    <template v-else>{{ s.t }}</template>
                  </template>
                </template>
                <template v-else>{{ p.res.plain }}</template>
              </div>
            </div>
            <div v-else class="db-last">
              <div class="db-last-hd">还没有执行记录</div>
              <div class="db-last-res">新建任务并开启定时后，这里会显示最近一次的结果</div>
            </div>
            <!-- 迷你统计三格 -->
            <div class="db-mini">
              <div><b>{{ p.on }}<i>/{{ p.total }}</i></b><span>启用任务</span></div>
              <div><b class="m-ok">{{ p.ok }}</b><span>最近成功</span></div>
              <div><b :class="{ 'm-bad': p.fail > 0 }">{{ p.fail }}</b><span>最近失败</span></div>
            </div>
          </div>
          <div class="db-pan-ft">
            <span>点击卡片查看任务</span>
            <!-- 去管理：stopPropagation 别让卡片把这次点击又消费成 tab 切换 -->
            <span
              class="db-pan-go"
              role="link"
              tabindex="0"
              :title="'进入 ' + p.name + ' 自动转存'"
              @click.stop="goManage(p.type)"
              @keydown.enter.prevent.stop="goManage(p.type)"
              @keydown.space.prevent.stop="goManage(p.type)"
            >
              去管理
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18l6-6-6-6" /></svg>
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- ===== 3. 定时任务卡 ===== -->
    <div ref="tasksCard" class="db-tasks">
      <div class="db-tasks-hd">
        <h3>定时任务</h3>
        <div class="db-tasks-desc">每个网盘只展示<b>最近一条要触发</b>的任务，看一眼就知道下一个跑什么；完整列表与新增/编辑去「自动转存」页。</div>
      </div>
      <div class="db-tabsbar">
        <div class="tabs">
          <div v-for="tb in tabViews" :key="tb.type" :class="{ on: curTab === tb.type }" @click="switchTab(tb.type)">
            {{ tb.label }}
            <span class="db-tabnb">{{ tb.on }}</span>
            <span v-if="tb.fail" class="db-tabdot" :title="'有 ' + tb.fail + ' 个任务最近执行失败'"></span>
          </div>
        </div>
      </div>
      <div class="db-tablewrap">
        <table class="db-table">
          <thead>
            <tr>
              <th class="db-th">任务 / 保存目录</th>
              <th class="db-th">状态</th>
              <th class="db-th">下次触发</th>
              <th class="db-th">最近结果</th>
              <th class="db-th">上次执行</th>
            </tr>
          </thead>
          <tbody>
            <!-- 空态两种：无任务 / 全停用 -->
            <tr v-if="!curList.length">
              <td class="db-td db-empty-cell" colspan="5"><div class="db-empty">这个网盘还没有定时任务</div></td>
            </tr>
            <tr v-else-if="!curRow">
              <td class="db-td db-empty-cell" colspan="5"><div class="db-empty">任务都在停用中 · 启用后这里显示最近一条要触发的</div></td>
            </tr>
            <tr v-else class="db-row">
              <td class="db-td" style="width: 34%">
                <span class="db-name" :title="curRow.t.name">{{ curRow.t.name }}</span>
                <span class="db-dir" :title="curRow.t.save_dir">{{ curRow.t.save_dir }}</span>
              </td>
              <td class="db-td" style="width: 120px">
                <span class="db-pill" :class="curRow.pill.cls"><i></i>{{ curRow.pill.text }}</span>
              </td>
              <td class="db-td" style="width: 150px">
                <div class="db-next" :class="curRow.n.none ? 'is-none' : curRow.n.near ? 'is-near' : ''">
                  <b>{{ curRow.n.main }}</b>
                  <span v-if="curRow.n.sub">{{ curRow.n.sub }}</span>
                </div>
              </td>
              <td class="db-td" style="width: 26%">
                <span class="db-result">
                  <template v-if="curRow.res.segs.length">
                    <template v-for="(s, i) in curRow.res.segs" :key="i">
                      <b v-if="s.c" :class="s.c">{{ s.t }}</b>
                      <template v-else>{{ s.t }}</template>
                    </template>
                  </template>
                  <template v-else>{{ curRow.res.plain }}</template>
                </span>
              </td>
              <td class="db-td" style="width: 88px; color: var(--text3); font-size: 12.5px">{{ curRow.t.last_run || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <!-- 卡脚：当前 tab 的任务汇总，大屏下正好填住任务卡撑高的余量 -->
      <div class="db-tasks-ft">
        <span>共 <b>{{ curSummary.total }}</b> 个任务，<b>{{ curSummary.on }}</b> 个启用中</span>
        <span>最近成功 <b class="ok">{{ curSummary.ok }}</b> · 失败 <b :class="{ bad: curSummary.fail > 0 }">{{ curSummary.fail }}</b></span>
        <span v-if="curSummary.total > 1" class="db-tasks-next">其余 <b>{{ curSummary.total - 1 }}</b> 个任务在「自动转存」页管理</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ============================================================
   首页（db- 前缀，自 page-dashboard.html 原样移植）
   自上而下：总览数字条 → 网盘卡片 → 定时任务 tab 分组
   ============================================================ */

/* 纵向撑满：外壳的 .content > .view 是 flex 列，这里跟着撑开，
   让最后一张「定时任务」卡吃掉剩余高度，底部不留空挡。
   min-height:0 是让内部滚动生效的关键（flex 子项默认 min-height:auto 会拒绝收缩）。 */
.db-wrap { display: flex; flex-direction: column; gap: 16px; flex: 1 1 auto; min-height: 0; }

/* ---------- 1. 总览数字条 ---------- */
.db-stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; flex: none; }
.db-stat {
  position: relative; background: var(--card); border-radius: var(--r);
  box-shadow: var(--shadow); padding: 13px 18px 13px 26px; overflow: hidden;
  transition: transform 0.18s, box-shadow 0.18s;
}
.db-stat:hover { transform: translateY(-2px); box-shadow: var(--shadow-lg); }
/* 底部流动色带：细线沿底边循环流动（颜色随 --db-accent） */
.db-stat::after {
  content: ''; position: absolute; left: 0; right: 0; bottom: 0; height: 2px;
  background: linear-gradient(90deg, transparent, var(--db-accent, var(--db-c)), transparent);
  background-size: 200% 100%;
  animation: dbStatFlow 3.2s linear infinite;
  opacity: 0.55;
}
@keyframes dbStatFlow {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
/* 左侧内缩色标，跟项目里 .stat 保持同一套视觉语言（颜色经 --db-accent 传入） */
.db-stat::before {
  content: ''; position: absolute; left: 12px; top: 50%; transform: translateY(-50%);
  width: 4px; height: 36px; border-radius: 2px; background: var(--db-accent, #dfe2e8);
}
.db-stat b {
  display: block; font-size: 24px; font-weight: 600; line-height: 1.2; letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
}
/* 「今天 03:00」这类文本 24px 会撑得比前三格高一截，单独降到 20px 并去掉负字距 */
.db-stat.is-text b { font-size: 20px; letter-spacing: 0; }
.db-stat span { display: block; font-size: 12.5px; color: var(--text3); margin-top: 3px; }
.db-stat .db-stat-sub { font-size: 12px; color: var(--text3); margin-top: 6px; display: flex; align-items: center; gap: 5px; }

/* ---------- 2. 网盘卡片 ---------- */
.db-sec-hd { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 2px; }
.db-sec-hd h3 { font-size: 14px; font-weight: 600; margin: 0; }
.db-sec-hd .db-sec-tip { font-size: 12.5px; color: var(--text3); }

.db-pans { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; flex: none; margin-top: 10px; }
.db-pan {
  position: relative; background: var(--card); border: 1px solid var(--split);
  border-radius: var(--r); box-shadow: var(--shadow); padding: 0; overflow: hidden;
  cursor: pointer; transition: transform 0.18s, box-shadow 0.18s, border-color 0.18s;
}
.db-pan:hover { transform: translateY(-3px); box-shadow: var(--shadow-lg); border-color: var(--db-c); }
.db-pan:focus-visible { outline: 2px solid var(--db-c); outline-offset: 2px; }
/* 顶部品牌色光晕细条：一眼区分是哪家网盘（颜色经 --db-c 传入） */
.db-pan::before {
  content: ''; position: absolute; left: 0; right: 0; top: 0; height: 3px;
  background: linear-gradient(90deg, transparent, var(--db-c) 35%, var(--db-c) 65%, transparent);
  box-shadow: 0 0 10px var(--db-c);
}
/* 扫描线：自上而下缓缓掠过（呼应参考稿的 console 质感） */
.db-pan::after {
  content: ''; position: absolute; left: 0; right: 0; height: 40px; top: -46px;
  background: linear-gradient(to bottom, transparent, color-mix(in srgb, var(--db-c) 7%, transparent), transparent);
  animation: dbPanScan 5.5s linear infinite;
  pointer-events: none;
}
@keyframes dbPanScan { 0% { top: -46px; } 70%, 100% { top: 105%; } }
.db-pan:hover { transform: translateY(-4px); box-shadow: var(--shadow-lg), 0 0 22px color-mix(in srgb, var(--db-c) 16%, transparent); }
.db-pan.is-off { opacity: 0.72; }
.db-pan.is-off::after { animation-play-state: paused; opacity: 0; }

/* ---- 光环核心：双环旋转 + 品牌色发光核心（参考稿 holo-ring 融合版） ---- */
.db-pan-ic {
  position: relative;
  width: 44px; height: 44px; border-radius: 13px; flex: none;
  display: flex; align-items: center; justify-content: center;
  color: #fff; font-size: 13px; font-weight: 700;
  background: var(--db-c);
  box-shadow: 0 0 16px color-mix(in srgb, var(--db-c) 45%, transparent);
  overflow: hidden;
}
/* 流动高光：一道柔光沿对角线循环掠过核心 */
.db-pan-ic::before {
  content: ''; position: absolute; inset: -40%;
  background: linear-gradient(115deg, transparent 38%, rgba(255, 255, 255, 0.45) 50%, transparent 62%);
  animation: dbCoreFlow 2.8s ease-in-out infinite;
}
@keyframes dbCoreFlow {
  0% { transform: translateX(-70%); }
  55%, 100% { transform: translateX(70%); }
}
.db-ring-a, .db-ring-b {
  position: absolute; inset: -7px; border-radius: 50%;
  pointer-events: none;
}
/* 内环：按连接状态着色（connected=绿 / expired=红 / unset=品牌色暗态），快转 */
.db-ring-b {
  inset: -3px; border: 2px solid transparent;
  border-top-color: color-mix(in srgb, var(--db-c) 70%, transparent);
  border-right-color: color-mix(in srgb, var(--db-c) 25%, transparent);
  animation: dbRingSpin 3.2s linear infinite;
}
.db-pan[data-st='connected'] .db-ring-b { border-top-color: var(--success); border-right-color: rgba(82, 196, 26, 0.3); box-shadow: 0 0 12px rgba(82, 196, 26, 0.2); }
.db-pan[data-st='expired'] .db-ring-b { border-top-color: var(--error); border-right-color: rgba(255, 77, 79, 0.3); box-shadow: 0 0 12px rgba(255, 77, 79, 0.2); }
@keyframes dbRingSpin { to { transform: rotate(360deg); } }
/* 核心字块（环中心的短名） */
.db-pan-ic-core { position: relative; z-index: 1; }
/* 未配置：环整体降暗，不旋转（没东西可转） */
.db-pan.is-off .db-ring-b { animation-play-state: paused; border-top-color: var(--text4); border-right-color: transparent; box-shadow: none; }


.db-pan-hd { display: flex; align-items: center; gap: 10px; padding: 14px 18px 11px; }
.db-pan-ic {
  width: 34px; height: 34px; border-radius: 10px; flex: none; display: flex;
  align-items: center; justify-content: center; color: #fff; font-size: 12.5px; font-weight: 600;
  background: var(--db-c);
}
.db-pan-nm { flex: 1 1 auto; min-width: 0; }
.db-pan-nm b { display: block; font-size: 14.5px; font-weight: 600; line-height: 1.35; }
.db-pan-nm span {
  display: block; font-size: 12px; color: var(--text3); margin-top: 1px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

.db-pan-body { padding: 0 18px 12px; }
/* 最近一次任务执行简报 */
.db-last { border: 1px solid var(--split); border-radius: 10px; background: var(--surface-2); padding: 9px 12px; }
.db-last-hd { display: flex; align-items: center; gap: 7px; font-size: 12px; color: var(--text3); margin-bottom: 6px; }
.db-last-nm {
  font-size: 13px; font-weight: 500; line-height: 1.4;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.db-last-res {
  font-size: 12.5px; color: var(--text2); margin-top: 5px; line-height: 1.5;
  font-variant-numeric: tabular-nums;
}
.db-last-tm { font-size: 12px; color: var(--text3); margin-left: auto; flex: none; }

/* 迷你统计三格 */
.db-mini { display: flex; gap: 1px; margin-top: 10px; background: var(--split); border-radius: 8px; overflow: hidden; }
.db-mini div { flex: 1; background: var(--card); text-align: center; padding: 8px 4px; }
.db-mini b {
  display: block; font-size: 15px; font-weight: 600; line-height: 1.25;
  font-variant-numeric: tabular-nums;
}
/* 总数用次级色同字号，别缩成看不清的小字（原型踩过：/3 缩到 11px 几乎读不出） */
.db-mini b i { font-style: normal; font-size: 13px; font-weight: 400; color: var(--text3); }
.db-mini span { display: block; font-size: 11.5px; color: var(--text3); margin-top: 1px; }
/* 迷你统计的成功/失败数（原型是内联 style，这里收成类并补暗色覆盖） */
.db-mini b.m-ok { color: #389e0d; }
.db-mini b.m-bad { color: #cf1322; }
html[data-theme='dark'] .db-mini b.m-ok { color: #95de64; }
html[data-theme='dark'] .db-mini b.m-bad { color: #ff7875; }

.db-pan-ft {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 10px 18px; border-top: 1px solid var(--split); font-size: 12.5px; color: var(--text3);
}
.db-pan-go {
  display: inline-flex; align-items: center; gap: 4px; color: var(--db-c); font-weight: 500;
  cursor: pointer; border-radius: 4px;
}
.db-pan-go:hover { text-decoration: underline; }
.db-pan-go:focus-visible { outline: 2px solid var(--db-c); outline-offset: 2px; }
.db-pan:hover .db-pan-go { gap: 7px; }
.db-pan-go svg { transition: transform 0.18s; }
.db-pan:hover .db-pan-go svg { transform: translateX(2px); }

/* ---------- 3. 定时任务 tab 分组 ----------
   任务卡在大屏吃掉剩余高度；min-height 收到 240（一行任务 + 头/脚），
   16 寸这类矮屏上整页刚好一屏放下，不至于为凑高度空一大块。 */
.db-tasks {
  background: var(--card); border-radius: var(--r); box-shadow: var(--shadow);
  overflow: hidden; flex: 1 1 auto; min-height: 240px; display: flex; flex-direction: column;
}
/* 标题与说明并排一行，说明压到单行 + 溢出省略，标题区不白吃高度 */
.db-tasks-hd {
  display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap;
  padding: 15px 22px 0; flex: none;
}
.db-tasks-hd h3 { font-size: 15.5px; font-weight: 600; margin: 0; flex: none; }
.db-tasks-desc {
  font-size: 12.5px; color: var(--text3); line-height: 1.5; min-width: 0;
  flex: 1 1 auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
/* 卡内分段 tab 栏（接表头底色，跟默认目录页同一做法） */
.db-tabsbar { padding: 10px 14px 0; border-bottom: 1px solid var(--split); background: var(--surface-2); flex: none; }
.db-tabsbar .tabs { margin-bottom: 0; }
.db-tabnb {
  display: inline-block; margin-left: 6px; padding: 0 6px; border-radius: 8px;
  font-size: 11.5px; line-height: 17px; background: rgba(0, 0, 0, 0.06); color: var(--text2);
  font-variant-numeric: tabular-nums;
}
.tabs div.on .db-tabnb { background: rgba(22, 119, 255, 0.12); color: var(--primary); }
/* 有失败任务时 tab 上的红点提醒 */
.db-tabdot {
  display: inline-block; width: 6px; height: 6px; border-radius: 50%;
  background: #ff4d4f; margin-left: 5px; vertical-align: 1px;
}

/* 表体外包一层滚动容器：行多时在内部滚动、表头用 sticky 钉住；
   flex:1 1 auto 让它吃掉卡片余量（配合卡脚 margin-top:auto，余量优先给表格区）。 */
.db-tablewrap { flex: 1 1 auto; min-height: 0; overflow: auto; }
.db-table { width: 100%; border-collapse: collapse; table-layout: fixed; }
.db-th {
  text-align: left; padding: 11px 14px; font-size: 12px; font-weight: 500;
  color: var(--text3); border-bottom: 1px solid var(--split); background: var(--card);
  white-space: nowrap; position: sticky; top: 0; z-index: 1;
}
.db-td { padding: 12px 14px; font-size: 13px; border-bottom: 1px solid var(--split); vertical-align: middle; }
.db-table tbody tr:last-child .db-td { border-bottom: none; }
.db-row:hover .db-td { background: var(--surface-2); }

/* 卡脚汇总：吃掉任务卡撑高后的余量，避免大屏下一页白 */
.db-tasks-ft {
  flex: none; display: flex; align-items: center; gap: 18px; flex-wrap: wrap;
  padding: 10px 18px; margin-top: auto; border-top: 1px solid var(--split);
  background: var(--surface-2); font-size: 12.5px; color: var(--text3);
}
.db-tasks-ft b { color: var(--text2); font-weight: 600; font-variant-numeric: tabular-nums; }
.db-tasks-ft b.ok { color: #389e0d; }
.db-tasks-ft b.bad { color: #cf1322; }
.db-tasks-ft .db-tasks-next { margin-left: auto; }
.db-tasks-ft .db-tasks-next b { color: var(--text); }

.db-name {
  font-weight: 500; display: block; line-height: 1.5;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.db-dir {
  font-family: var(--font-mono); font-size: 12px; color: var(--text3); display: block; margin-top: 2px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
/* 下次触发：主行是人话，次行是倒计时 */
.db-next b { display: block; font-size: 13px; font-weight: 500; line-height: 1.45; }
.db-next span {
  display: block; font-size: 12px; color: var(--text3); margin-top: 1px;
  font-variant-numeric: tabular-nums;
}
.db-next.is-near b { color: #fa8c16; }
.db-next.is-none b { color: var(--text3); font-weight: 400; }

/* 状态药丸 */
.db-pill {
  display: inline-flex; align-items: center; gap: 5px; padding: 2px 9px;
  border-radius: 20px; font-size: 12px; line-height: 18px; white-space: nowrap;
}
.db-pill i { width: 5px; height: 5px; border-radius: 50%; background: currentColor; flex: none; }
.db-pill.s-ok { color: #389e0d; background: rgba(82, 196, 26, 0.12); }
.db-pill.s-fail { color: #cf1322; background: rgba(255, 77, 79, 0.12); }
.db-pill.s-run { color: #1677ff; background: rgba(22, 119, 255, 0.12); }
.db-pill.s-never { color: var(--text3); background: rgba(0, 0, 0, 0.05); }
.db-pill.s-off { color: var(--text3); background: rgba(0, 0, 0, 0.05); }

.db-result {
  font-size: 12.5px; color: var(--text2); line-height: 1.5;
  font-variant-numeric: tabular-nums;
}
.db-result .r-new { color: #389e0d; font-weight: 500; }
.db-result .r-fail { color: #cf1322; font-weight: 500; }

/* 空态：表体撑高后，文字要落在中间而不是贴着顶部 */
.db-empty { padding: 48px 20px; text-align: center; color: var(--text3); font-size: 13px; }
.db-tablewrap > .db-table tbody td[colspan] { height: 100%; }
.db-empty-cell { padding: 0; }
.db-empty-cell .db-empty {
  display: flex; align-items: center; justify-content: center; min-height: 180px; height: 100%;
}

/* ---------- 暗色（对齐原型 dark 覆盖块） ---------- */
html[data-theme='dark'] .db-tabnb { background: rgba(255, 255, 255, 0.1); color: var(--text2); }
html[data-theme='dark'] .tabs div.on .db-tabnb { background: rgba(64, 150, 255, 0.22); color: #91caff; }
html[data-theme='dark'] .db-pill.s-ok { color: #95de64; background: rgba(82, 196, 26, 0.18); }
html[data-theme='dark'] .db-pill.s-fail { color: #ff7875; background: rgba(255, 77, 79, 0.18); }
html[data-theme='dark'] .db-pill.s-run { color: #91caff; background: rgba(64, 150, 255, 0.2); }
html[data-theme='dark'] .db-pill.s-never,
html[data-theme='dark'] .db-pill.s-off { color: var(--text3); background: rgba(255, 255, 255, 0.08); }
html[data-theme='dark'] .db-result .r-new { color: #95de64; }
html[data-theme='dark'] .db-result .r-fail { color: #ff7875; }
html[data-theme='dark'] .db-next.is-near b { color: #ffc069; }
html[data-theme='dark'] .db-tabdot { background: #ff7875; }
html[data-theme='dark'] .db-tasks-ft b.ok { color: #95de64; }
html[data-theme='dark'] .db-tasks-ft b.bad { color: #ff7875; }

/* ---------- 窄屏 ----------
   窄屏下内容本来就比一屏高，不需要「撑满」——把 flex 撑高和内部滚动撤掉，
   让整页正常纵向滚动（否则任务卡被压到 320px、表格挤成一条）。 */
@media (max-width: 1180px) {
  .db-stats { grid-template-columns: repeat(2, 1fr); }
  .db-pans { grid-template-columns: repeat(2, 1fr); }
}
@media (max-width: 1024px) {
  .db-wrap { flex: 0 0 auto; }
  .db-tasks { flex: 0 0 auto; min-height: 0; }
  .db-tablewrap { overflow: visible; }
  .db-th { position: static; }
}
@media (max-width: 860px) {
  .db-pans { grid-template-columns: 1fr; }
}

/* ---------- 手机（<768px）：总览两列、任务表竖排成卡片观感 ----------
   任务表只有一行数据，竖排堆叠就是天然的手机卡片，不用 JS 换结构。 */
@media (max-width: 767px) {
  .db-wrap { gap: 14px; }
  .db-stats { grid-template-columns: repeat(2, 1fr); gap: 10px; }
  .db-stat { padding: 12px 14px 12px 20px; }
  .db-stat b { font-size: 20px; }
  .db-stat.is-text b { font-size: 17px; }
  .db-stat-sub { display: none; } /* 两列下副行挤成两三字一行，干脆收掉（数据下有完整卡） */
  .db-sec-hd { flex-direction: column; gap: 2px; }

  .db-pan-hd { padding: 13px 14px 10px; }
  .db-pan-body { padding: 0 14px 12px; }
  .db-pan-ft { padding: 9px 14px; }

  /* 任务卡头部：说明文字放开折行 */
  .db-tasks-hd { padding: 13px 14px 0; }
  .db-tasks-desc { white-space: normal; }
  .db-tabsbar { padding: 8px 10px 0; }
  .db-tasks-ft { padding: 10px 14px; gap: 8px 14px; }

  /* 表格竖排：隐藏表头，每格变成整行块（inline width 一并作废） */
  .db-table { table-layout: auto; }
  .db-table thead { display: none; }
  .db-table, .db-table tbody, .db-table tr, .db-table td {
    display: block;
    width: auto !important;
  }
  .db-td { padding: 10px 14px; border-bottom: 1px dashed var(--split); }
  .db-row .db-td:last-child { border-bottom: none; }
  .db-name, .db-dir { white-space: normal; word-break: break-all; }
  .db-empty-cell .db-empty { min-height: 120px; }
}
</style>
