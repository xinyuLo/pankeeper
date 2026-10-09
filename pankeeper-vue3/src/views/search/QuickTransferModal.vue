<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import { message } from 'ant-design-vue';
import { ThunderboltOutlined } from '@ant-design/icons-vue';
import { pkQueue } from '@/queue/engine';
import { DRIVE_META, DD_MEDIA } from '@/api/mock/meta';
import { listDdItems, listQmsPaths } from '@/api/modules/dd';
import { recognizeShare, type RecognizeCandidate } from '@/api/modules/recognize';
import { getSettings } from '@/api/modules/settings';
import { listAccounts } from '@/api/modules/accounts';
import { getSearchShareFiles } from '@/api/modules/search';
import { ddStore } from '@/api/mock/dd';
import RecognizePicker from '@/components/RecognizePicker.vue';
import type { DdItem, DdQmsPath, DriveType, MainDriveType } from '@/types/model';
import { useBackGuard } from '@/composables/useBackGuard';
const props = defineProps<{
    open: boolean;
    type: DriveType | null;
    shareName: string;
    shareUrl?: string;
    shareCode?: string;
}>();
const emit = defineEmits<{
    (e: 'update:open', v: boolean): void;
}>();
useBackGuard(() => props.open, () => emit('update:open', false));
const items = computed<DdItem[]>(() => ddStore.items);
const qmsPaths = ref<DdQmsPath[]>([]);
const accNames = ref<Record<string, string>>({});
const selId = ref<number | null>(null);
const rename = ref('');
const renameRef = ref();
const filesLoading = ref(false);
const filesFailed = ref(false);
const fileRows = ref<{
    path: string;
    name: string;
    size: number;
}[]>([]);
const selPaths = ref<string[]>([]);
const isMovieDir = computed(() => {
    const it = currentItem.value;
    if (!it)
        return false;
    if (it.media_type === 'tv' || it.media_type === 'movie')
        return it.media_type === 'movie';
    const n = (it.name || '').toLowerCase();
    return !(n.includes('电视') || n.includes('剧'));
});
const VIDEO_EXTS = new Set([
    '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.3gp', '.ts',
    '.iso',
]);
function isVideoFile(name: string): boolean {
    const dot = name.lastIndexOf('.');
    return dot >= 0 && VIDEO_EXTS.has(name.slice(dot).toLowerCase());
}
const displayRows = computed(() => currentItem.value?.only_video ? fileRows.value.filter((f) => isVideoFile(f.name)) : fileRows.value);
const showFilePicker = computed(() => isMovieDir.value && !filesFailed.value && displayRows.value.length > 1);
const dirCheckTip = computed(() => {
    if (filesLoading.value)
        return '正在检测资源…';
    if (filesFailed.value)
        return '文件清单获取失败——链接可能已失效，无法转存';
    if (!isMovieDir.value)
        return '电视节目无需确认勾选，转存内容全部转存';
    if (displayRows.value.length <= 1)
        return '单一资源，无需勾选，直接转存';
    return `检测到 ${displayRows.value.length} 个文件，过滤 ${fileRows.value.length - displayRows.value.length} 个`;
});
async function loadShareFiles() {
    filesLoading.value = true;
    filesFailed.value = false;
    fileRows.value = [];
    selPaths.value = [];
    try {
        const meta = await getSearchShareFiles(props.type || '', props.shareUrl || '', props.shareCode || '');
        fileRows.value = (meta.files || [])
            .filter((f) => !f.is_dir)
            .map((f) => ({ path: f.path, name: f.name, size: f.size }));
    }
    catch {
        filesFailed.value = true;
    }
    finally {
        filesLoading.value = false;
    }
}
const mediaBackend = ref<'qms' | 'litepan'>('qms');
const options = computed<DdItem[]>(() => items.value
    .filter((x) => x.type === props.type)
    .sort((a, b) => (a.sort || 0) - (b.sort || 0)));
