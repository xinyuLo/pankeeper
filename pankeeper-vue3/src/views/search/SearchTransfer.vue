<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { useBackGuard } from '@/composables/useBackGuard';
import { useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { CopyOutlined, FolderOpenOutlined, SearchOutlined } from '@ant-design/icons-vue';
import PkPager from '@/components/PkPager.vue';
import QuickTransferModal from './QuickTransferModal.vue';
import TransferModal, { type TransferTarget } from './TransferModal.vue';
import ShareFilesModal from '@/views/auto/ShareFilesModal.vue';
import { useIsMobile } from '@/composables/useIsMobile';
import { checkShareLink, getInitialResults, getPanSouAddr, getRecentKeywords, getSearchChannels, getSearchResults, getSearchShareFiles, type SearchChannel, getEngineHealth, getEngineHealthCached } from '@/api/modules/search';
import { getSettings, saveSearchSrc } from '@/api/modules/settings';
import { listDdItems } from '@/api/modules/dd';
import { ddStore } from '@/api/mock/dd';
import { DRIVE_META } from '@/api/mock/meta';
import { loadSearchCache, saveSearchCache } from '@/views/search/searchCache';
import type { DriveType, SearchResultItem } from '@/types/model';
const isMobile = useIsMobile();
const DRIVE_ORDER: DriveType[] = ['baidu', 'quark', '115', 'magnet', '123', 'ali', 'xunlei', 'uc'];
const T_NO_CRED = '请先到「网盘连接」页配置该网盘凭据';
const FALLBACK_WORDS = ['狂飙', '哪吒2', 'F1：狂飙飞车', '兰香如故', '飞驰人生2'];
const sampleWords = ref<string[]>(FALLBACK_WORDS);
function searchSample(word: string) {
    if (busy.value)
        return;
    kw.value = word;
    void doSearch();
}
const T_NO_DD = '请先到「转存配置」页给该网盘添加一个路径';
const router = useRouter();
const kw = ref('');
const channels = ref<SearchChannel[]>([]);
const addr = ref('');
const addrReady = ref(false);
const pansouMissing = computed(() => addrReady.value && !addr.value.trim());
const booted = ref(false);
function goPansouCfg() {
    router.push({ name: 'settings' });
}
const engineOk = ref<boolean | null>(null);
const results = ref<SearchResultItem[]>([]);
const ddTypes = computed(() => new Set<DriveType>(ddStore.items.map((x) => x.type)));
onMounted(async () => {
    const [chs, a, rows, , cached] = await Promise.all([
        getSearchChannels().catch(() => []),
        getPanSouAddr().catch(() => ''),
        getInitialResults().catch(() => []),
        listDdItems().catch(() => undefined),
        getEngineHealthCached().catch(() => ({ ok: null as boolean | null, checked_at: '' })),
    ]);
    channels.value = chs;
    addr.value = a;
    addrReady.value = true;
    engineOk.value = cached.ok;
    getEngineHealth()
        .then((h) => {
        if (h.ok || engineOk.value !== true)
            engineOk.value = h.ok;
    })
        .catch(() => {
        if (engineOk.value === null)
            engineOk.value = false;
    });
    results.value = rows;
    const cachedSearch = loadSearchCache();
    if (cachedSearch) {
        kw.value = cachedSearch.kw;
        active.value = cachedSearch.active;
        elapsed.value = cachedSearch.elapsed;
        results.value = cachedSearch.results;
    }
    renderStats();
    getRecentKeywords(5)
        .then((words) => {
        if (words.length)
            sampleWords.value = words;
    })
        .catch(() => { });
    booted.value = true;
});
const chCfgOpen = ref(false);
const chFilter = ref('');
const chDraft = ref<string[]>([]);
const selectedChannels = computed(() => channels.value.filter((c) => c.on).map((c) => c.name));
function openChannelCfg() {
    chDraft.value = [...selectedChannels.value];
    chFilter.value = '';
    chCfgOpen.value = true;
}
const chFiltered = computed(() => {
    const kwTrim = chFilter.value.trim().toLowerCase();
    return channels.value.filter((c) => !kwTrim || c.name.toLowerCase().includes(kwTrim));
});
function chSetAll(on: boolean) {
    for (const c of channels.value)
        c.on = on;
    chDraft.value = channels.value.filter((c) => c.on).map((c) => c.name);
}
function chToggle(name: string, on: boolean) {
    const set = new Set(chDraft.value);
    if (on)
        set.add(name);
    else
        set.delete(name);
    chDraft.value = [...set];
}
async function chSave() {
    const draft = new Set(chDraft.value);
    for (const c of channels.value)
        c.on = draft.has(c.name);
    try {
        const all = await getSettings();
        all.search.channels = [...draft];
        await saveSearchSrc(all.search);
        chCfgOpen.value = false;
        message.success(draft.size ? `已保存：搜索时使用 ${draft.size} 个频道` : '已保存：使用全部频道');
    }
    catch {
        message.error('保存频道设置失败');
    }
}
type TabKey = 'all' | DriveType;
const active = ref<TabKey>('all');
const countsBy = computed<Record<string, number>>(() => {
    const c: Record<string, number> = { all: results.value.length };
    for (const t of DRIVE_ORDER)
        c[t] = 0;
    for (const r of results.value)
        c[r.t] = (c[r.t] || 0) + 1;
    return c;
});
const tabs = computed(() => [
    { key: 'all' as TabKey, name: '全部', count: countsBy.value.all, color: '' },
    ...DRIVE_ORDER.map((t) => ({ key: t as TabKey, name: DRIVE_META[t].name, count: countsBy.value[t] || 0, color: DRIVE_META[t].color })),
]);
function setTab(k: TabKey) {
    if (k === active.value)
        return;
    active.value = k;
    page.value = 1;
}
function qualityScore(name: string): number {
    const n = name.toUpperCase();
    let s = 0;
    if (/2160P?|4K/.test(n))
        s += 400;
    else if (/1080[P】]?|1080/.test(n))
        s += 300;
    else if (/720/.test(n))
        s += 200;
    if (/REMUX|原盘|BLURAY|BLU-RAY|BDMV|UHD/.test(n))
        s += 50;
    else if (/WEB-?DL/.test(n))
        s += 30;
    else if (/WEB/.test(n))
        s += 20;
    else if (/HDTV/.test(n))
        s += 10;
    if (/ATMOS|全景声|TRUEHD|DDP|DD\+|杜比|EAC3|AC3/.test(n))
        s += 30;
    else if (/DTS-?HD|DTS/.test(n))
        s += 26;
    if (/7\.1/.test(n))
        s += 20;
    else if (/5\.1/.test(n))
        s += 12;
    else if (/2\.0/.test(n))
        s += 4;
    if (/杜比视界|DOLBY.?VISION|\bDV\b/.test(n))
        s += 15;
    else if (/HDR10\+|HDR/.test(n))
        s += 10;
    if (/H\.?265|HEVC|X265/.test(n))
        s += 6;
    else if (/H\.?264|X264|AVC/.test(n))
        s += 3;
    return s;
}
const driveRank = computed<Record<string, number>>(() => {
    const r: Record<string, number> = {};
    DRIVE_ORDER.forEach((t, i) => (r[t] = i));
    return r;
});
const filtered = computed(() => {
    const rank = driveRank.value;
    const sorted = [...results.value].sort((a, b) => {
        if (active.value === 'all') {
            const d = (rank[a.t] ?? 99) - (rank[b.t] ?? 99);
            if (d !== 0)
                return d;
        }
        return qualityScore(b.n) - qualityScore(a.n);
    });
    return active.value === 'all' ? sorted : sorted.filter((r) => r.t === active.value);
});
const page = ref(1);
const size = ref(8);
const paged = computed(() => filtered.value.slice((page.value - 1) * size.value, page.value * size.value));
watch(() => filtered.value.length, (n) => {
    const max = Math.max(1, Math.ceil(n / size.value));
    if (page.value > max)
        page.value = max;
});
const busy = ref(false);
const scanCount = ref(1);
const elapsed = ref<string | null>(null);
const rowEpoch = ref(0);
let scanTimer: number | undefined;
const kwRef = ref();
async function doSearch() {
    if (busy.value)
        return;
    if (pansouMissing.value) {
        message.error('还没配置 PanSou 搜索服务，请到「系统设置 → 搜索源」填写 PanSou 地址', 5);
        return;
    }
    if (!kw.value.trim()) {
        message.warning('请输入搜索关键词');
        return;
    }
    busy.value = true;
    scanCount.value = 1;
    elapsed.value = null;
    page.value = 1;
    kwRef.value?.blur?.();
    scanTimer = window.setInterval(() => {
        scanCount.value = Math.min(8, scanCount.value + 1);
    }, 210);
    const t0 = Date.now();
    try {
        results.value = await getSearchResults(kw.value);
        elapsed.value = ((Date.now() - t0) / 1000).toFixed(1) + 's';
        rowEpoch.value++;
    }
    catch (e: unknown) {
        const detail = (e as {
            response?: {
                data?: {
                    detail?: string;
                };
            };
        })?.response?.data?.detail;
        message.error(detail || '检索失败，请检查「系统设置 → 搜索源」里的 PanSou 地址', 5);
        results.value = [];
    }
    finally {
        window.clearInterval(scanTimer);
        busy.value = false;
        renderStats();
        if (elapsed.value)
            message.success(`命中 ${results.value.length} 条结果 · 耗时 ${elapsed.value}`);
    }
}
interface StatCard {
    key: string;
    label: string;
    value: number | string;
    accent?: string;
}
const statCards = ref<StatCard[]>([]);
const statShown = ref<Record<string, number | string>>({});
const statEpoch = ref(0);
let rollRaf = 0;
function renderStats() {
    const list: StatCard[] = [{ key: 'all', label: '命中资源', value: results.value.length }];
    for (const t of DRIVE_ORDER) {
        list.push({ key: t, label: DRIVE_META[t].full, value: countsBy.value[t] || 0, accent: DRIVE_META[t].color });
    }
    statCards.value = list;
    statEpoch.value++;
    rollStats(list);
}
function rollStats(cards: StatCard[]) {
    cancelAnimationFrame(rollRaf);
    const nums = cards.filter((c): c is StatCard & {
        value: number;
    } => typeof c.value === 'number');
    for (const c of cards)
        statShown.value[c.key] = typeof c.value === 'number' ? 0 : c.value;
    if (!nums.length)
        return;
    const dur = 620;
    const t0 = performance.now();
    const step = (ts: number) => {
        const p = Math.min(1, (ts - t0) / dur);
        const e = 1 - Math.pow(1 - p, 3);
        for (const c of nums)
            statShown.value[c.key] = Math.round(c.value * e);
        if (p < 1)
            rollRaf = requestAnimationFrame(step);
        else
            for (const c of nums)
                statShown.value[c.key] = c.value;
    };
    rollRaf = requestAnimationFrame(step);
}
function hasDD(t: DriveType) {
    return ddTypes.value.has(t);
}
const checkingRows = ref(new Set<string>());
function rowKeyOf(r: SearchResultItem) {
    return `${r.t}|${r.url}`;
}
function isChecking(r: SearchResultItem) {
    return checkingRows.value.has(rowKeyOf(r));
}
async function aliveOrBlock(r: SearchResultItem): Promise<boolean> {
    if (!r.url || r.t === 'magnet')
        return true;
    const key = rowKeyOf(r);
    if (checkingRows.value.has(key))
        return false;
    checkingRows.value = new Set(checkingRows.value).add(key);
    let tipShown = false;
    const tipTimer = window.setTimeout(() => {
        tipShown = true;
        message.loading({ content: '正在检测链接有效性…', key: 'linkcheck', duration: 0 });
    }, 300);
    try {
        const res = await checkShareLink(r.t, r.url, r.share_code || '');
        if (res.state === 'bad') {
            if (tipShown)
                message.warning({ content: `分享已失效（${res.summary || '链接检测未通过'}）`, key: 'linkcheck', duration: 5 });
            else
                message.warning(`分享已失效（${res.summary || '链接检测未通过'}）`, 5);
            return false;
        }
        if (tipShown)
            message.success({ content: '链接有效', key: 'linkcheck', duration: 1 });
        return true;
    }
    catch {
        return true;
    }
    finally {
        window.clearTimeout(tipTimer);
        if (!tipShown)
            message.destroy('linkcheck');
        const next = new Set(checkingRows.value);
        next.delete(key);
        checkingRows.value = next;
    }
}
function quickDisabled(r: SearchResultItem) {
    return !r.ok || !hasDD(r.t);
}
function quickTitle(r: SearchResultItem) {
    return !r.ok ? T_NO_CRED : T_NO_DD;
}
const qsOpen = ref(false);
useBackGuard(qsOpen);
const qsType = ref<DriveType | null>(null);
const qsName = ref('');
const qsUrl = ref('');
const qsCode = ref('');
const tmOpen = ref(false);
useBackGuard(tmOpen);
const tmTarget = ref<TransferTarget | null>(null);
async function openQuick(r: SearchResultItem) {
    if (!r.ok || r.t === 'magnet' || !hasDD(r.t))
        return;
    if (!(await aliveOrBlock(r)))
        return;
    tmOpen.value = false;
    qsType.value = r.t;
    qsName.value = defaultName(r);
    qsUrl.value = r.url || '';
    qsCode.value = r.share_code || '';
    qsOpen.value = true;
}
async function openTransfer(r: SearchResultItem) {
    if (!r.ok || r.t === 'magnet')
        return;
    if (!(await aliveOrBlock(r)))
        return;
    qsOpen.value = false;
    tmTarget.value = { type: r.t, name: defaultName(r), size: r.s, url: r.url || '', share_code: r.share_code || '' };
    tmOpen.value = true;
}
const YEAR_RE = /(19|20)\d{2}/;
function defaultName(r: SearchResultItem): string {
    const base = kw.value.trim();
    if (!base)
        return r.n;
    if (YEAR_RE.test(base))
        return base;
    const m = YEAR_RE.exec(r.n || '');
    return m ? `${base} (${m[0]})` : base;
}
function onJump(r: SearchResultItem) {
    if (!r.url) {
        message.warning('该结果没有分享链接');
        return;
    }
    window.open(r.url, '_blank', 'noopener');
}
const sfOpen = ref(false);
const sfRec = ref<SearchResultItem | null>(null);
function onViewFiles(r: SearchResultItem) {
    if (!r.url) {
        message.warning('该结果没有分享链接');
        return;
    }
    sfRec.value = r;
    sfOpen.value = true;
}
async function onCopy(r: SearchResultItem) {
    if (!r.url) {
        message.warning('该结果没有分享链接');
        return;
    }
    try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(r.url);
        }
        else {
            const ta = document.createElement('textarea');
            ta.value = r.url;
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
        }
        message.success('已复制分享链接');
    }
    catch {
        message.error('复制失败');
    }
}
onUnmounted(() => {
    window.clearInterval(scanTimer);
    cancelAnimationFrame(rollRaf);
    saveSearchCache({ kw: kw.value, results: results.value, active: active.value, elapsed: elapsed.value });
});
</script>

