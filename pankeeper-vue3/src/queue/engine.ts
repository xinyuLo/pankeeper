import { reactive } from 'vue';
import { get, post, put, USE_MOCK } from '@/api/http';
import type { QueueCfg, QueueState, QueueTask } from '@/types/model';
const CFG_DEF: QueueCfg = { threads: 1, gap: 5, qms: 10, strm: 10 };
const KEEP_DONE = 60 * 60 * 1000;
type Listener = (s: QueueState) => void;
const listeners: Listener[] = [];
const cfgListeners: Listener[] = [];
export const queueView = reactive<QueueState>({ seq: 0, lastTick: 0, lastDone: 0, tasks: [] });
export function isAutoQueued(t: QueueTask): boolean {
    return t.paTaskId != null || t.source === 'auto';
}
export const cfgView = reactive<QueueCfg>({ ...CFG_DEF });
let onNewTaskStart: () => void = () => { };
export function setOnNewTaskStart(fn: () => void) {
    onNewTaskStart = fn;
}
function fireListeners() {
    for (const fn of listeners) {
        try {
            fn(queueView);
        }
        catch {
        }
    }
}
const LOCAL_KEY = 'pkq_v2';
const LOCAL_CFG_KEY = 'pkq_cfg';
const TICK = 600;
let pinnedLocalSeq = 0;
function loadLocal(): QueueState | null {
    try {
        const raw = localStorage.getItem(LOCAL_KEY);
        return raw ? (JSON.parse(raw) as QueueState) : null;
    }
    catch {
        return null;
    }
}
function seed(s: QueueState) {
    s.tasks = [
        {
            id: ++s.seq, name: '庆余年.第二季.4K.HDR.国粤双语.简繁特效字幕', type: 'baidu', path: '/影视/国产剧',
            files: 36, size: '82.4 GB', status: 'run', phase: 'transfer', phaseStart: 0, progress: 68,
            flags: { start: 1, half: 1 }, doneAt: 0,
            logs: [
                { lv: 'STEP', txt: '解析分享链接完成，share_id=1xxxxxx' },
                { lv: 'INFO', txt: '获取分享内文件清单，共 36 项' },
                { lv: 'INFO', txt: '开始转存：庆余年.S02E01.2160p.HDR.mkv' },
                { lv: 'STEP', txt: '正在转存 24/36 …' },
            ],
        },
        {
            id: ++s.seq, name: '庆余年第二季.1080P.腾讯版.全36集', type: 'quark', path: '/媒体/剧集',
            files: 36, size: '46.1 GB', status: 'wait', phase: '', phaseStart: 0, progress: 0, flags: {}, doneAt: 0, logs: [],
        },
    ];
}
function syncView(s: QueueState) {
    queueView.seq = s.seq;
    queueView.lastTick = s.lastTick;
    queueView.lastDone = s.lastDone;
    queueView.tasks.splice(0, queueView.tasks.length, ...s.tasks);
}
function saveLocal(s: QueueState) {
    try {
        localStorage.setItem(LOCAL_KEY, JSON.stringify(s));
    }
    catch {
    }
    syncView(s);
}
function pushLog(t: QueueTask, lv: QueueTask['logs'][number]['lv'], txt: string) {
    t.logs.push({ lv, txt });
    if (t.logs.length > 40)
        t.logs.shift();
}
function advance(t: QueueTask, cfg: QueueCfg, now: number, s: QueueState) {
    switch (t.phase || 'transfer') {
        case 'transfer':
            if (!t.flags.start) {
                t.flags.start = 1;
                pushLog(t, 'INFO', '开始转存：' + t.name.split('.')[0] + ' …');
            }
            t.progress = Math.min(100, t.progress + 6 + Math.round(Math.random() * 7));
            if (t.progress >= 55 && !t.flags.half) {
                t.flags.half = 1;
                pushLog(t, 'INFO', '正在转存 ' + Math.max(2, Math.round(t.files / 2)) + '/' + t.files + ' …');
            }
            if (t.progress >= 100) {
                pushLog(t, 'INFO', '转存完成：成功 ' + t.files + ' / 失败 0');
                pushLog(t, 'STEP', '等待 ' + cfg.qms + ' 秒后触发 QMS 刮削');
                t.phase = 'waitqms';
                t.phaseStart = now;
            }
            break;
        case 'waitqms':
            if (now - t.phaseStart >= cfg.qms * 1000) {
                pushLog(t, 'STEP', '触发 QMS 刮削任务 #' + (t.id + 11));
                t.phase = 'qms';
                t.phaseStart = now;
            }
            break;
        case 'qms':
            if (now - t.phaseStart >= 1200) {
                pushLog(t, 'INFO', 'QMS 刮削完成，新入库 ' + t.files + ' 条');
                t.phase = 'waitstrm';
                t.phaseStart = now;
            }
            break;
        case 'waitstrm':
            if (now - t.phaseStart >= cfg.strm * 1000) {
                pushLog(t, 'STEP', '触发 STRM 生成 → 通知 Emby 刷新媒体库');
                t.phase = 'strm';
                t.phaseStart = now;
            }
            break;
        case 'strm':
            if (now - t.phaseStart >= 800) {
                t.status = 'done';
                t.doneAt = now;
                pushLog(t, 'INFO', '任务完成：转存 + QMS + STRM 全链路结束');
                s.lastDone = now;
            }
            break;
    }
}
function loadCfgRaw(): QueueCfg {
    try {
        const c = JSON.parse(localStorage.getItem(LOCAL_CFG_KEY) || '');
        if (c && c.threads)
            return { ...CFG_DEF, ...c };
    }
    catch {
    }
    return { ...CFG_DEF };
}
function tick() {
    const s = loadLocal() ?? { seq: 0, tasks: [], lastTick: 0, lastDone: 0 };
    const now = Date.now();
    if (now - (s.lastTick || 0) < TICK - 80)
        return;
    s.lastTick = now;
    s.tasks = s.tasks.filter((t) => !(t.status === 'done' && t.doneAt && now - t.doneAt > KEEP_DONE));
    const cfg = loadCfgRaw();
    Object.assign(cfgView, cfg);
    let running = 0;
    for (const t of s.tasks) {
        if (t.status !== 'run')
            continue;
        running++;
        advance(t, cfg, now, s);
    }
    if (running < cfg.threads && now - (s.lastDone || 0) >= cfg.gap * 1000) {
        for (const t of s.tasks) {
            if (t.status === 'wait') {
                t.status = 'run';
                t.phase = 'transfer';
                t.phaseStart = now;
                pushLog(t, 'STEP', '轮到它了，开始转存');
                onNewTaskStart();
                break;
            }
        }
    }
    saveLocal(s);
    fireListeners();
}
let lastStateJson = '';
let lastRunIdSeen = 0;
function applyRemoteState(s: QueueState) {
    const json = JSON.stringify(s);
    if (json === lastStateJson)
        return;
    lastStateJson = json;
    for (const t of s.tasks) {
        if (t.status === 'run' && t.id > lastRunIdSeen) {
            if (lastRunIdSeen > 0)
                onNewTaskStart();
            lastRunIdSeen = t.id;
        }
    }
    syncView(s);
    fireListeners();
}
async function pollRemote() {
    try {
        applyRemoteState(await get<QueueState>('/queue/state'));
    }
    catch {
    }
}
function stateMock(): QueueState {
    const s = loadLocal();
    if (!s) {
        const fresh = { seq: 0, tasks: [], lastTick: 0, lastDone: 0 };
        seed(fresh);
        saveLocal(fresh);
        return fresh;
    }
    return s;
}
export const pkQueue = {
    state: (): QueueState => (USE_MOCK ? stateMock() : queueView),
    onChange(fn: Listener) {
        listeners.push(fn);
    },
    enqueue(item: {
        name?: string;
        type?: string;
        path?: string;
        files?: number;
        size?: string;
        share_url?: string;
        share_code?: string;
        include_subdirs?: boolean;
        acc_id?: number | null;
        file_paths?: string[];
        rename?: string;
        with_shell?: boolean;
        qms_id?: number | null;
        media_off?: boolean;
        lp_event?: string;
        only_video?: boolean;
    }): number {
        if (USE_MOCK) {
            const s = stateMock();
            const t: QueueTask = {
                id: ++s.seq,
                name: item.name || '未命名资源',
                type: (item.type as QueueTask['type']) || 'quark',
                path: item.path || '/',
                files: item.files || 12,
                size: item.size || '—',
                status: 'wait',
                phase: '',
                phaseStart: 0,
                progress: 0,
                flags: {},
                doneAt: 0,
                logs: [],
            };
            s.tasks.push(t);
            saveLocal(s);
            pinnedLocalSeq = 0;
            onNewTaskStart();
            const pos = s.tasks.filter((x) => x.status === 'wait' || x.status === 'run').length;
            fireListeners();
            return pos;
        }
        const seqAtCall = queueView.seq;
        post<{
            pos: number;
        }>('/queue/tasks', item)
            .then(() => pollRemote())
            .catch(() => { });
        void seqAtCall;
        return queueView.tasks.filter((x) => x.status === 'wait' || x.status === 'run').length + 1;
    },
};
export function pkQueueCfgGet(): QueueCfg {
    if (USE_MOCK) {
        const c = loadCfgRaw();
        Object.assign(cfgView, c);
        return c;
    }
    get<QueueCfg>('/queue/config')
        .then((c) => {
        Object.assign(cfgView, c);
    })
        .catch(() => { });
    return { ...cfgView };
}
export function pkQueueCfgSet(c: QueueCfg) {
    Object.assign(cfgView, c);
    if (USE_MOCK) {
        try {
            localStorage.setItem(LOCAL_CFG_KEY, JSON.stringify(c));
        }
        catch {
        }
    }
    else {
        put<QueueCfg>('/queue/config', c)
            .then((saved) => Object.assign(cfgView, saved))
            .catch(() => { });
    }
    for (const fn of cfgListeners) {
        try {
            fn(queueView);
        }
        catch {
        }
    }
}
export function onCfgChange(fn: Listener) {
    cfgListeners.push(fn);
}
if (USE_MOCK) {
    syncView(stateMock());
    Object.assign(cfgView, loadCfgRaw());
    setInterval(tick, TICK);
}
else {
    pkQueueCfgGet();
    let pollTimer = 0;
    const startPolling = (ms: number) => {
        window.clearInterval(pollTimer);
        pollTimer = window.setInterval(() => void pollRemote(), ms);
    };
    const SSE_IDLE_POLL = 45000;
    const connectSse = () => {
        const es = new EventSource(`/api/queue/events?token=${encodeURIComponent(localStorage.getItem('pk-auth') || '')}`);
        es.onmessage = (ev) => {
            try {
                applyRemoteState(JSON.parse(ev.data) as QueueState);
                startPolling(SSE_IDLE_POLL);
            }
            catch {
            }
        };
        es.addEventListener('ping', () => startPolling(SSE_IDLE_POLL));
        es.onerror = () => {
            es.close();
            startPolling(2500);
            window.setTimeout(connectSse, 10000);
        };
    };
    void pollRemote().then(connectSse);
}
