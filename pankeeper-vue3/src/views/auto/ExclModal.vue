<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { message } from 'ant-design-vue';
import { CheckSquareOutlined, ReloadOutlined } from '@ant-design/icons-vue';
import { DRIVE_META } from '@/api/mock/meta';
import { commitExcl, fetchExclFiles, fmtHms, type PaExclFetch, type PaExclFile } from '@/api/modules/tasks';
import type { MainDriveType, PaTask } from '@/types/model';
import { useBackGuard } from '@/composables/useBackGuard';
const props = defineProps<{
    open: boolean;
    task: PaTask | null;
}>();
const emit = defineEmits<{
    (e: 'update:open', v: boolean): void;
    (e: 'committed'): void;
}>();
useBackGuard(() => props.open, () => emit('update:open', false));
const meta = computed(() => (props.task ? DRIVE_META[props.task.type as MainDriveType] : DRIVE_META.baidu));
const rex = computed(() => {
    const p = (props.task?.regex_pattern || '').trim();
    if (!p)
        return null;
    try {
        return new RegExp(p);
    }
    catch {
        return null;
    }
});
function isRegexOut(f: PaExclFile) {
    return !!rex.value && !rex.value.test(f.name);
}
const missingCount = computed(() => {
    const saved = props.task?.exclude_names || [];
    return saved.filter((n) => !files.value.some((f) => f.name === n)).length;
});
const files = ref<PaExclFile[]>([]);
const sel = ref(new Set<number>());
const loading = ref(false);
const busy = ref(false);
const fresh = ref<boolean | null>(null);
const cacheTs = ref(0);
const statusText = computed(() => {
    if (loading.value)
        return '';
    if (fresh.value === null)
        return '○ 首次加载';
    if (fresh.value)
        return `○ 刚从网盘重新拉取 · 获取于 ${fmtHms(cacheTs.value)} · 下次转存执行后自动刷新`;
    return `● 命中缓存 · 获取于 ${fmtHms(cacheTs.value)} · 转存执行后自动刷新`;
});
function applyVisible(res: PaExclFetch, keep: string[]) {
    const r = rex.value;
    files.value = r ? res.files.filter((f) => r.test(f.name) || keep.includes(f.name)) : res.files;
    fresh.value = res.fresh;
    cacheTs.value = res.ts;
}
watch(() => props.open, async (v) => {
    if (!v || !props.task)
        return;
    const saved: string[] = props.task?.exclude_names || [];
    files.value = [];
    fresh.value = null;
    loading.value = true;
    try {
        applyVisible(await fetchExclFiles(props.task.id), saved);
        sel.value = new Set(files.value.map((f, i) => (saved.includes(f.name) ? i : -1)).filter((i) => i >= 0));
    }
    finally {
        loading.value = false;
    }
});
async function onRefresh() {
    if (busy.value || !props.task)
        return;
    busy.value = true;
    const keep = new Set(files.value.filter((_, i) => sel.value.has(i)).map((f) => f.name));
    try {
        const res = await fetchExclFiles(props.task.id, true);
        applyVisible(res, [...new Set([...(props.task.exclude_names || []), ...keep])]);
        sel.value = new Set(files.value.map((f, i) => (keep.has(f.name) ? i : -1)).filter((i) => i >= 0));
        message.success('文件清单已刷新');
    }
    finally {
        busy.value = false;
    }
}
function checkNoMd5() {
    const s = new Set<number>();
    files.value.forEach((f, i) => {
        if (!f.md5)
            s.add(i);
    });
    sel.value = s;
    if (!files.value.length)
        message.info('清单为空，没有可勾选的文件');
    else if (!s.size)
        message.info('所有文件都有 MD5 校验值，不用排除');
    else
        message.success(`已勾选 ${s.size} 个无 MD5 文件`);
}
function toggle(i: number, e: Event) {
    const s = new Set(sel.value);
    if ((e.target as HTMLInputElement).checked)
        s.add(i);
    else
        s.delete(i);
    sel.value = s;
}
async function onOk() {
    if (!props.task)
        return;
    const idxs = [...sel.value].sort((a, b) => a - b);
    const names = idxs.map((i) => files.value[i]?.name || '').filter(Boolean);
    const md5s = idxs.map((i) => files.value[i]?.md5 || '').filter(Boolean);
    await commitExcl(props.task.id, names, md5s);
    message.success(`已排除 ${names.length} 个文件`);
    emit('committed');
    close();
}
function close() {
    emit('update:open', false);
}
function onKey(e: KeyboardEvent) {
    if (e.key === 'Escape')
        close();
}
watch(() => props.open, (v) => {
    if (v)
        window.addEventListener('keydown', onKey);
    else
        window.removeEventListener('keydown', onKey);
});
onBeforeUnmount(() => {
    window.removeEventListener('keydown', onKey);
});
</script>