<template>
  <div>
    

    
    <div class="searchwrap">
      <a-input ref="kwRef" v-model:value="kw" class="kw-input" placeholder="输入片名 / 关键词，空格分隔" @press-enter="doSearch" />
      <a-button type="primary" class="btn-search" :loading="busy" @click="doSearch">搜 索</a-button>
    </div>

    
    <div class="card st-flush st-mb">
      <div class="filterbar">
        
        <div class="ch-strip" title="点击管理搜索频道" @click="openChannelCfg">
          <span class="muted">搜索源频道</span>
          <template v-if="selectedChannels.length === 0">
            <span class="ch-chip ch-all">全部频道 · {{ channels.length }}</span>
          </template>
          <template v-else>
            
            <template v-if="!isMobile">
              <span v-for="c in selectedChannels.slice(0, 3)" :key="c" class="ch-chip">{{ c }}</span>
              <span v-if="selectedChannels.length > 3" class="ch-chip ch-more">+{{ selectedChannels.length - 3 }}</span>
            </template>
            <span v-else class="ch-chip ch-more">+{{ selectedChannels.length }}</span>
          </template>
          <span class="ch-edit">✎ 管理</span>
        </div>
        <span class="st-flex1"></span>
        
        <span v-if="isMobile && elapsed" class="ch-elapsed">耗时 {{ elapsed }}</span>
        <span class="engine-pill" :class="{ ok: engineOk === true, bad: engineOk === false || pansouMissing }">
          <span class="ep-dot"></span>
          <template v-if="pansouMissing">检索引擎未配置</template>
          <template v-else-if="engineOk === null">引擎状态检测中…</template>
          <template v-else-if="engineOk">检索引擎在线</template>
          <template v-else>检索引擎离线</template>
        </span>
      </div>

      
      <div class="pktabs">
        <div
          v-for="t in tabs"
          :key="t.key"
          class="pktab"
          :class="{ on: active === t.key, dim: t.key !== 'all' && !t.count }"
          @click="setTab(t.key)"
        >
          <i class="pkdot" :style="{ background: t.color }"></i>
          {{ t.name }}<span class="pknum">{{ t.count }}</span>
        </div>
        <span class="pk-tab-scan">
          <template v-if="busy"><span class="pkspin"></span>正在检索… 已扫 {{ scanCount }} 个源</template>
          <template v-else>
            <span class="pkdot-live"></span>已检索 {{ countsBy.all }} 条<template v-if="elapsed"> · 耗时 {{ elapsed }}</template>
          </template>
        </span>
      </div>

      
      <div v-if="busy" class="pk-strip" aria-hidden="true"><i class="pk-strip-fill"></i></div>
    </div>

    
    <div v-if="statCards.length" :key="statEpoch" class="statline enter">
      <div
        v-for="it in statCards"
        :key="it.key"
        class="stat clickable"
        :class="{ sel: active === it.key && (isMobile || it.key !== 'all') }"
        :style="it.accent ? { '--pk-accent': it.accent } : undefined"
        @click="setTab(it.key as TabKey)"
      >
        <b>{{ statShown[it.key] ?? it.value }}</b>
        <span>{{ it.label }}</span>
      </div>
    </div>

    
    <div class="card st-flush st-res" :class="{ enter: rowEpoch > 0, 'is-empty': booted && !busy && !paged.length }">
      
      <div v-if="booted && !busy && !paged.length" class="pk-empty-state">
        <template v-if="pansouMissing">
          <div class="pk-es-ico is-warn">⚙</div>
          <div class="pk-es-title">还没配置 PanSou 搜索服务</div>
          <div class="pk-es-sub">PanKeeper 的搜索结果全部来自 PanSou，地址填好之前搜不出任何东西</div>
          <a-button type="primary" class="pk-es-btn" @click="goPansouCfg">去「系统设置 → 搜索源」配置</a-button>
        </template>
        <template v-else>
          <div class="st-es-orbit">
            <span class="st-es-pulse"></span>
            <span class="st-es-pulse is-delay"></span>
            <span class="st-es-sweep"></span>
            <div class="pk-es-ico st-es-core"><SearchOutlined /></div>
          </div>
          <div class="pk-es-title">输入片名，全网资源一站直达</div>
          <div class="st-es-samples">
            <span class="st-es-sample" v-for="w in sampleWords" :key="w" @click="searchSample(w)">{{ w }}</span>
          </div>
        </template>
      </div>
      
      <div v-if="busy" class="st-searching">
        <div class="ss-orbit">
          <i class="ss-glow g1"></i>
          <i class="ss-glow g2"></i>
          <div class="ss-ring r-dash"></div>
          <div class="ss-ring r-solid"><i class="ss-dot"></i></div>
          <div class="ss-ring r-arc"></div>
          <div class="ss-core">
            <svg viewBox="0 0 48 48" fill="none">
              <circle cx="20" cy="20" r="11" stroke="#fff" stroke-width="3.5" />
              <path d="M29 29 L39 39" stroke="#fff" stroke-width="4.5" stroke-linecap="round" />
            </svg>
          </div>
        </div>
        <div class="ss-text">正在全网检索资源<i></i><i></i><i></i></div>
        <div class="ss-sub">已扫 {{ scanCount }} 个频道 · 结果按网盘自动分类</div>
      </div>

      <template v-else-if="paged.length">
      <table v-if="!isMobile" class="st-table">
        <thead>
          <tr>
            <th style="width: 50%">资源名称</th>
            <th>来源</th>
            <th>大小</th>
            <th>分享时间</th>
            <th style="width: 210px">操作</th>
          </tr>
        </thead>

        <tbody :key="'rows' + rowEpoch">
          <tr v-for="(r, i) in paged" :key="r.t + '-' + r.n" :style="{ animationDelay: 0.03 * i + 's' }">
            <td>
              <div class="st-namecell">
                <span class="srcbar" :style="{ background: DRIVE_META[r.t].color }"></span>
                <span class="resname" :title="r.n">{{ r.n }}</span>
                <span v-if="r.hot" class="tag t-123 st-hot">极速</span>
              </div>
            </td>
            <td>
              <span class="st-srccell">
                <span class="tag" :class="DRIVE_META[r.t].tag">{{ DRIVE_META[r.t].full }}</span>
                <a-tooltip v-if="r.url && r.t !== 'magnet'" title="查看文件"><button class="pa-ico pa-ico-view" @click.stop="onViewFiles(r)"><FolderOpenOutlined /></button></a-tooltip>
                <a-tooltip v-if="r.url" :title="r.t === 'magnet' ? '复制磁力链接' : '复制链接'"><button class="pa-ico pa-ico-copy" @click.stop="onCopy(r)"><CopyOutlined /></button></a-tooltip>
              </span>
            </td>
            <td class="small muted">{{ r.s }}</td>
            <td class="small muted">{{ r.d }}</td>
            <td>
              
              <div class="rowbtns">
                <template v-if="r.t !== 'magnet'">
                  <button class="btn btn-quick" :disabled="quickDisabled(r) || isChecking(r)" :title="isChecking(r) ? '正在检测链接…' : quickTitle(r)" @click="openQuick(r)">快速转存</button>
                  <button class="btn btn-trans" :disabled="!r.ok || isChecking(r)" :title="isChecking(r) ? '正在检测链接…' : T_NO_CRED" @click="openTransfer(r)">转存</button>
                  <span class="rb-sep"></span>
                  <button class="btn btn-jump" @click="onJump(r)">跳转</button>
                </template>
                <button v-else class="btn btn-jump" @click="onCopy(r)">复制磁力</button>
              </div>
            </td>
          </tr>

        </tbody>
      </table>

      
      <div v-else class="st-cards" :key="'mrows' + rowEpoch">
        <div
          v-for="(r, i) in paged"
          :key="r.t + '-' + r.n"
          class="st-card-item"
          :style="{ animationDelay: 0.03 * i + 's' }"
        >
            <div class="st-card-name">
              <span class="srcbar" :style="{ background: DRIVE_META[r.t].color }"></span>
              <span class="st-card-title">{{ r.n }}</span>
              <span v-if="r.hot" class="tag t-123 st-hot">极速</span>
            </div>
            <div class="st-card-meta">
              <span class="tag" :class="DRIVE_META[r.t].tag">{{ DRIVE_META[r.t].full }}</span>
              <a-tooltip v-if="r.url && r.t !== 'magnet'" title="查看文件"><button class="pa-ico pa-ico-view" @click.stop="onViewFiles(r)"><FolderOpenOutlined /></button></a-tooltip>
              <a-tooltip v-if="r.url" :title="r.t === 'magnet' ? '复制磁力链接' : '复制链接'"><button class="pa-ico pa-ico-copy" @click.stop="onCopy(r)"><CopyOutlined /></button></a-tooltip>
              <span class="small muted">{{ r.s }}</span>
              <span class="small muted st-card-date">{{ r.d }}</span>
            </div>
            <div class="rowbtns st-card-ops">
              <template v-if="r.t !== 'magnet'">
                <button class="btn btn-quick" :disabled="quickDisabled(r) || isChecking(r)" :title="isChecking(r) ? '正在检测链接…' : quickTitle(r)" @click="openQuick(r)">快速转存</button>
                <button class="btn btn-trans" :disabled="!r.ok || isChecking(r)" :title="isChecking(r) ? '正在检测链接…' : T_NO_CRED" @click="openTransfer(r)">转存</button>
                <span class="rb-sep"></span>
                <button class="btn btn-jump" @click="onJump(r)">跳转</button>
              </template>
              <button v-else class="btn btn-jump" @click="onCopy(r)">复制磁力</button>
            </div>
          </div>
      </div>
      <PkPager v-if="paged.length" v-model:current="page" v-model:pageSize="size" :total="filtered.length" />
      </template>
    </div>



    
    <QuickTransferModal v-model:open="qsOpen" :type="qsType" :share-name="qsName" :share-url="qsUrl" :share-code="qsCode" />
    
    <ShareFilesModal
      v-model:open="sfOpen"
      :task-id="null"
      :task-name="sfRec?.n || ''"
      :fetcher="(refresh: boolean) => getSearchShareFiles(sfRec!.t, sfRec!.url!, sfRec!.share_code || '', refresh)"
    />
    
    <TransferModal v-model:open="tmOpen" :target="tmTarget" />
  </div>
  
  <a-modal v-model:open="chCfgOpen" title="搜索频道设置" :width="520" @ok="chSave">
    <div style="display: flex; gap: 8px; margin-bottom: 10px">
      <a-input v-model:value="chFilter" placeholder="按频道名过滤" allow-clear />
      <a-button @click="chSetAll(true)">全选</a-button>
      <a-button @click="chSetAll(false)">清空</a-button>
    </div>
    <div style="max-height: 320px; overflow: auto; border: 1px solid var(--split); border-radius: 8px; padding: 8px 12px">
      <a-checkbox
        v-for="c in chFiltered"
        :key="c.name"
        :checked="chDraft.includes(c.name)"
        style="display: flex; padding: 4px 0"
        @change="(e: any) => chToggle(c.name, e.target.checked)"
      >{{ c.name }}</a-checkbox>
      <div v-if="chFiltered.length === 0" class="small muted" style="text-align: center; padding: 16px 0">
        没有匹配的频道
      </div>
    </div>
    <p class="small" style="color: var(--text3); margin: 10px 0 0">
      不勾任何频道 = 每次搜索使用 PanSou 的全部频道（{{ channels.length }} 个）。白名单保存在后端设置里。
    </p>
  </a-modal>
