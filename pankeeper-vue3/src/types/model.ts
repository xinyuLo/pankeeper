export type DriveType = 'baidu' | 'quark' | '115' | '123' | 'ali' | 'xunlei' | 'uc' | 'magnet';
export type MainDriveType = 'baidu' | 'quark' | '115';
export interface PaTask {
    id: number;
    type: MainDriveType;
    acc_id: number | null;
    name: string;
    enabled: boolean;
    share_url: string;
    share_code: string;
    save_dir: string;
    compare_path?: string;
    include_subdirs: boolean;
    cron: string;
    exclude_count: number;
    exclIdx: number[];
    regex_pattern?: string;
    exclude_names?: string[];
    exclude_md5s?: string[];
    last_run: string;
    last_status: 'success' | 'partial' | 'fail' | 'running' | 'never';
    last_result: string;
    post_qms: boolean;
    post_notify?: boolean;
}
export type QueueTaskStatus = 'wait' | 'run' | 'done' | 'fail' | 'warn';
export type QueuePhase = 'transfer' | 'waitqms' | 'qms' | 'waitstrm' | 'strm' | '';
export interface QueueLogLine {
    lv: 'STEP' | 'INFO' | 'WARN' | 'ERROR';
    txt: string;
}
export interface QueueTask {
    id: number;
    name: string;
    type: MainDriveType;
    path: string;
    files: number;
    size: string;
    status: QueueTaskStatus;
    phase: QueuePhase;
    phaseStart: number;
    progress: number;
    flags: Record<string, number>;
    doneAt: number;
    paTaskId?: number | null;
    source?: string;
    logs: QueueLogLine[];
}
export interface QueueState {
    seq: number;
    lastTick: number;
    lastDone: number;
    tasks: QueueTask[];
}
export interface QueueCfg {
    threads: number;
    gap: number;
    qms: number;
    strm: number;
}
export interface DdItem {
    id: number;
    type: MainDriveType;
    account: string;
    sort: number;
    name: string;
    path: string;
    is_default: boolean;
    qms_on: boolean;
    qms_id: number | null;
    strm_id: number | null;
    lp_on: boolean;
    lp_event: string;
    media_type: 'movie' | 'tv' | '';
    only_video: boolean;
}
export type DdItemDraft = Omit<DdItem, 'id'> & {
    id?: number | null;
};
export interface DdQmsPath {
    id: number;
    media_type: string;
    source_path: string;
}
export interface DdStrmPath {
    id: number;
    remote_path: string;
}
export interface DdAccount {
    id: string;
    label: string;
}
export interface RecordTag {
    st: string;
    cls: 't-ok' | 't-bad' | 't-off';
}
export interface RecordFileSnap {
    path: string;
    name: string;
    size: number;
    st: string;
}
export interface RecordItem {
    n: string;
    t: DriveType;
    p: string;
    st: string;
    cls: 't-ok' | 't-bad' | 't-off' | 't-warn';
    tm: string;
    qms: RecordTag;
    strm: RecordTag;
    backend?: string;
    files?: RecordFileSnap[];
}
export type AccountStatus = 'connected' | 'expired' | 'unset';
export interface AccountInfo {
    type: MainDriveType;
    cred_kind: string;
    status: AccountStatus;
    last_check: string;
    notify: boolean;
}
export interface AccountSummary {
    capacity: {
        total: number;
        used: number;
    } | null;
    vip: {
        name: string;
        expires: string | null;
    } | null;
}
export interface SearchResultItem {
    n: string;
    t: DriveType;
    s: string;
    d: string;
    ok: boolean;
    hot?: boolean;
    url?: string;
    share_code?: string;
    source?: string;
}
export interface TreeNode {
    name: string;
    path?: string;
    size?: string;
    kids?: TreeNode[];
}