function accLabel(it: DdItem): string {
    return accNames.value[it.account] || '';
}
const selectOptions = computed(() => options.value.map((it) => {
    const acc = accLabel(it);
    const name = acc ? `${acc} · ${it.name}` : it.name;
    return {
        value: it.id,
        label: `${name}　—　${it.path}${it.is_default ? '（默认）' : ''}`,
    };
}));
const currentItem = computed<DdItem | null>(() => options.value.find((x) => x.id === selId.value) || null);
const driveName = computed(() => (props.type ? DRIVE_META[props.type as MainDriveType]?.full || props.type : ''));
function pinDefault() {
    const def = options.value.find((x) => x.is_default) || options.value[0] || null;
    selId.value = def ? def.id : null;
}
watch(() => props.open, (v) => {
    if (!v)
        return;
    rename.value = '';
    selPaths.value = [];
    void loadShareFiles();
    getSettings().then((d) => (mediaBackend.value = d.media?.backend || 'qms')).catch(() => { });
    pinDefault();
    listDdItems().catch(() => { });
    listAccounts()
        .then((rows) => {
        const m: Record<string, string> = {};
        for (const r of rows)
            m[String(r.id)] = r.alias || r.nickname || `账号#${r.id}`;
        accNames.value = m;
    })
        .catch(() => { });
    listQmsPaths().then((qs) => (qmsPaths.value = qs)).catch(() => { });
    void nextTick().then(() => {
        setTimeout(() => renameRef.value?.focus?.(), 60);
    });
});
watch(options, () => {
    if (!options.value.some((x) => x.id === selId.value))
        pinDefault();
});
function close() {
    emit('update:open', false);
}
const RECOGNIZE_STAGES: Array<[
    number,
    string
]> = [
    [3000, '正在查询相关年份…'],
    [6000, '正在比对候选准确率…'],
    [9000, '正在整理候选结果…'],
];
function recognizeTipStart() {
    message.loading({ content: '正在识别…', key: 'recognize', duration: 0 });
    return RECOGNIZE_STAGES.map(([ms, text]) => window.setTimeout(() => {
        message.loading({ content: text, key: 'recognize', duration: 0 });
    }, ms));
}
function recognizeTipDone(timers: number[], ok: boolean, text: string) {
    timers.forEach((t) => window.clearTimeout(t));
    if (ok)
        message.success({ content: text, key: 'recognize', duration: 3 });
    else
        message.warning({ content: text, key: 'recognize', duration: 4 });
}
const recognizing = ref(false);
const pickerOpen = ref(false);
const pickerCands = ref<RecognizeCandidate[]>([]);
async function onRecognize() {
    const src = (props.shareName || '').trim();
    if (!src) {
        message.warning('没有可识别的资源名');
        return;
    }
    if (recognizing.value)
        return;
    recognizing.value = true;
    const timers = recognizeTipStart();
    try {
        const r = await recognizeShare(src, src, { share_type: props.type || '', share_url: props.shareUrl || '', share_code: props.shareCode || '' });
        if (r.ok && r.media_name) {
            if (r.confident) {
                rename.value = r.media_name;
                recognizeTipDone(timers, true, r.doubt ? `已识别（存疑，请确认）：${r.media_name}` : `已识别：${r.media_name}`);
            }
            else {
                recognizeTipDone(timers, true, `识别到 ${r.candidates?.length || 0} 个候选，请选择`);
                pickerCands.value = r.candidates || [];
                pickerOpen.value = true;
            }
        }
        else {
            recognizeTipDone(timers, false, r.message || '未识别到 TMDB 条目');
        }
    }
    catch {
        recognizeTipDone(timers, false, '识别失败，请稍后重试');
    }
    finally {
        recognizing.value = false;
    }
}
function onPick(c: RecognizeCandidate) {
    rename.value = c.year ? `${c.title} (${c.year})` : c.title;
}
const pv = computed(() => {
    const it = currentItem.value;
    if (!it)
        return null;
    const raw = rename.value.trim();
    const origin = props.shareName || '分享的目录名';
    const xrows: {
        k: string;
        v: string;
        on: boolean;
    }[] = [];
    if (mediaBackend.value === 'litepan') {
        xrows.push({
            k: '推送 LitePan',
            v: it.lp_on ? `事件 ${it.lp_event || '未配置'}` : '不推送（该目录未开 LitePan 联动）',
            on: it.lp_on,
        });
        return { base: it.path, raw, origin, xrows };
    }
    if (it.qms_on) {
        const q = qmsPaths.value.find((x) => x.id === it.qms_id) || null;
        xrows.push({
            k: '触发 QMS',
            v: q ? `#${q.id} · ${DD_MEDIA[q.media_type] || q.media_type} · ${q.source_path}` : '联动已开，但刮削目录已失效',
            on: !!q,
        });
    }
    else {
        xrows.push({ k: '触发 QMS', v: '不联动（该目录未开启）', on: false });
    }
    xrows.push({ k: '触发 STRM', v: it.qms_on ? 'QMS 整理成功后自动生成' : '不生成', on: false });
    return { base: it.path, raw, origin, xrows };
});
function onOk() {
    if (filesLoading.value) {
        message.warning('正在检测资源，请稍候…');
        return;
    }
    if (filesFailed.value) {
        message.error('文件检测未通过（链接可能已失效），无法转存');
        return;
    }
    const it = currentItem.value;
    if (!it) {
        message.warning('请先选择保存位置');
        return;
    }
    const raw = rename.value.trim();
    const pos = pkQueue.enqueue({
        name: raw || props.shareName || '分享资源',
        type: it.type,
        path: it.path,
        size: '—',
        share_url: props.shareUrl,
        share_code: props.shareCode,
        rename: raw,
        with_shell: true,
        acc_id: it.account ? Number(it.account) : null,
    });
    if (pos < 0) {
        message.warning('该分享已在转存队列中，勿重复添加');
        return;
    }
    message.success(`已加入转存队列 · 当前第 ${pos} 位，完成后去右下角队列抽屉看日志`);
    close();
}
</script>