</template>

<style scoped>
/* =====================================================================
 * 搜索结果区样式。
 * 模板里引用的 .st-* / .skel / .enter 等 class 此前在 pk.css 中并不存在，
 * 导致「骨架屏看不见、结果行瞬间闪出」，这里按项目视觉令牌补齐。
 * 注：pk.css 只保留跨页公共件；本页私有件按约定放本文件的 scoped 样式。
 * ===================================================================== */

/* 无内边距卡片：表格/卡片列表自己管留白，避免与卡内 padding 叠加错位 */
.st-flush { padding: 0; position: relative; overflow: hidden; }
.st-mb { margin-bottom: 16px; }
.st-flex1 { flex: 1; }
.st-res { overflow: hidden; }

/* ===== 加载骨架：横向扫光（与 ShareFilesModal 的 sfm-skel 同一套观感） ===== */
.skel {
  display: inline-block;
  height: 14px;
  border-radius: 6px;
  background: linear-gradient(90deg, var(--surface-2) 20%, var(--split) 45%, var(--surface-2) 70%);
  background-size: 220% 100%;
  animation: pkSkelSweep 1.35s cubic-bezier(0.4, 0, 0.6, 1) infinite;
}
@keyframes pkSkelSweep {
  0% { background-position: 120% 0; }
  100% { background-position: -80% 0; }
}
.skel-tr td { padding-top: 15px; padding-bottom: 15px; }

