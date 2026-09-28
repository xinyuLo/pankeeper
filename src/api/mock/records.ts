import { reactive } from 'vue'
import type { DriveType, QueueLogLine, RecordItem, RecordTag } from '@/types/model'

/**
 * 转存记录 mock —— 原型 _shell.html records 数组（4 手写 + 24 IIFE 生成）原样移植。
 * 记录是快照：存转存当时选的目标路径与结果，事后改配置不影响旧记录。
 * 内存态即可（会话内可变、刷新重置，和原型一致）。
 */

/** 记录行 = 表格快照（RecordItem，对齐 docs/02 §7）+ 详情抽屉要用的转存快照扩展 */
export interface RecordRow extends RecordItem {
  id: number
  /** 分享链接（快照） */
  share_url: string
  /** 提取码（百度系才有，夸克/115 链接不带） */
  share_code: string
  /** 定时策略表达式；空 = 手动转存（手动任务不接 Server 酱推送） */
  cron: string
  include_subdirs: boolean
  exclude_count: number
  /** 完成后动作：触发 QMS */
  post_qms: boolean
  /** 完成后动作：Server 酱推送（只有自动转存有，对应交互契约） */
  post_notify: boolean
}

/* ===== 4 条手写：原型 records 数组前 4 条字段原样搬，仅按角色补抽屉快照字段 ===== */
const HANDWRITTEN: RecordRow[] = [
  {
    id: 1,
    n: '庆余年.第二季.4K.HDR.国粤双语', t: 'baidu', p: '/影视/国产剧/庆余年2',
    st: '完成 36/36', cls: 't-ok', tm: '09-26 22:41',
    qms: { st: '成功', cls: 't-ok' }, strm: { st: '成功', cls: 't-ok' },
    share_url: 'https://pan.baidu.com/s/1qtkz9m2vwa', share_code: 'abcd',
    cron: '0 3 * * *', include_subdirs: true, exclude_count: 0,
    post_qms: true, post_notify: true,
  },
  {
    id: 2,
    n: '三体.S01-S02.1080P.简繁字幕', t: 'quark', p: '/剧集/三体',
    st: '完成 24/24', cls: 't-ok', tm: '09-26 21:08',
    qms: { st: '成功', cls: 't-ok' }, strm: { st: '成功', cls: 't-ok' },
    share_url: 'https://pan.quark.cn/s/8f3a91c0', share_code: '',
    cron: '0 12 * * *', include_subdirs: true, exclude_count: 2,
    post_qms: true, post_notify: true,
  },
  {
    id: 3,
    n: '流浪地球系列合集.BDREMUX', t: '115', p: '/电影/国产科幻',
    st: '失败 3/12', cls: 't-bad', tm: '09-25 19:32',
    qms: { st: '失败 · 目标目录不存在', cls: 't-bad' }, strm: { st: '未执行', cls: 't-off' },
    share_url: 'https://115.com/s/5d0xv2q9', share_code: '',
    cron: '', include_subdirs: true, exclude_count: 1,
    post_qms: true, post_notify: false,
  },
  {
    id: 4,
    n: 'XX纪录片.全季.1080P', t: 'baidu', p: '/纪录片',
    st: '部分 18/30', cls: 't-off', tm: '09-24 12:03',
    qms: { st: '成功', cls: 't-ok' }, strm: { st: '失败 · QMS 未返回媒体路径', cls: 't-bad' },
    share_url: 'https://pan.baidu.com/s/1p8wlz3e', share_code: 'x7k2',
    cron: '0 */6 * * *', include_subdirs: true, exclude_count: 3,
    post_qms: true, post_notify: true,
  },
]

/* ===== 24 条生成：原型 IIFE 的生成逻辑逐行移植成 TS =====
   只有 4 条翻不了页，分页得有几十条才像真的 */
const GEN_NAMES = [
  '权力的游戏.S01-S08.BluRay', '曼达洛人.全三季.4K', '沙丘.合集.REMUX', '狂飙.4K.全39集',
  '漫长的季节.4K', '繁花.导演剪辑版', '甄嬛传.76集.修复版', '大明王朝1566', '觉醒年代.4K', '人世间.4K',
  '山海情.4K', '白夜追凶.S01', '隐秘的角落.4K', '沉默的真相.4K', '漫长的季节.幕后花絮', '庆余年.第一季.4K',
  '三体.纪录片.地球大工程', '流浪地球2.幕后纪录', '火星救援.4K.HDR', '星际穿越.IMAX.修复版',
  '信条.4K', '奥本海默.IMAX', '芭比.2023.4K', '蜘蛛侠.纵横宇宙.4K',
]
const GEN_TYPES: DriveType[] = ['baidu', 'quark', '115']
const GEN_OUTS: { st: string; cls: RecordItem['cls']; q: RecordTag; s: RecordTag }[] = [
  { st: '完成 24/24', cls: 't-ok', q: { st: '成功', cls: 't-ok' }, s: { st: '成功', cls: 't-ok' } },
  { st: '部分 18/30', cls: 't-off', q: { st: '成功', cls: 't-ok' }, s: { st: '失败 · QMS 未返回媒体路径', cls: 't-bad' } },
  { st: '完成 12/12', cls: 't-ok', q: { st: '未执行', cls: 't-off' }, s: { st: '未执行', cls: 't-off' } },
]

