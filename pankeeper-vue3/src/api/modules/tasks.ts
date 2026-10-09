import { reactive } from 'vue';
import { get, mockDelay, post, put, del, USE_MOCK } from '../http';
import { paStore, paByType } from '../mock/tasks';
import type { MainDriveType, PaTask, QueueLogLine, TreeNode } from '@/types/model';
function hydrate(rows: Record<string, unknown>[]): PaTask[] {
    const tasks: PaTask[] = [];
    for (const r of rows) {
        tasks.push({ ...(r as unknown as PaTask), exclIdx: Array.isArray(r.exclIdx) ? (r.exclIdx as number[]) : [] });
        const raw = r as Record<string, unknown>;
        const regex: PaRegexRule[] = raw.regex_pattern
            ? [{ pat: String(raw.regex_pattern), rep: String(raw.regex_replace || '') }]
            : [];
        extrasMap[Number(raw.id)] = cloneExtras({
            regex,
            drill_on: Boolean(raw.drill_on),
            drill: (raw.drill as string[]) || [],
            qms_id: (raw.qms_id as number | null) ?? null,
            strm_id: (raw.strm_id as number | null) ?? null,
            lp_event: String(raw.lp_event || ''),
        });
    }
    paStore.tasks.splice(0, paStore.tasks.length, ...tasks);
    paStore.seq = Math.max(paStore.seq, ...tasks.map((t) => t.id), 0);
    return tasks;
}
export function listPaTasks(type?: MainDriveType | ''): Promise<PaTask[]> {
    if (USE_MOCK)
        return mockDelay(type ? paByType(type) : [...paStore.tasks]);
    return get<Record<string, unknown>[]>('/pa/tasks', { params: type ? { type } : {} }).then(hydrate);
}
export async function togglePaTask(id: number): Promise<boolean> {
    if (USE_MOCK) {
        const t = paStore.tasks.find((x) => x.id === id);
        if (t)
            t.enabled = !t.enabled;
        return mockDelay(!!t && t.enabled);
    }
    const { enabled } = await put<{
        enabled: boolean;
    }>(`/pa/tasks/${id}/enabled`);
    const t = paStore.tasks.find((x) => x.id === id);
    if (t)
        t.enabled = enabled;
    return enabled;
}
export async function deletePaTask(id: number): Promise<void> {
    if (USE_MOCK) {
        paStore.tasks = paStore.tasks.filter((x) => x.id !== id);
        delete extrasMap[id];
        return mockDelay(undefined);
    }
    await del(`/pa/tasks/${id}`);
    paStore.tasks = paStore.tasks.filter((x) => x.id !== id);
    delete extrasMap[id];
}
export async function savePaTask(task: PaTask, extras: PaExtras): Promise<PaTask> {
    if (USE_MOCK) {
        const idx = paStore.tasks.findIndex((x) => x.id === task.id);
        let saved: PaTask;
        if (idx >= 0) {
            saved = { ...task };
            paStore.tasks[idx] = saved;
        }
        else {
            saved = { ...task, id: ++paStore.seq };
            paStore.tasks.push(saved);
        }
        extrasMap[saved.id] = cloneExtras(extras);
        return mockDelay(saved);
    }
    const body = {
        ...task,
        regex_pattern: extras.regex?.[0]?.pat || '',
        regex_replace: extras.regex?.[0]?.rep || '',
        qms_id: extras.qms_id,
        strm_id: extras.strm_id,
        lp_event: extras.lp_event || '',
        drill_on: extras.drill_on,
        drill: extras.drill,
    };
    const saved = task.id
        ? await put<PaTask>(`/pa/tasks/${task.id}`, body)
        : await post<PaTask>('/pa/tasks', body);
    await listPaTasks();
    const savedRow = paStore.tasks.find((x) => x.name === saved.name && x.share_url === saved.share_url);
    return savedRow || saved;
}
export interface ShareFileNode {
    name: string;
    is_dir: boolean;
    size: number;
    kids: ShareFileNode[];
}
export interface ShareFilesMeta {
    total: number;
    tree: ShareFileNode[];
    files: {
        path: string;
        name: string;
        is_dir: boolean;
        size: number;
        md5: string;
    }[];
    cached_at: number;
    fresh: boolean;
}
export function getShareFiles(taskId: number, refresh = false, filtered = false): Promise<ShareFilesMeta> {
    if (USE_MOCK)
        return Promise.resolve({ total: 0, tree: [], files: [], cached_at: 0, fresh: false });
    return get<ShareFilesMeta>(`/pa/tasks/${taskId}/share-files`, { params: { refresh, filtered } });
}
export interface PaRegexRule {
    pat: string;
    rep: string;
}
export interface PaExtras {
    regex: PaRegexRule[];
    drill_on: boolean;
    drill: string[];
    qms_id: number | null;
    strm_id: number | null;
    lp_event: string;
}
const extrasMap = reactive<Record<number, PaExtras>>({});
function cloneExtras(e: PaExtras): PaExtras {
    return { regex: e.regex.map((r) => ({ ...r })), drill_on: e.drill_on, drill: [...e.drill], qms_id: e.qms_id, strm_id: e.strm_id, lp_event: e.lp_event };
}
function defaultExtras(): PaExtras {
    return { regex: [{ pat: '', rep: '' }], drill_on: false, drill: [], qms_id: null, strm_id: null, lp_event: '' };
}
export function getPaExtras(id: number): PaExtras {
    return extrasMap[id] ? cloneExtras(extrasMap[id]) : defaultExtras();
}
export interface PaDrillDir {
    name: string;
    size: string;
}
const DRILL_DIRS: PaDrillDir[] = [
    { name: '第 01-12 集', size: '36 GB' },
    { name: '第 13-24 集', size: '35 GB' },
    { name: '第 25-36 集', size: '34 GB' },
    { name: '幕后花絮', size: '4.2 GB' },
    { name: '字幕包', size: '240 KB' },
    { name: '海报剧照', size: '310 MB' },
];
export function parseShare(type: string, shareUrl: string, shareCode = ''): Promise<{
    count: number;
    total: number;
}> {
    if (USE_MOCK)
        return mockDelay({ count: 36, total: 36 });
    return post<{
        count: number;
        total: number;
    }>('/pa/parse-share', { type, share_url: shareUrl, share_code: shareCode });
}
export function getDrillDirs(): Promise<PaDrillDir[]> {
    return mockDelay(DRILL_DIRS.map((d) => ({ ...d })));
}
export interface PaExclFile {
    name: string;
    md5: string;
}
export interface PaExclFetch {
    fresh: boolean;
    ts: number;
    files: PaExclFile[];
}
export async function fetchExclFiles(taskId: number, force = false): Promise<PaExclFetch> {
    const r = await getShareFiles(taskId, force, false);
    return {
        fresh: r.fresh,
        ts: r.cached_at * 1000,
        files: r.files.filter((f) => !f.is_dir).map((f) => ({ name: f.name, md5: f.md5 || '' })),
    };
}
export function commitExcl(taskId: number, names: string[], md5s: string[] = []): Promise<{
    count: number;
}> {
    return post<{
        count: number;
    }>(`/pa/tasks/${taskId}/exclude`, { names, md5s });
}
export interface PaRunRow {
    id: number;
    started: string;
    finished: string;
    status: 'success' | 'fail' | 'running';
    overall?: {
        st: string;
        cls: string;
    };
    add: number;
    skip: number;
    skip_md5: number;
    excl: number;
    total_share: number;
    regex_miss: number;
    message: string;
}
export interface PaRunDetail extends PaRunRow {
    task_id: number;
    task_name: string;
    save_dir: string;
    compare_path: string;
    regex_pattern: string;
    include_subdirs: boolean;
    transferred: string[];
    excluded: string[];
    regex_hit: string[];
    md5_skipped: string[];
    qms: {
        st: string;
        cls: string;
    } | null;
    strm: {
        st: string;
        cls: string;
    } | null;
    logs: QueueLogLine[];
}
export function runPaTaskNow(taskId: number): Promise<{
    queued: boolean;
    task?: string;
    reason?: string;
}> {
    if (USE_MOCK)
        return mockDelay({ queued: true, task: 'mock' });
    return post<{
        queued: boolean;
        task?: string;
        reason?: string;
    }>(`/pa/tasks/${taskId}/run`);
}
export function retriggerTaskQms(taskId: number): Promise<{
    ok: boolean;
    message: string;
}> {
    if (USE_MOCK)
        return mockDelay({ ok: true, message: 'mock' });
    return post<{
        ok: boolean;
        message: string;
    }>(`/pa/tasks/${taskId}/retrigger-qms`);
}
export function getPaRuns(taskId: number): Promise<PaRunRow[]> {
    if (USE_MOCK)
        return mockDelay([]);
    return get<PaRunRow[]>(`/pa/tasks/${taskId}/runs`);
}
export interface PaRunListItem extends PaRunRow {
    task_id: number;
    task_name: string;
    task_type: MainDriveType;
    fail: number;
    duration: number;
    save_dir: string;
}
export interface PaRunsQuery {
    task_id?: number | null;
    type?: string;
    status?: string;
    keyword?: string;
    page?: number;
    page_size?: number;
}
export function listPaRuns(q: PaRunsQuery = {}): Promise<{
    total: number;
    items: PaRunListItem[];
}> {
    if (USE_MOCK)
        return mockDelay({ total: 0, items: [] });
    return get<{
        total: number;
        items: PaRunListItem[];
    }>('/pa/runs', {
        params: {
            task_id: q.task_id || undefined,
            type: q.type || undefined,
            status: q.status || undefined,
            keyword: q.keyword || undefined,
            page: q.page || 1,
            page_size: q.page_size || 20,
        },
    });
}
export function getPaRunDetail(runId: number): Promise<PaRunDetail> {
    if (USE_MOCK)
        return mockDelay({} as PaRunDetail);
    return get<PaRunDetail>(`/pa/runs/${runId}`);
}
export function cronHuman(cron: string, emptyText = '未开启定时'): string {
    if (!cron || !cron.trim())
        return emptyText;
    const p = cron.trim().split(/\s+/);
    if (p.length < 5)
        return cron;
    const [m, h, dom, mon, dow] = p;
    const pad = (v: string) => (v.length < 2 ? '0' + v : v);
    if (dom === '*' && mon === '*' && dow === '*') {
        if (m === '*' && h === '*')
            return '每分钟执行';
        if (m === '*/5' && h === '*')
            return '每 5 分钟';
        if (m === '0' && h === '*')
            return '每小时整点';
        if (m !== '*' && h !== '*')
            return `每天 ${pad(h)}:${pad(m)}`;
        return `每天 ${h}:${m}`;
    }
    if (dom !== '*' && mon !== '*')
        return `每月 ${parseInt(mon, 10)} 月 ${parseInt(dom, 10)} 日 ${pad(h)}:${pad(m)}`;
    if (dow !== '*' && dom === '*' && mon === '*') {
        const map: Record<string, string> = { '0': '周日', '1': '周一', '2': '周二', '3': '周三', '4': '周四', '5': '周五', '6': '周六', '7': '周日' };
        return `${map[dow] || '周' + dow} ${pad(h)}:${pad(m)}`;
    }
    return `自定义 (${cron})`;
}
export function cronNextTs(cron: string): number | null {
    const m = /^(\d{1,2})\s+(\d{1,2})\s+\*\s+\*\s+\*$/.exec((cron || '').trim());
    if (!m)
        return null;
    const d = new Date();
    d.setHours(parseInt(m[2], 10), parseInt(m[1], 10), 0, 0);
    if (d.getTime() <= Date.now())
        d.setDate(d.getDate() + 1);
    return d.getTime();
}
export function fmtHms(ts: number): string {
    const d = new Date(ts);
    const p = (n: number) => (n < 10 ? '0' + n : '' + n);
    return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
export function fmtMdHm(ts: number): string {
    const d = new Date(ts);
    const p = (n: number) => (n < 10 ? '0' + n : '' + n);
    return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
const CODE_RULES = [
    /[?&]pwd=([0-9a-zA-Z]{4})/,
    /[?&]password=([0-9a-zA-Z]{4})/,
    /[?&]code=([0-9a-zA-Z]{4})/,
    /[（(]\s*([0-9a-zA-Z]{4})\s*[)）]/,
    /\s([0-9a-zA-Z]{4})\s*$/,
];
export function extractShareCode(url: string): string {
    if (!url)
        return '';
    for (const re of CODE_RULES) {
        const m = url.match(re);
        if (m)
            return m[1];
    }
    return '';
}
export interface PaRunStep {
    lv: QueueLogLine['lv'];
    txt: string;
    p: number;
    add?: Partial<Record<'add' | 'skip' | 'fail' | 'excl', number>>;
}
export function buildPaRunSeq(task: PaTask): PaRunStep[] {
    const total = 36;
    const excl = task.exclude_count || 0;
    const fail = task.last_status === 'fail' ? 1 : 0;
    const add = 2;
    const skip = Math.max(0, total - excl - fail - add);
    const cmp = task.compare_path || task.save_dir;
    const seq: PaRunStep[] = [
        { lv: 'INFO', txt: `解析分享链接 ${task.share_url}`, p: 8 },
        { lv: 'STEP', txt: `获取文件清单，共 ${total} 个文件`, p: 16 },
        { lv: 'INFO', txt: '应用正则过滤规则，命中 0 条', p: 24 },
        { lv: 'STEP', txt: `开始去重对比（路径 ${cmp}）`, p: 34 },
        { lv: 'WARN', txt: '片段预告.mp4 无 MD5，回退文件名对比', p: 42 },
        { lv: 'STEP', txt: '逐文件转存中…', p: 55, add: { skip } },
        { lv: 'INFO', txt: `新增 ${add} 个 / 跳过 ${skip} 个 / 失败 ${fail} 个`, p: 70, add: { add, skip, fail } },
        { lv: 'INFO', txt: `已排除 ${excl} 个文件（来自排除清单）`, p: 78, add: { excl } },
        { lv: 'STEP', txt: '完成本批次转存，生成汇总', p: 84 },
    ];
    if (task.post_qms) {
        seq.push({ lv: 'STEP', txt: '延迟 15 秒触发 QMS 整理', p: 88 }, { lv: 'STEP', txt: '轮询 QMS 任务状态… 整理完成', p: 92 }, { lv: 'STEP', txt: '延迟 15 秒触发 STRM 生成', p: 95 });
    }
    seq.push({ lv: 'INFO', txt: '转存任务完成，已写入执行记录', p: 100 });
    if (task.post_notify)
        seq.push({ lv: 'INFO', txt: 'Server 酱推送已送达', p: 100 });
    return seq;
}
export function finishPaRun(taskId: number, add: number, skip: number, fail: number): Promise<void> {
    const t = paStore.tasks.find((x) => x.id === taskId);
    if (t) {
        t.last_run = fmtMdHm(Date.now());
        t.last_status = fail > 0 ? 'fail' : 'success';
        t.last_result = `新增 ${add} / 跳过 ${skip} / 失败 ${fail}`;
    }
    return mockDelay(undefined);
}
export function getPaDetailLog(): Promise<QueueLogLine[]> {
    const lines: QueueLogLine[] = [
        { lv: 'STEP', txt: '解析分享链接完成' },
        { lv: 'INFO', txt: '获取分享内文件清单，共 36 项' },
        { lv: 'INFO', txt: '正则过滤：命中 34 项，跳过 2 项' },
        { lv: 'INFO', txt: '去重：MD5 比对跳过 31 项，文件名比对跳过 1 项' },
        { lv: 'INFO', txt: '新增 2 个文件待转存' },
        { lv: 'INFO', txt: '正在转存：XXX.S01E33.2160p.mkv' },
        { lv: 'INFO', txt: '正在转存：XXX.S01E34.2160p.mkv' },
        { lv: 'STEP', txt: '转存完成：新增 2 / 跳过 34 / 失败 0' },
        { lv: 'STEP', txt: '延迟 10 秒后触发 QMS 刮削' },
        { lv: 'INFO', txt: 'QMS 刮削完成，新入库 2 条' },
        { lv: 'STEP', txt: '触发 STRM 生成 → 通知 Emby 刷新媒体库' },
        { lv: 'INFO', txt: 'Server 酱推送已送达' },
    ];
    return mockDelay(lines);
}
const PA_DIR_RAW: Record<MainDriveType, TreeNode[]> = {
    baidu: [
        {
            name: '影视',
            kids: [
                { name: '国产剧', kids: [{ name: '兰香如故' }, { name: '繁花' }, { name: '大江大河' }, { name: '长安的荔枝' }] },
                { name: '美剧', kids: [{ name: '权力的游戏' }, { name: '西部世界' }] },
                { name: '电影' },
                { name: '纪录片' },
            ],
        },
        { name: '资料', kids: [{ name: '文档' }, { name: '图片' }, { name: '学习视频' }] },
        { name: '备份', kids: [{ name: '手机相册' }, { name: '工作' }] },
    ],
    quark: [
        { name: '夸克影视', kids: [{ name: '九重紫' }, { name: '小巷人家' }, { name: '动漫', kids: [{ name: '京都动画合集' }] }] },
        { name: '夸克资料', kids: [{ name: '文档' }] },
    ],
    '115': [
        { name: '115影视', kids: [{ name: '玫瑰的故事' }, { name: '与凤行' }, { name: '庆余年2' }] },
        { name: '115备份', kids: [{ name: '手机相册' }] },
    ],
};
function annotateDirs(list: TreeNode[], base: string): TreeNode[] {
    return list.map((nd) => {
        const path = (base === '/' ? '' : base) + '/' + nd.name;
        return { name: nd.name, path, kids: nd.kids && nd.kids.length ? annotateDirs(nd.kids, path) : undefined };
    });
}
export function getDirTree(type: MainDriveType): Promise<TreeNode[]> {
    const tree: TreeNode[] = [{ name: '全部文件', path: '/', kids: annotateDirs(PA_DIR_RAW[type], '/') }];
    return mockDelay(JSON.parse(JSON.stringify(tree)) as TreeNode[], 80);
}
export async function clearRuns(before: string): Promise<number> {
    if (USE_MOCK)
        return mockDelay(0);
    const { count } = await del<{
        count: number;
    }>(`/pa/runs?before=${encodeURIComponent(before)}`);
    return count;
}
