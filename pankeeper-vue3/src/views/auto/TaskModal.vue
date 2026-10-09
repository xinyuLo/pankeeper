<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { message } from 'ant-design-vue';
import AutoDirModal from './AutoDirModal.vue';
import CronPicker from './CronPicker.vue';
import { DD_MEDIA, DRIVE_META } from '@/api/mock/meta';
import { accountStore } from '@/api/mock/accounts';
import { listDdItems, listQmsPaths, listStrmPaths } from '@/api/modules/dd';
import { getSettings } from '@/api/modules/settings';
import { cronHuman, extractShareCode, getPaExtras, parseShare, savePaTask, type PaExtras, } from '@/api/modules/tasks';
import type { DdQmsPath, DdStrmPath, MainDriveType, PaTask } from '@/types/model';
import { useBackGuard } from '@/composables/useBackGuard';
const props = defineProps<{
    open: boolean;
    type: MainDriveType;
    task: PaTask | null;
    suspended?: boolean;
}>();
const emit = defineEmits<{
    (e: 'update:open', v: boolean): void;
    (e: 'saved', task: PaTask): void;
}>();
useBackGuard(() => props.open, () => emit('update:open', false));
const meta = computed(() => DRIVE_META[props.type]);
const editing = computed(() => !!(props.task && props.task.id));
const accId = ref<number | null>(null);
const name = ref('');
const shareUrl = ref('');
const shareCode = ref('');
const saveDir = ref('');
const comparePath = ref('');
const includeSub = ref(true);
const postQms = ref(false);
const qmsId = ref<number | null>(null);
const postLp = ref(false);
const lpEvent = ref('');
const mediaBackend = ref<'qms' | 'litepan'>('qms');
const strmId = ref<number | null>(null);
const cron = ref('0 3 * * *');
const regPat = ref('');
const parsing = ref(false);
const drillOn = ref(false);
const drill = ref<string[]>([]);
const accOptions = computed(() => accountStore.accounts
    .filter((a) => a.type === props.type)
    .map((a) => ({ value: a.id, label: a.alias || a.nickname || `${DRIVE_META[a.type].name}#${a.id}`, def: !!a.is_default })));