<template>
  <teleport to="body">
    <div v-if="open" class="mt-mask" style="z-index: 1002" @click.self="close">
      <div class="mt-dialog" style="width: 560px">
        <div class="mt-dialog-head">
          <span class="mt-color-dot" :style="{ background: meta.color }"></span>
          <span class="mt-dialog-title">排除文件清单 · {{ meta.full }}</span>
          <button class="mt-close" title="关闭" @click="close">×</button>
        </div>
        <div class="mt-dialog-body">
          <div class="mt-section">
            <div class="mt-section-head">
              <span class="mt-section-title">候选文件</span>
              
              <div class="mt-head-btns" :class="{ busy: busy }">
                <button class="mt-btn mt-btn-sm mt-btn-soft" title="勾选全部无校验值的文件（转存时只能按文件名去重，最值得排除）" @click="checkNoMd5">
                  <CheckSquareOutlined />一键勾选无 MD5
                </button>
                <button class="mt-btn mt-btn-sm mt-btn-soft" title="忽略缓存，重新拉取文件清单（点完即用最新数据）" @click="onRefresh">
                  <ReloadOutlined />刷新
                </button>
              </div>
            </div>

            
            <div class="mt-listcache">
              <span class="mt-listcache-txt" :class="fresh === false ? 'hit' : fresh === true ? 'cold' : ''">
                <template v-if="loading"><span class="mt-spin"></span>正在获取文件清单…</template>
                <template v-else>{{ statusText }}</template>
              </span>
            </div>

            <div class="mt-check-list">
              <label v-for="(f, i) in files" :key="f.name" class="mt-check-item" :class="{ 'mt-checked': sel.has(i) }">
                <input type="checkbox" :checked="sel.has(i)" @change="toggle(i, $event)" />
                <span class="mt-check-name">{{ f.name }}</span>
                <span v-if="isRegexOut(f)" class="mt-tag mt-tag-dim">正则外</span>
                <span v-if="!f.md5" class="mt-tag">无 MD5</span>
              </label>
              <div v-if="!files.length && !loading" class="mt-list-foot"><span>清单为空</span></div>
            </div>
            <div class="mt-list-foot">
              <span>已选 {{ sel.size }} 个</span>
              <span v-if="missingCount" class="mt-list-warn">另 {{ missingCount }} 个已排除文件不在当前清单，点「确定」后将移出排除清单</span>
              <span v-else class="mt-section-tip">勾选的文件在转存时会被排除</span>
            </div>
          </div>
          <div class="mt-hint">候选为正则命中的文件 + 已勾选的排除项：正则匹配不上的文件不会转存，只保留已勾选的方便取消；「无 MD5」的文件无校验值，转存时按文件名去重。</div>
        </div>
        <div class="mt-dialog-foot">
          <button class="mt-btn" @click="close">取消</button>
          <button class="mt-btn mt-btn-primary" @click="onOk">确定</button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<style scoped src="./mt-modal.css"></style>