<template>
  <a-modal
    :open="open"
    :width="560"
    centered
    :title="`快速转存 · ${driveName}`"
    :footer="null"
    destroy-on-close
    @update:open="(v: boolean) => emit('update:open', v)"
  >
    <div class="qs-body">
      <div v-if="!options.length" class="qs-noconfig">
        「{{ driveName }}」还没配置转存目录，请先到「转存配置」页添加一条路径。
      </div>

      <template v-else>
        
        <div class="dd-field">
          <label class="dd-label">保存位置<i>*</i></label>
          <a-select
            v-model:value="selId"
            style="width: 100%"
            :options="selectOptions"
            placeholder="选择保存位置"
          />
        </div>

        
        <div class="dd-field">
          <label class="dd-label">文件夹更名</label>
          <a-input
            ref="renameRef"
            v-model:value="rename"
            :maxlength="80"
            placeholder="留空则用资源名新建文件夹"
            @press-enter="onOk"
          >
            <template #suffix>
              <a-button size="small" class="tm-recog" :loading="recognizing" @click="onRecognize">
                <template #icon><ThunderboltOutlined /></template>
                识别
              </a-button>
            </template>
          </a-input>
        </div>

        
        <div class="dd-field">
          <label class="dd-label">资源检测</label>
          <div class="qs-chk-tip">
            <a-spin v-if="filesLoading" size="small" />
            <span class="small muted">{{ dirCheckTip }}</span>
          </div>
        </div>
        <div v-if="showFilePicker" class="dd-field">
          <label class="dd-label">选择要转存的文件<i>*</i></label>
          <div class="qs-chk-list">
            <label v-for="f in displayRows" :key="f.path" class="qs-chk">
              <input type="checkbox" :value="f.path" v-model="selPaths" />
              <span class="qs-chk-name" :title="f.name">{{ f.name }}</span>
              <span class="qs-chk-size">{{ f.size ? (f.size / 1024 / 1024 / 1024).toFixed(1) + ' GB' : '—' }}</span>
            </label>
          </div>
        </div>

        
        <div class="dd-field">
          <div v-if="pv" class="qs-preview" :class="{ 'is-renamed': pv.raw }">
            <div class="qs-pv-hd">转存后</div>
            <div class="qs-pv-body">
              <div class="qs-pv-path">
                <span class="qs-pv-base">{{ pv.base }}</span>
                <span class="qs-pv-slash">/</span>
                <span class="qs-pv-new">{{ pv.raw || pv.origin }}</span>
              </div>
              <div v-if="pv.raw" class="qs-pv-note">
                新建文件夹 <b>{{ pv.raw }}</b> 承接分享内容（剥壳转入）
              </div>
              <div class="qs-pv-xrows">
                <div v-for="x in pv.xrows" :key="x.k" class="qs-pv-xrow">
                  <span class="qs-pv-xk">{{ x.k }}</span>
                  <span class="qs-pv-xv" :class="{ on: x.on }">{{ x.v }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </template>
    </div>

    <div class="qs-foot">
      <a-button @click="close">取消</a-button>
      <a-button
        type="primary"
        :disabled="!currentItem || filesLoading || filesFailed"
        :title="filesLoading ? '正在检测资源，请稍候…' : filesFailed ? '文件检测未通过（链接可能已失效）' : undefined"
        @click="onOk"
      >开始转存</a-button>
    </div>

    
    <RecognizePicker v-model:open="pickerOpen" :candidates="pickerCands" :source-name="shareName" @pick="onPick" />
  </a-modal>
</template>

<style scoped>
/* 表单行布局（原型 dd-field/dd-label 结构，scoped 不跨页） */
.qs-body { padding-top: 18px; }
.dd-field { margin-bottom: 16px; }
.dd-field:last-child { margin-bottom: 0; }
.dd-label { display: block; font-size: 13px; color: var(--text2); margin-bottom: 6px; }
.dd-label i { color: var(--error); font-style: normal; margin-left: 2px; }
/* 行内字段：label 和输入框同一行（「文件夹更名」） */
.dd-inline { display: flex; align-items: center; gap: 10px; }
.dd-inline > :last-child { flex: 1; }

.qs-noconfig {
  padding: 14px 16px;
  border: 1px solid var(--note-border);
  border-radius: var(--r-sm);
  background: var(--note-bg);
  color: var(--note-fg);
  font-size: 13px;
  line-height: 1.7;
}

/* 底部按钮条：与原型 dd-mfoot 对齐（右对齐 + 顶部分隔线） */
.qs-foot {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 14px 0 2px;
  margin-top: 6px;
  border-top: 1px solid var(--split);
}

/* ---- 「转存后」结果预览（search-ui 原型样式移植） ---- */
/* 电影目录内容判断：loading 行 + 多选框列表（固定高度滚动） */
.qs-chk-tip {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid var(--split);
  border-radius: 8px;
  background: var(--surface-2);
}
.qs-chk-list {
  height: 150px;
  overflow: auto;
  border: 1px solid var(--split);
  border-radius: 8px;
  background: var(--surface-2);
  padding: 5px;
}
.qs-chk {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 12.5px;
}
.qs-chk:hover { background: rgba(22, 119, 255, 0.07); }
.qs-chk-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.qs-chk-size { flex: none; color: var(--text3); font-size: 11.5px; }

.qs-preview { border: 1px solid var(--split); border-radius: 10px; background: var(--surface-2); overflow: hidden; }
.qs-pv-hd {
  display: flex; align-items: center; gap: 6px; padding: 8px 12px;
  font-size: 12px; color: var(--text3); border-bottom: 1px solid var(--split);
  background: var(--card); letter-spacing: 0.02em;
}
.qs-pv-hd::before { content: ''; width: 5px; height: 5px; border-radius: 50%; background: var(--primary); flex: none; }
.qs-pv-body { padding: 11px 12px 12px; }
/* 路径本体：等宽、字重略加，明显是「结果」而不是「备注」 */
.qs-pv-path { font-family: var(--font-mono); font-size: 13px; font-weight: 500; line-height: 1.75; word-break: break-all; color: var(--text); }
/* 前缀（保存位置）次级色，末段（本次新建的目录）主色高亮 */
.qs-pv-base { color: var(--text3); }
.qs-pv-slash { color: var(--text4); margin: 0 2px; }
.qs-pv-new {
  background: rgba(22, 119, 255, 0.1); color: var(--primary); border-radius: 6px;
  padding: 1px 7px; font-weight: 600;
  box-decoration-break: clone; -webkit-box-decoration-break: clone;
}
.qs-pv-note { margin-top: 9px; padding-top: 8px; border-top: 1px dashed var(--split); font-size: 12px; color: var(--text3); line-height: 1.65; }
.qs-pv-note b { color: var(--text2); font-weight: 500; }
/* 触发行：明说会联动什么，没配的明说「不联动/不生成」 */
.qs-pv-xrows { margin-top: 9px; padding-top: 8px; border-top: 1px dashed var(--split); display: flex; flex-direction: column; gap: 4px; }
.qs-pv-xrow { display: flex; align-items: baseline; gap: 10px; font-size: 12px; line-height: 1.65; }
.qs-pv-xk { flex: none; color: var(--text3); letter-spacing: 0.02em; }
.qs-pv-xv { color: var(--text2); word-break: break-all; }
.qs-pv-xv.on { color: var(--primary); }
/* 更名发生时：预览块换主色系，与「没改名」的常态区分 */
.qs-preview.is-renamed { border-color: rgba(22, 119, 255, 0.35); }
.qs-preview.is-renamed .qs-pv-hd { color: var(--primary); }

/* 暗色：高亮是硬编码浅色底，深色上几乎看不见，必须单独换（原型实测踩坑） */
html[data-theme='dark'] .qs-pv-new { background: rgba(64, 150, 255, 0.2); color: #91caff; }
html[data-theme='dark'] .qs-preview.is-renamed { border-color: rgba(64, 150, 255, 0.34); }

/* 移动端（<768px）：行内字段（文件夹更名）改上下结构 */
@media (max-width: 767px) {
  .qs-body { padding-top: 12px; }
  .dd-inline { display: block; }
  .dd-inline .dd-label { margin-bottom: 6px !important; }
}
/* 识别按钮：淡紫描边 + 闪电图标（与普通转存弹窗同款），图标贴紧文字 */
.tm-recog {
  color: #8c73e6;
  border-color: rgba(140, 115, 230, 0.5);
  background: rgba(140, 115, 230, 0.06);
  box-shadow: none;
}
.tm-recog:hover, .tm-recog:focus-visible {
  color: #7451d8;
  border-color: #8c73e6;
  background: rgba(140, 115, 230, 0.12);
}
.tm-recog :deep(.ant-btn-icon + span) { margin-inline-start: 4px; }
.tm-recog :deep(.anticon) { margin-inline-end: 0; }
</style>