function pickDefaultAcc(): number | null {
    const list = accOptions.value;
    if (!list.length)
        return null;
    return (list.find((a) => a.def) || list[0]).value;
}
const qmsPaths = ref<DdQmsPath[]>([]);
const strmPaths = ref<DdStrmPath[]>([]);
function qmsLabel(p: DdQmsPath): string {
    return `#${p.id} · ${DD_MEDIA[p.media_type] || p.media_type || '未分类'} · ${p.source_path}`;
}
function strmLabel(p: DdStrmPath): string {
    return `#${p.id} · ${p.remote_path}`;
}
const qmsOpts = computed(() => qmsPaths.value.map((p) => ({ value: p.id, label: qmsLabel(p) })));
const strmOpts = computed(() => strmPaths.value.map((p) => ({ value: p.id, label: strmLabel(p) })));
const cronTextValue = computed(() => cronHuman(cron.value, '未设置'));
watch(shareUrl, (v) => {
    const code = extractShareCode(v);
    if (code)
        shareCode.value = code;
});
async function onParse() {
    const url = shareUrl.value.trim();
    if (!url) {
        message.warning('请先输入分享链接');
        return;
    }
    const code = extractShareCode(url);
    if (code)
        shareCode.value = code;
    try {
        parsing.value = true;
        const r = await parseShare(props.type, url, shareCode.value.trim());
        const sub = r.total - r.count;
        message.success(sub > 0
            ? `解析成功：根层 ${r.total} 项（含 ${sub} 个子目录），子目录内容按「包含子目录」设置在执行时一起转存`
            : `解析成功，共 ${r.count} 个文件`);
    }
    catch (e: unknown) {
        const detail = (e as {
            response?: {
                data?: {
                    detail?: string;
                };
            };
        })?.response?.data?.detail;
        message.error(detail || '解析失败：链接无效或网盘连接异常');
    }
    finally {
        parsing.value = false;
    }
}
const dirOpen = ref(false);
const cronPickOpen = ref(false);
const dirTarget = ref<'save' | 'compare'>('save');
const dirInitial = ref('');
function browse(target: 'save' | 'compare') {
    dirTarget.value = target;
    dirInitial.value = target === 'save' ? saveDir.value : comparePath.value;
    dirOpen.value = true;
}
function onPicked(path: string) {
    if (dirTarget.value === 'save')
        saveDir.value = path;
    else
        comparePath.value = path;
}
watch(() => props.open, async (v) => {
    if (!v)
        return;
    const t = props.task;
    const ex: PaExtras = t ? getPaExtras(t.id) : { regex: [{ pat: '', rep: '' }], drill_on: false, drill: [], qms_id: null, strm_id: null, lp_event: '' };
    accId.value = t?.acc_id ?? pickDefaultAcc();
    name.value = t?.name || '';
    shareUrl.value = t?.share_url || '';
    shareCode.value = t?.share_code || '';
    saveDir.value = t?.save_dir || '';
    comparePath.value = t?.compare_path || '';
    includeSub.value = t ? t.include_subdirs : true;
    postQms.value = !!t?.post_qms;
    cron.value = t ? t.cron || '' : '0 3 * * *';
    const firstRule = ex.regex[0];
    regPat.value = firstRule?.pat || '';
    drillOn.value = ex.drill_on;
    drill.value = [...ex.drill];
    qmsId.value = ex.qms_id;
    strmId.value = ex.strm_id;
    postLp.value = Boolean((ex.lp_event || '').trim());
    lpEvent.value = ex.lp_event || '';
    getSettings().then((d) => (mediaBackend.value = d.media?.backend || 'qms')).catch(() => { });
    void syncMediaSelection(t);
});
async function syncMediaSelection(t: PaTask | null) {
    if (!postQms.value)
        return;
    const dir = (t?.save_dir || saveDir.value || '').replace(/\/+$/, '');
    try {
        const items = await listDdItems();
        const hit = items
            .filter((d) => d.type === props.type && dir && (dir === d.path || dir.startsWith(d.path.replace(/\/+$/, '') + '/')))
            .sort((a, b) => b.path.length - a.path.length)[0];
        if (hit && !hit.qms_on) {
            if (qmsId.value != null || strmId.value != null) {
                qmsId.value = null;
                strmId.value = null;
                message.info('该保存目录已在「转存配置」里关闭 QMS 联动，任务内的目录选择已清空（等于没配）');
            }
            return;
        }
    }
    catch {
    }
    try {
        const [qs, ss] = await Promise.all([listQmsPaths(), listStrmPaths()]);
        const qIds = new Set(qs.map((x) => x.id));
        const sIds = new Set(ss.map((x) => x.id));
        if (qmsId.value != null && !qIds.has(qmsId.value))
            qmsId.value = null;
        if (strmId.value != null && !sIds.has(strmId.value))
            strmId.value = null;
    }
    catch {
    }
}
const pathsLoading = ref(false);
watch(postQms, async (on) => {
    if (!on || qmsPaths.value.length || pathsLoading.value)
        return;
    pathsLoading.value = true;
    try {
        qmsPaths.value = await listQmsPaths();
        strmPaths.value = await listStrmPaths();
    }
    catch {
        await new Promise((r) => setTimeout(r, 800));
        try {
            qmsPaths.value = await listQmsPaths();
            strmPaths.value = await listStrmPaths();
        }
        catch {
        }
    }
    finally {
        pathsLoading.value = false;
    }
});
async function onSave() {
    if (!name.value.trim() || !shareUrl.value.trim()) {
        message.error('请填写任务名称和分享链接（必填）');
        return;
    }
    if (accId.value == null) {
        message.error(`该网盘还没有已连接的账号，请先到「网盘连接」配置`);
        return;
    }
    const t = props.task;
    const task: PaTask = {
        id: t?.id ?? 0,
        type: props.type,
        acc_id: accId.value,
        name: name.value.trim(),
        enabled: t ? t.enabled : true,
        share_url: shareUrl.value.trim(),
        share_code: shareCode.value.trim(),
        save_dir: saveDir.value,
        compare_path: comparePath.value,
        include_subdirs: includeSub.value,
        cron: cron.value.trim(),
        exclude_count: t ? t.exclude_count : 0,
        exclIdx: t ? [...t.exclIdx] : [],
        last_run: t ? t.last_run : '',
        last_status: t ? t.last_status : 'never',
        last_result: t ? t.last_result : '',
        post_qms: postQms.value,
    };
    const extras: PaExtras = {
        regex: regPat.value.trim() ? [{ pat: regPat.value.trim(), rep: '' }] : [],
        drill_on: drillOn.value,
        drill: [...drill.value],
        qms_id: postQms.value ? qmsId.value ?? null : null,
        strm_id: postQms.value ? strmId.value ?? null : null,
        lp_event: mediaBackend.value === 'litepan' && postLp.value ? lpEvent.value.trim() : '',
    };
    const saved = await savePaTask(task, extras);
    message.success(`保存成功：${saved.name}`);
    emit('saved', saved);
    close();
}
function close() {
    if (dirOpen.value)
        dirOpen.value = false;
    emit('update:open', false);
}
function onKey(e: KeyboardEvent) {
    if (e.key !== 'Escape' || props.suspended)
        return;
    if (dirOpen.value) {
        dirOpen.value = false;
        return;
    }
    close();
}
watch(() => props.open, (v) => {
    if (v)
        window.addEventListener('keydown', onKey);
    else
        window.removeEventListener('keydown', onKey);
});
onBeforeUnmount(() => window.removeEventListener('keydown', onKey));
</script>