/* ===== 结果行：来源色条 + 名称省略 ===== */
.st-namecell { display: flex; align-items: center; gap: 8px; min-width: 0; }
.srcbar { flex: none; width: 3px; height: 15px; border-radius: 3px; }
.resname {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
}
.st-hot { flex: none; margin-right: 0; }

/* 来源格：网盘 tag + 「查看文件/复制链接」图标钮（同自动转存 .pa-ico 五色钮语义：
   查看=蓝 / 复制=紫；样式就近自带一份——scoped 不跨页） */
.st-srccell { display: inline-flex; align-items: center; gap: 2px; min-width: 0; }
.pa-ico {
  width: 26px;
  height: 26px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  line-height: 1;
  border: none;
  border-radius: 7px;
  background: transparent;
  cursor: pointer;
  color: var(--text2);
  transition: background 0.15s, color 0.15s;
  font-family: inherit;
}
.pa-ico:hover { background: var(--surface-3); }
.pa-ico.pa-ico-view { color: #1677ff; }
.pa-ico.pa-ico-view:hover { background: #f0f8ff; }
.pa-ico.pa-ico-copy { color: #722ed1; }
.pa-ico.pa-ico-copy:hover { background: #f9f0ff; }
html[data-theme='dark'] .pa-ico.pa-ico-view { color: #69b1ff; }
html[data-theme='dark'] .pa-ico.pa-ico-view:hover { background: #111a2c; }
html[data-theme='dark'] .pa-ico.pa-ico-copy { color: #b37feb; }
html[data-theme='dark'] .pa-ico.pa-ico-copy:hover { background: #1a1425; }

/* fixed 布局让「资源名称 50%」真正生效：超长名称在格子内省略（hover 有 title 全文），按钮列不再被挤没 */
table.st-table { table-layout: fixed; }

/* ===== 检索中动画：能量环 + 卫星点 + 核心放大镜（纯 CSS/SVG） ===== */
.st-searching {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 360px;
  padding: 36px 0;
}
.ss-orbit {
  position: relative;
  width: 180px;
  height: 180px;
}
/* 背景辉光斑：给整个环打氛围光 */
.ss-glow {
  position: absolute;
  width: 88px;
  height: 88px;
  border-radius: 50%;
  filter: blur(34px);
  opacity: 0.32;
}
.ss-glow.g1 {
  top: -20px;
  left: -10px;
  background: var(--primary);
  animation: ssDrift 5s ease-in-out infinite alternate;
}
.ss-glow.g2 {
  bottom: -24px;
  right: -12px;
  background: #7c5cfc;
  animation: ssDrift 5s ease-in-out infinite alternate-reverse;
}
@keyframes ssDrift {
  from { transform: translate(0, 0) scale(1); }
  to { transform: translate(18px, 12px) scale(1.25); }
}
/* 三层环：虚线慢转 / 实线反向带卫星 / 弧线快转 */
.ss-ring {
  position: absolute;
  border-radius: 50%;
}
.ss-ring.r-dash {
  inset: 0;
  border: 1.5px dashed color-mix(in srgb, var(--primary) 45%, transparent);
  animation: ssSpin 9s linear infinite;
}
.ss-ring.r-solid {
  inset: 26px;
  border: 1.5px solid color-mix(in srgb, #7c5cfc 45%, transparent);
  animation: ssSpin 5s linear infinite reverse;
}
.ss-dot {
  position: absolute;
  top: -5px;
  left: 50%;
  width: 9px;
  height: 9px;
  margin-left: -4.5px;
  border-radius: 50%;
  background: #7c5cfc;
  box-shadow: 0 0 10px 2px color-mix(in srgb, #7c5cfc 70%, transparent);
}
.ss-ring.r-arc {
  inset: 52px;
  border: 3.5px solid transparent;
  border-top-color: var(--primary);
  border-right-color: var(--primary);
  filter: drop-shadow(0 0 6px color-mix(in srgb, var(--primary) 60%, transparent));
  animation: ssSpin 1.1s linear infinite;
}
@keyframes ssSpin {
  to { transform: rotate(360deg); }
}
/* 核心：渐变圆 + 放大镜 + 呼吸光晕 */
.ss-core {
  position: absolute;
  inset: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--primary), #7c5cfc);
  animation: ssPulse 1.8s ease-out infinite;
}
.ss-core svg {
  width: 30px;
  height: 30px;
}
@keyframes ssPulse {
  0% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--primary) 45%, transparent); }
  70% { box-shadow: 0 0 0 22px transparent; }
  100% { box-shadow: 0 0 0 0 transparent; }
}
/* 文案：渐变流光 */
.ss-text {
  margin-top: 26px;
  font-size: 15px;
  font-weight: 600;
  display: flex;
  align-items: center;
  background: linear-gradient(90deg, var(--text) 30%, var(--primary), #7c5cfc, var(--text) 70%);
  background-size: 220% 100%;
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  animation: ssShine 2.6s linear infinite;
}
@keyframes ssShine {
  from { background-position: 120% 0; }
  to { background-position: -120% 0; }
}
.ss-text i {
  width: 4px;
  height: 4px;
  margin-left: 5px;
  border-radius: 50%;
  background: var(--text3);
  animation: ssBounce 1.2s ease-in-out infinite;
}
.ss-text i:nth-child(2) { animation-delay: 0.15s; }
.ss-text i:nth-child(3) { animation-delay: 0.3s; }
@keyframes ssBounce {
  0%, 100% { transform: translateY(0); opacity: 0.4; }
  50% { transform: translateY(-4px); opacity: 1; }
}
.ss-sub {
  margin-top: 8px;
  font-size: 12.5px;
  color: var(--text3);
}

/* ===== 入场过渡：卡片整体上浮淡入 + 结果行逐行错峰（配合模板的 animation-delay） ===== */
.enter { animation: pkEnter 0.3s cubic-bezier(0.22, 0.61, 0.36, 1) both; }
@keyframes pkEnter {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: none; }
}
.st-res tbody tr { animation: pkRowIn 0.36s cubic-bezier(0.22, 0.61, 0.36, 1) both; }
@keyframes pkRowIn {
  from { opacity: 0; transform: translateY(7px); }
  to { opacity: 1; transform: none; }
}
/* 骨架行不参与入场动画，否则会和扫光叠在一起显脏 */
.st-res .skel-tr { animation: none; }

/* ===== 手机端卡片列表（<768px 用它替代表格） ===== */
.st-cards { padding: 10px 12px; }
.st-card-item {
  padding: 12px 14px;
  border: 1px solid var(--split);
  border-radius: 10px;
  animation: pkRowIn 0.36s cubic-bezier(0.22, 0.61, 0.36, 1) both;
  transition: background 0.15s;
}
.st-card-item + .st-card-item { margin-top: 10px; }
.st-card-item:hover { background: var(--surface-3); }
.st-card-name { display: flex; align-items: flex-start; gap: 8px; margin-bottom: 8px; }
/* 文件名放开换行看全（用户要求：截断了根本不知道文件是啥）；色条随行首对齐 */
.st-card-title {
  min-width: 0;
  font-weight: 500;
  line-height: 1.5;
  word-break: break-all;
}
.st-card-meta { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.st-card-date { margin-left: auto; }
.st-card-ops { justify-content: flex-end; }

/* ===== 空态雷达扫描图标：中心放大镜 + 旋转扫描弧 + 脉冲涟漪（"全网检索"的动感隐喻） ===== */
.st-es-orbit { position: relative; width: 132px; height: 132px; margin-bottom: 16px; display: flex; align-items: center; justify-content: center; }
.pk-empty-state .pk-es-ico.st-es-core {
  width: 68px;
  height: 68px;
  border-radius: 20px;
  font-size: 30px;
  color: #fff;
  background: linear-gradient(135deg, #7c5cf6, #3b6ef6 55%, #1d3ad8);
  box-shadow: 0 10px 26px rgba(59, 110, 246, 0.4), 0 0 30px rgba(99, 120, 255, 0.25), inset 0 1px 0 rgba(255, 255, 255, 0.28);
  z-index: 2;
}
/* 扫描弧：conic 渐变亮尾 + 圆环 mask，绕中心匀速转 */
.st-es-sweep {
  position: absolute;
  inset: 0;
  border-radius: 50%;
  background: conic-gradient(from 0deg, rgba(99, 120, 255, 0), rgba(99, 120, 255, 0) 290deg, rgba(124, 92, 246, 0.55) 350deg, rgba(59, 110, 246, 0.9) 360deg);
  -webkit-mask: radial-gradient(closest-side, transparent calc(100% - 3px), #000 calc(100% - 2px));
  mask: radial-gradient(closest-side, transparent calc(100% - 3px), #000 calc(100% - 2px));
  animation: stEsSweep 3.2s linear infinite;
}
@keyframes stEsSweep { to { transform: rotate(360deg); } }
/* 脉冲涟漪：两道错相扩散，扫描"发出去了"的观感 */
.st-es-pulse {
  position: absolute;
  inset: 10px;
  border-radius: 50%;
  border: 1px solid rgba(99, 120, 255, 0.5);
  animation: stEsPulse 3.2s ease-out infinite;
}
.st-es-pulse.is-delay { animation-delay: 1.6s; }
@keyframes stEsPulse {
  0% { transform: scale(0.82); opacity: 0.7; }
  100% { transform: scale(1.18); opacity: 0; }
}
@media (prefers-reduced-motion: reduce) {
  .st-es-sweep, .st-es-pulse { animation: none; }
  .st-es-pulse.is-delay { display: none; }
}
/* 示例词 chips：空态从纯装饰变成可点入口 */
.st-es-samples { display: flex; flex-wrap: wrap; justify-content: center; gap: 8px; margin-top: 18px; }
.st-es-sample {
  padding: 5px 14px;
  border-radius: 999px;
  border: 1px solid var(--split);
  background: var(--surface-2);
  font-size: 12.5px;
  color: var(--text2);
  cursor: pointer;
  transition: color 0.15s, border-color 0.15s, background 0.15s, transform 0.15s;
}
.st-es-sample:hover {
  color: var(--primary);
  border-color: var(--primary);
  background: rgba(22, 119, 255, 0.06);
  transform: translateY(-1px);
}

/* ===== 未配置 PanSou 时的空态引导：图标转警示色 + 去配置按钮 ===== */
.pk-empty-state .pk-es-ico.is-warn {
  color: var(--warning);
  background: linear-gradient(135deg, rgba(250, 173, 20, 0.14), rgba(250, 173, 20, 0.06));
  box-shadow: inset 0 0 0 1px rgba(250, 173, 20, 0.28);
}
.pk-es-btn {
  margin-top: 16px;
}
/* 空态副文案限宽，避免长句在宽屏下拉成一条 */
.pk-empty-state .pk-es-sub {
  max-width: 420px;
  text-align: center;
  line-height: 1.7;
}

/* ===== 统计卡（搜索页侧的补充态；.stat 基础样式在 pk.css） ===== */
/* 手机（<768px）：网盘 tab 那排隐藏——筛选条只留频道管理 + 引擎状态；
   切网盘走下面的统计卡（可点，当前 tab 有描边高亮），别在 393px 宽里塞 8 个 tab */
@media (max-width: 767px) {
  .pktabs {
    display: none;
  }
  /* 频道行两行式：上行「搜索源频道 +N 管理 … 耗时」，下行引擎状态胶囊独占一行 */
  .st-flex1 {
    display: none;
  }
  /* 耗时贴频道行右缘：频道摘要条不再 flex:1 撑满整行 */
  .ch-strip {
    flex: 0 1 auto;
  }
  .ch-elapsed {
    margin-left: auto;
    font-size: 12.5px;
    color: var(--text3);
    white-space: nowrap;
  }
  .engine-pill {
    flex-basis: 100%;
    margin-left: 10px; /* 与 ch-strip 内文字左缘对齐 */
    justify-self: start;
  }
}

/* 系统开启「减弱动态效果」时全部关闭 */
@media (prefers-reduced-motion: reduce) {
  .skel, .enter, .st-res tbody tr, .st-card-item { animation: none; }
}
</style>