function pad(v: number): string {
  return v < 10 ? '0' + v : '' + v
}
function shareUrl(t: DriveType, i: number): string {
  const tail = (9214770 + i * 911).toString(36)
  if (t === 'baidu') return 'https://pan.baidu.com/s/1' + tail
  if (t === 'quark') return 'https://pan.quark.cn/s/' + tail
  return 'https://115.com/s/' + tail
}
const CRONS = ['0 3 * * *', '0 12 * * *', '0 */6 * * *', '30 8 * * *', '0 9 * * 1']
const CODES = ['abcd', 'x7k2', '9m4q', 'pk3d']

function buildGenerated(): RecordRow[] {
  const out: RecordRow[] = []
  GEN_NAMES.forEach((n, i) => {
    const o = GEN_OUTS[i % 3]
    const t = GEN_TYPES[i % 3]
    // 偶数行是自动任务（带 cron、接 Server 酱推送），奇数行是手动转存——和交互契约对上
    const auto = i % 2 === 0
    out.push({
      id: 5 + i,
      n,
      t,
      p: '/影视/批量导入/' + n.split('.')[0],
      st: o.st,
      cls: o.cls,
      tm: `09-${pad(27 - (i % 10))} ${pad(22 - (i % 12))}:${pad((i * 17) % 60)}`,
      qms: o.q,
      strm: o.s,
      share_url: shareUrl(t, i),
      share_code: t === 'baidu' ? CODES[i % CODES.length] : '',
      cron: auto ? CRONS[i % CRONS.length] : '',
      include_subdirs: i % 3 !== 1,
      exclude_count: i % 5,
      post_qms: o.q.st !== '未执行',
      post_notify: auto,
    })
  })
  return out
}

export const recordsStore = reactive<{
  items: RecordRow[]
  seq: number
  /** 触发留痕（原型 window.rcTrigLog 调试出口的对应物） */
  trigLog: string[]
  /** STRM 延迟触发进行中（原型 window.rcStrmPending 的对应物） */
  strmPending: boolean
}>({
  items: [...HANDWRITTEN, ...buildGenerated()],
  seq: 100,
  trigLog: [],
  strmPending: false,
})

/**
 * 详情抽屉的执行日志：按记录结果快照拼一行行日志（分级 STEP/INFO/WARN/ERROR）。
 * 自动转存记录末尾含「Server 酱推送已送达」；手动转存不接推送（交互契约）。
 */
export function recordLogOf(r: RecordRow): QueueLogLine[] {
  const m = /(\d+)\s*\/\s*(\d+)/.exec(r.st)
  const ok = m ? Number(m[1]) : 36
  const total = m ? Number(m[2]) : 36
  const fail = Math.max(0, total - ok)
  const file = r.n.split('.')[0]
  const L: QueueLogLine[] = [{ lv: 'STEP', txt: '解析分享链接完成，share_id=1xxxxxx' }]
  if (r.cron) {
    // 自动任务：先检测后转存，有过滤/去重环节
    L.push({ lv: 'INFO', txt: `定时任务触发（${r.cron}），开始本轮检测` })
    L.push({ lv: 'INFO', txt: `获取分享内文件清单，共 ${total} 项` })
    L.push({ lv: 'INFO', txt: '正则过滤：命中 ' + Math.min(total, ok + 2) + ' 项，跳过 2 项' })
    L.push({ lv: 'INFO', txt: '去重：MD5 比对跳过 ' + Math.max(0, total - ok - 2) + ' 项' })
    L.push({ lv: 'INFO', txt: `新增 ${ok} 个文件待转存` })
  } else {
    L.push({ lv: 'INFO', txt: `获取分享内文件清单，共 ${total} 个文件` })
    L.push({ lv: 'INFO', txt: '已过滤：非视频文件按规则跳过' })
    L.push({ lv: 'INFO', txt: '手动触发转存（手动任务不接入 Server 酱推送）' })
  }
  L.push({ lv: 'INFO', txt: `正在转存 ${ok}/${total} … ${file}.2160p.mkv` })
  if (r.cls === 't-bad') L.push({ lv: 'ERROR', txt: `${fail} 个文件提交失败：网盘接口返回超限，已达重试上限` })
  else if (r.cls === 't-off') L.push({ lv: 'WARN', txt: `${fail} 个文件首次提交超时，重试一次仍未通过` })
  L.push({ lv: 'STEP', txt: `转存结束：成功 ${ok} / 失败 ${fail}` })
  // 下游联动严格按快照走：转存失败时 QMS/STRM 不执行（t-off 不进日志）
  if (r.qms.cls === 't-ok') {
    L.push({ lv: 'STEP', txt: '等待 10 秒后触发 QMS 刮削' })
    L.push({ lv: 'INFO', txt: `QMS 刮削完成，新入库 ${ok} 条` })
  } else if (r.qms.cls === 't-bad') {
    L.push({ lv: 'ERROR', txt: 'QMS 刮削失败 · ' + (r.qms.st.split('·')[1]?.trim() || '原因未知') })
  }
  if (r.strm.cls === 't-ok') {
    L.push({ lv: 'STEP', txt: '触发 STRM 同步 → Emby 媒体库刷新请求已发送' })
  } else if (r.strm.cls === 't-bad') {
    L.push({ lv: 'ERROR', txt: 'STRM 生成失败 · ' + (r.strm.st.split('·')[1]?.trim() || '原因未知') })
  }
  if (r.post_notify) L.push({ lv: 'INFO', txt: 'Server 酱推送已送达' })
  return L
}