<template>
  <teleport to="body">
    <div v-if="open" class="mt-mask" style="z-index: 1000" @click.self="close">
      <div class="mt-dialog">
        <div class="mt-dialog-head">
          <span class="mt-color-dot" :style="{ background: meta.color }"></span>
          <span class="mt-dialog-title">{{ editing ? '编辑' : '新增' }}自动转存任务</span>
          <span class="tag" :class="meta.tag" style="margin-right: 0">{{ meta.full }}</span>
          <button class="mt-close" title="关闭" @click="close">×</button>
        </div>
        <div class="mt-dialog-body">
          
          <div class="mt-form-row">
            <label class="mt-label">任务名称</label>
            <div class="mt-control">
              <input v-model="name" class="mt-input" />
            </div>
          </div>

          
          <div class="mt-form-row">
            <label class="mt-label">所属账号</label>
            <div class="mt-control">
              <a-select
                v-model:value="accId"
                :options="accOptions"
                style="width: 260px"
                :disabled="!accOptions.length"
                :placeholder="accOptions.length ? '选择账号' : '该网盘暂无已连接账号'"
              />
              <div class="mt-hint">多账号时在这里选；「默认」在「网盘连接」页设置</div>
            </div>
          </div>

          
          <div class="mt-form-row">
            <label class="mt-label">分享链接</label>
            <div class="mt-control">
              <div class="mt-row-inline">
                <input v-model="shareUrl" class="mt-input" />
                <button class="mt-btn mt-btn-inline mt-btn-soft" @click="onParse">解析</button>
              </div>
              <div class="mt-hint">链接末尾带 <code>?pwd=</code> 会自动识别提取码</div>
            </div>
          </div>

          
          <div class="mt-form-row">
            <label class="mt-label">提取码</label>
            <div class="mt-control">
              <input v-model="shareCode" class="mt-input" style="max-width: 220px" readonly placeholder="解析后自动填写" title="粘贴带 ?pwd= 的链接或点「解析」自动识别" />
            </div>
          </div>

          
          <div class="mt-form-row">
            <label class="mt-label">保存到</label>
            <div class="mt-control">
              <div class="mt-row-inline">
                <input :value="saveDir" class="mt-input" placeholder="选择网盘目录" readonly title="点击「浏览」选择目录" />
                <button class="mt-btn mt-btn-inline mt-btn-soft" @click="browse('save')">浏览</button>
              </div>
            </div>
          </div>

          
          <div class="mt-form-row">
            <label class="mt-label">对比路径</label>
            <div class="mt-control">
              <div class="mt-row-inline">
                <input :value="comparePath" class="mt-input" placeholder="用于去重对比的目录" readonly title="点击「浏览」选择目录" />
                <button class="mt-btn mt-btn-inline mt-btn-soft" @click="browse('compare')">浏览</button>
              </div>
              <div class="mt-hint">转存前会与该路径下的文件做去重对比（先 MD5 后文件名）。</div>
            </div>
          </div>

          
          <div class="mt-form-row">
            <label class="mt-label">包含子目录</label>
            <div class="mt-control">
              <div class="mt-opts">
                <label class="mt-opt">
                  <input v-model="includeSub" type="checkbox" /><span class="mt-box"></span>分享内有子目录时一并转存
                </label>
              </div>
            </div>
          </div>

          
          <div v-if="mediaBackend === 'litepan'" class="mt-form-row" style="align-items: flex-start">
            <label class="mt-label">LitePan 联动</label>
            <div class="mt-control">
              <div class="mt-opts">
                <label class="mt-switch-row">
                  <label class="mt-switch"><input v-model="postLp" type="checkbox" /><span class="mt-switch-slider"></span></label>
                  <span class="mt-switch-label">转存完成后推送 LitePan</span>
                </label>
              </div>
              <div v-if="postLp" style="margin-top: 10px">
                <a-input v-model:value="lpEvent" :maxlength="80" placeholder="LitePan 事件名，留空则按转存配置里配的" />
                <div class="mt-hint">须与 LitePan 自动化规则里配的事件名一致；留空则按转存配置目录配的事件，目录也没配就不推送。</div>
              </div>
            </div>
          </div>
          <div v-else class="mt-form-row" style="align-items: flex-start">
            <label class="mt-label">QMS 联动</label>
            <div class="mt-control">
              <div class="mt-opts">
                <label class="mt-switch-row">
                  <label class="mt-switch"><input v-model="postQms" type="checkbox" /><span class="mt-switch-slider"></span></label>
                  <span class="mt-switch-label">转存完成后联动 QMS 整理</span>
                </label>
              </div>
              <div v-if="postQms" style="margin-top: 10px">
                <a-select
                  v-model:value="qmsId"
                  :options="qmsOpts"
                  style="width: 100%; margin-bottom: 8px"
                  :loading="pathsLoading"
                  :placeholder="pathsLoading ? '正在加载 QMS 目录…' : qmsPaths.length ? '选择 QMS 刮削目录' : 'QMS 未连接或没有刮削目录'"
                  allow-clear
                />
                <a-select
                  v-model:value="strmId"
                  :options="strmOpts"
                  style="width: 100%"
                  :loading="pathsLoading"
                  :placeholder="pathsLoading ? '正在加载 STRM 目录…' : strmPaths.length ? '选择 STRM 同步目录' : 'QMS 未连接或没有同步目录'"
                  allow-clear
                />
                <div class="mt-hint">转存 → QMS → STRM，触发间隔 15s；不需要 STRM 就不选。</div>
              </div>
            </div>
          </div>

          
          <div class="mt-form-row" style="align-items: flex-start">
            <label class="mt-label">正则过滤</label>
            <div class="mt-control">
              <input v-model="regPat" class="mt-input" placeholder="匹配模式（正则），如 \.mkv$" />
              <div class="mt-hint">留空 = 不过滤；填了模式，只转存文件名匹配的文件。</div>
            </div>
          </div>

          

          
          <div class="mt-form-row">
            <label class="mt-label">定时表达式</label>
            <div class="mt-control">
              <div class="mt-row-inline">
                <input v-model="cron" class="mt-input" placeholder="0 3 * * *" />
                <button class="mt-btn mt-btn-inline mt-btn-soft" @click="cronPickOpen = true">选择</button>
              </div>
              <div class="mt-hint">可直接输入，或点「选择」用模式拼一个（分 时 日 月 周）。</div>
              <div class="mt-hint mt-hint-strong">{{ cronTextValue }}</div>
            </div>
          </div>
        </div>
        <div class="mt-dialog-foot">
          <button class="mt-btn" @click="close">取消</button>
          <button class="mt-btn mt-btn-primary" @click="onSave">保存</button>
        </div>
      </div>
    </div>

    
    <AutoDirModal v-model:open="dirOpen" :type="type" :initial="dirInitial" :acc-id="accId" @picked="onPicked" />
    
    <CronPicker v-model:open="cronPickOpen" :cron="cron" @save="(c: string) => (cron = c)" />
  </teleport>
</template>

<style scoped src="./mt-modal.css"></style>
