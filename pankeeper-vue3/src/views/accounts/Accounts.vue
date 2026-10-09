<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useBackGuard } from '@/composables/useBackGuard';
import { message } from 'ant-design-vue';
import LazyDirTree from '@/components/LazyDirTree.vue';
import { accountStore, ACCOUNT_STATUS_VIEW, type AccountRow } from '@/api/mock/accounts';
import { MAIN_ORDER, DRIVE_META } from '@/api/mock/meta';
import { addAccount, checkAccount, deleteAccount, getRootDirs, getSummary, listAccounts, saveCredential, setAlias, setDefaultAccount, setDriveNotify, setRootDir, type AccountSummary } from '@/api/modules/accounts';
import { getFilesList } from '@/api/modules/files';
import { warmTrees, warmStatus } from '@/api/modules/cache';
import type { MainDriveType } from '@/types/model';
const rows = computed<AccountRow[]>(() => {
    const rank: Record<string, number> = {};
    MAIN_ORDER.forEach((t, i) => (rank[t] = i));
    return [...accountStore.accounts].sort((a, b) => (rank[a.type] ?? 99) - (rank[b.type] ?? 99) || a.id - b.id);
});
function view(status: string) {
    return ACCOUNT_STATUS_VIEW[status];
}
function sameTypeCount(type: string): number {
    return accountStore.accounts.filter((a) => a.type === type).length;
}
const aliasOpen = ref(false);
const aliasAcc = ref<AccountRow | null>(null);
const aliasText = ref('');
const aliasSaving = ref(false);
function openAlias(a: AccountRow) {
    aliasAcc.value = a;
    aliasText.value = a.alias || '';
    aliasOpen.value = true;
}
async function onAliasSave() {
    if (!aliasAcc.value)
        return;
    aliasSaving.value = true;
    try {
        await setAlias(aliasAcc.value.id, aliasText.value.trim());
        aliasOpen.value = false;
        message.success(aliasText.value.trim() ? '别名已保存' : '别名已清除');
    }
    catch (e: unknown) {
        const detail = (e as {
            response?: {
                data?: {
                    detail?: string;
                };
            };
        })?.response?.data?.detail;
        message.error(detail || '保存失败');
    }
    finally {
        aliasSaving.value = false;
    }
}
async function onSetDefault(a: AccountRow) {
    try {
        await setDefaultAccount(a.id);
        message.success(a.is_default ? `已取消「${a.alias || a.nickname}」的默认标记（回落该网盘第一个账号）` : `已把「${a.alias || a.nickname}」设为默认账号`);
    }
    catch (e: unknown) {
        const detail = (e as {
            response?: {
                data?: {
                    detail?: string;
                };
            };
        })?.response?.data?.detail;
        message.error(detail || '设置失败');
    }
}
const notifySaving = ref<number | null>(null);
async function onToggleNotify(a: AccountRow, v: boolean) {
    if (notifySaving.value)
        return;
    notifySaving.value = a.id;
    try {
        await setDriveNotify(a.id, v);
        a.notify = v;
        message.success(`${a.alias || DRIVE_META[a.type].full}：${v ? '已开启' : '已关闭'}失效通知`);
    }
    catch (e: unknown) {
        const detail = (e as {
            response?: {
                data?: {
                    detail?: string;
                };
            };
        })?.response?.data?.detail;
        message.error(detail || '保存失败');
    }
    finally {
        notifySaving.value = null;
    }
}
const addOpen = ref(false);
useBackGuard(addOpen);
const addSaving = ref(false);
const addType = ref<MainDriveType>('baidu');
const addAlias = ref('');
const addCookies = ref('');
const ADD_PLATFORMS: {
    value: MainDriveType;
    label: string;
}[] = [
    { value: 'baidu', label: '百度网盘' },
    { value: 'quark', label: '夸克网盘' },
    { value: '115', label: '115 网盘' },
];
async function onAddSave() {
    if (!addCookies.value.trim()) {
        message.warning('请先粘贴 Cookie');
        return;
    }
    const type = addType.value;
    const cookies = addCookies.value.trim();
    const alias = addAlias.value.trim();
    addSaving.value = true;
    const tempId = -Date.now();
    const meta = DRIVE_META[type];
    accountStore.accounts.push({
        id: tempId, type, alias, nickname: '', short: meta.name, color: meta.color,
        cred_kind: 'Cookie', note: '', status: 'unset', last_check: '从未配置', notify: false, summary: null,
    });
    verifyingId.value = tempId;
    addOpen.value = false;
    try {
        const { nickname } = await addAccount(type, cookies, alias);
        message.success(`账号已添加并验证通过（${nickname}）`);
        addCookies.value = '';
        addAlias.value = '';
        const mine = accountStore.accounts.filter((x) => x.type === type);
        const fresh = mine.length ? mine[mine.length - 1] : null;
        if (fresh) {
            verifyingId.value = fresh.id;
            await loadSummary(fresh.id);
        }
        verifyingId.value = null;
        await warmAll(type, fresh?.id ?? null);
    }
    catch (e: unknown) {
        const detail = (e as {
            response?: {
                data?: {
                    detail?: string;
                };
            };
        })?.response?.data?.detail;
        message.error(detail || '添加失败');
        const pos = accountStore.accounts.findIndex((x) => x.id === tempId);
        if (pos >= 0)
            accountStore.accounts.splice(pos, 1);
        await listAccounts().catch(() => { });
    }
    finally {
        addSaving.value = false;
        verifyingId.value = null;
    }
}
onMounted(async () => {
    prefillSummaries();
    await listAccounts().catch(() => { });
    prefillSummaries();
    for (const a of accountStore.accounts) {
        if (a.status === 'connected')
            loadSummary(a.id);
    }
    rootDirs.value = await getRootDirs().catch(() => ({}));
});
const bdOpen = ref(false);
useBackGuard(bdOpen);
const bdType = ref<MainDriveType>('quark');
const bdPath = ref('');
const bdFid = ref('');
const bdTree = ref<InstanceType<typeof LazyDirTree> | null>(null);
const bdSaving = ref(false);
const bdTitle = computed(() => `默认根目录 · ${DRIVE_META[bdType.value]?.full || bdType.value}`);
const rootDirs = ref<Record<string, string>>({});
function rootDirOf(type: MainDriveType): string {
    return rootDirs.value[type] || '';
}
function openBaseDir(a: AccountRow) {
    bdType.value = a.type;
    bdPath.value = rootDirOf(a.type);
    bdFid.value = '';
    bdOpen.value = true;
}
function onTreePick(path: string, fid: string) {
    bdPath.value = path;
    bdFid.value = fid;
}
const bdRefreshing = ref(false);
async function onRefreshTree() {
    bdRefreshing.value = true;
    try {
        await bdTree.value?.reload();
    }
    finally {
        bdRefreshing.value = false;
    }
}
async function warmDirCache(type: MainDriveType, fid: string, path: string) {
    await getFilesList(type, fid || '0', fid ? '' : path);
}
const warmState = ref<Partial<Record<MainDriveType, string>>>({});
function warmOf(type: MainDriveType): string {
    return warmState.value[type] || '';
}
async function warmAll(type: MainDriveType, accId?: number | null) {
    try {
        await warmTrees(type, accId);
        for (let i = 0; i < 600; i++) {
            const st = await warmStatus(type, accId);
            if (st.status === 'done') {
                warmState.value[type] = `预热完成 · ${st.done} 个文件夹`;
                message.success(st.message ? `预热完成（${st.done} 个文件夹，${st.message}）` : `预热完成（${st.done} 个文件夹）`, 4);
                setTimeout(() => {
                    if (warmState.value[type]?.startsWith('预热完成'))
                        warmState.value[type] = '';
                }, 6000);
                return;
            }
            if (st.status === 'error') {
                warmState.value[type] = '';
                message.error(`预热失败：${st.message || '未知错误'}`, 5);
                return;
            }
            warmState.value[type] = `正在预热 ${st.done} 个文件夹…`;
            await new Promise((r) => setTimeout(r, 3000));
        }
        warmState.value[type] = '';
        message.warning('预热还在后台跑，已转入静默；可稍后在「网盘日志 → 缓存」查看', 5);
    }
    catch (e: unknown) {
        warmState.value[type] = '';
        const detail = (e as {
            response?: {
                data?: {
                    detail?: string;
                };
            };
        })?.response?.data?.detail;
        message.error(`预热失败：${detail || '请求失败'}`, 5);
    }
}
async function onConfirmBaseDir() {
    if (!bdPath.value) {
        message.warning('请先在树里选择一个目录');
        return;
    }
    bdSaving.value = true;
    const type = bdType.value;
    const fid = bdFid.value;
    const path = bdPath.value;
    try {
        await setRootDir(type, path);
        rootDirs.value = await getRootDirs();
    }
    catch (e: unknown) {
        const detail = (e as {
            response?: {
                data?: {
                    detail?: string;
                };
            };
        })?.response?.data?.detail;
        message.error(detail || '保存失败');
        bdSaving.value = false;
        return;
    }
    bdOpen.value = false;
    bdSaving.value = false;
    const hide = message.loading('正在缓存文件夹信息…', 0);
    try {
        await warmDirCache(type, fid, path);
        message.success('缓存成功');
    }
    catch (e: unknown) {
        const detail = (e as {
            response?: {
                data?: {
                    detail?: string;
                };
            };
        })?.response?.data?.detail;
        message.error(`缓存失败：${detail || '目录拉取出错'}`, 5);
    }
    finally {
        hide();
    }
}
const checking = ref<number | null>(null);
const verifyingId = ref<number | null>(null);
async function onCheck(a: AccountRow) {
    checking.value = a.id;
    try {
        const res = await checkAccount(a.id);
        const row = accountStore.accounts.find((x) => x.id === a.id);
        if (row)
            row.status = res.status;
        if (res.kind === 'success') {
            message.success(res.message);
            await loadSummary(a.id);
        }
        else if (res.kind === 'warning')
            message.warning(res.message);
        else
            message.error(res.message);
    }
    finally {
        checking.value = null;
    }
}
const summaries = ref<Record<number, AccountSummary | null>>({});
function prefillSummaries() {
    for (const a of accountStore.accounts) {
        if (a.summary)
            summaries.value[a.id] = a.summary;
    }
}
async function loadSummary(accId: number) {
    try {
        const data = await getSummary(accId);
        summaries.value[accId] = data;
        const row = accountStore.accounts.find((x) => x.id === accId);
        if (row)
            row.summary = data;
    }
    catch {
        summaries.value[accId] = null;
    }
}
function summaryOf(accId: number): AccountSummary | null {
    return summaries.value[accId] || null;
}
function capOf(accId: number) {
    const cap = summaryOf(accId)?.capacity;
    if (!cap || !cap.total)
        return null;
    const pct = Math.min(100, Math.round((cap.used / cap.total) * 100));
    const unit: 'GB' | 'TB' = cap.total >= 1024 * 1024 ** 3 ? 'TB' : 'GB';
    const fmt = (v: number) => (unit === 'TB' ? (v / 1024 ** 4).toFixed(2) : (v / 1024 ** 3).toFixed(v > 10 * 1024 ** 3 ? 0 : 1));
    return { pct, used: fmt(cap.used), total: fmt(cap.total), unit };
}
function capClass(pct: number): string {
    return pct >= 95 ? 'full' : pct >= 80 ? 'warn' : '';
}
function gb(v: number, unit: 'GB' | 'TB' = 'GB'): string {
    return unit === 'TB' ? (v / 1024 ** 4).toFixed(2) : (v / 1024 ** 3).toFixed(v > 10 * 1024 ** 3 ? 0 : 1);
}
function capUnit(accId: number): 'GB' | 'TB' {
    const cap = summaryOf(accId)?.capacity;
    return cap && cap.total >= 1024 * 1024 ** 3 ? 'TB' : 'GB';
}
const credOpen = ref(false);
useBackGuard(credOpen);
const credAcc = ref<AccountRow | null>(null);
const credTitle = computed(() => `配置凭据 · ${credAcc.value ? credAcc.value.alias || DRIVE_META[credAcc.value.type]?.full || credAcc.value.type : ''}`);
const credCookies = ref('');
const credSaving = ref(false);
function onConfig(a: AccountRow) {
    credAcc.value = a;
    credCookies.value = '';
    credOpen.value = true;
}
async function onCredSave() {
    if (!credCookies.value.trim()) {
        message.warning('请先粘贴 Cookie');
        return;
    }
    if (!credAcc.value)
        return;
    const acc = credAcc.value;
    const cookies = credCookies.value.trim();
    credOpen.value = false;
    verifyingId.value = acc.id;
    try {
        const { nickname } = await saveCredential(acc.id, cookies);
        credCookies.value = '';
        message.success(`凭据已保存并验证通过（${nickname}）`);
        await loadSummary(acc.id);
        verifyingId.value = null;
        await warmAll(acc.type, acc.id);
    }
    catch (e: unknown) {
        const detail = (e as {
            response?: {
                data?: {
                    detail?: string;
                };
            };
        })?.response?.data?.detail;
        message.error(detail || '凭据验证失败');
    }
    finally {
        verifyingId.value = null;
    }
}
async function onClear(a: AccountRow) {
    await deleteAccount(a.id);
    message.success(`已删除「${a.alias || DRIVE_META[a.type].full}」账号卡片`);
}
</script>

<template>
  <div>
    
    <div style="display: flex; justify-content: flex-end; margin-bottom: 14px">
      <a-button type="primary" @click="addOpen = true">＋ 新增网盘</a-button>
    </div>
    <div class="accgrid">
      <div v-for="a in rows" :key="a.id" class="acc" :class="{ off: a.status === 'unset' }" :style="{ '--acc': a.color }">
        <div class="acchead">
          <div class="accname">
            <span class="chip" :style="{ background: a.color }">{{ a.short }}</span>
            {{ a.alias || a.nickname || DRIVE_META[a.type].full }}
            <span class="tag acc-tag" :class="view(a.status).cls">
              <span class="dot" :class="view(a.status).dot"></span>{{ view(a.status).label }}
            </span>
            
            <span
              v-if="sameTypeCount(a.type) > 1"
              class="tag acc-tag def-tag"
              :class="{ on: a.is_default }"
              title="默认账号：转存与定时任务未指定账号时用它；点击切换"
              @click="onSetDefault(a)"
            >{{ a.is_default ? '★ 默认' : '☆ 设默认' }}</span>
          </div>
          
          <button class="acc-alias-btn" title="修改别名" @click="openAlias(a)">别名</button>
        </div>
        <div class="kv">
          <span>会员</span>
          <b v-if="a.status === 'connected' && summaryOf(a.id)?.vip && summaryOf(a.id)!.vip!.name !== '普通用户'"
             :title="summaryOf(a.id)!.vip!.expires ? `会员到期：${summaryOf(a.id)!.vip!.expires}` : ''">
            <span class="vip-tag">✦ {{ summaryOf(a.id)!.vip!.name }}<template v-if="summaryOf(a.id)!.vip!.expires"> · {{ summaryOf(a.id)!.vip!.expires }}</template></span>
          </b>
          <b v-else-if="a.status === 'connected' && summaryOf(a.id)?.vip?.name === '普通用户'"><span class="normal-tag">普通用户</span></b>
          <b v-else style="color: var(--text3)">—</b>
        </div>
        <div class="kv"><span>上次检测</span><b>{{ a.last_check }}</b></div>
        <div class="kv">
          <span>默认根目录</span>
          <b style="display: inline-flex; align-items: center; gap: 6px">
            {{ rootDirOf(a.type) || '—' }}
            <a-tooltip :title="a.status !== 'connected' ? '请先配置凭据并连通' : '从网盘目录中选择默认保存位置'">
              <a-button
                type="link"
                size="small"
                style="padding: 0 2px; height: auto"
                :disabled="a.status !== 'connected'"
                @click="openBaseDir(a)"
              >{{ rootDirOf(a.type) ? '修改' : '配置' }}</a-button>
            </a-tooltip>
          </b>
        </div>
        <div class="kv">
          <span>失效通知</span>
          <b>
            <a-switch
              :checked="a.notify"
              :loading="notifySaving === a.id"
              size="small"
              @change="(v: any) => onToggleNotify(a, !!v)"
            />
          </b>
        </div>
        
        <div v-if="a.status !== 'connected'" class="capblock">
          <div class="capbar"><i style="width: 0%"></i></div>
          <div class="capmeta">
            <span style="color: var(--text3)">配置凭据后显示容量</span>
            <span>—</span>
          </div>
        </div>
        <div v-else class="capblock">
          <template v-if="capOf(a.id)">
            <div class="capbar"><i :class="capClass(capOf(a.id)!.pct)" :style="{ width: capOf(a.id)!.pct + '%' }"></i></div>
            <div class="capmeta">
              <span>已用 {{ capOf(a.id)!.used }} {{ capOf(a.id)!.unit }} / 共 {{ capOf(a.id)!.total }} {{ capOf(a.id)!.unit }}</span>
              <span>{{ capOf(a.id)!.pct }}%</span>
            </div>
          </template>
          <div v-else-if="summaries[a.id] !== undefined" class="small" style="color: var(--text3)">
            {{ summaries[a.id] === null ? '容量信息获取失败' : '暂无容量信息' }}
          </div>
        </div>
        <div class="accbtns">
          <template v-if="verifyingId === a.id">
            <span class="acc-checking"><a-spin size="small" />正在验证 Cookie…</span>
          </template>
          <template v-else-if="warmOf(a.type)">
            <span class="acc-checking"><a-spin size="small" />{{ warmOf(a.type) }}</span>
          </template>
          <template v-else>
            <a-button type="primary" size="small" @click="onConfig(a)">配置凭据</a-button>
            <a-button size="small" :loading="checking === a.id" @click="onCheck(a)">检测连通</a-button>
            <a-popconfirm
              v-if="a.status !== 'unset'"
              title="确定删除该账号？凭据密文一并删除，需重新添加绑定。"
              ok-text="删除"
              cancel-text="取消"
              @confirm="onClear(a)"
            >
              <a-button size="small" danger>删除</a-button>
            </a-popconfirm>
          </template>
        </div>
      </div>
    </div>



    
    <a-modal v-model:open="addOpen" :width="480" title="新增账号" :footer="null">
      <div class="acc-add">
        <div class="acc-add-label">选择平台</div>
        <div class="acc-add-plates">
          <button
            v-for="p in ADD_PLATFORMS"
            :key="p.value"
            type="button"
            class="acc-add-plate"
            :class="{ on: addType === p.value }"
            :style="{ '--pc': DRIVE_META[p.value].color }"
            @click="addType = p.value"
          >
            <i class="acc-add-ic">{{ DRIVE_META[p.value].name }}</i>
            <span>{{ p.label }}</span>
          </button>
        </div>
        <div class="acc-add-label">账号别名<span class="acc-add-opt">可选，方便区分同平台多个账号</span></div>
        <a-input v-model:value="addAlias" placeholder="如：百度-大号" allow-clear />
        <div class="acc-add-label">Cookie</div>
        <a-textarea
          v-model:value="addCookies"
          :rows="5"
          placeholder="粘贴整串 Cookie，例如：UID=...; CID=...; SEID=...; __pus=...; __puus=..."
        />
        <div class="acc-add-hint">
          获取方式：登录网盘网页版 → 按 <b>F12</b> 打开开发者工具 → <b>网络</b> 标签 → 刷新页面
          → 点任一请求 → 在请求头里复制完整 Cookie。凭据加密存储，任何接口都不会回显明文。
        </div>
        <div class="acc-add-foot">
          <span>保存后会立即连接网盘验证，失败会提示原因</span>
          <div class="acc-add-btns">
            <a-button @click="addOpen = false">取消</a-button>
            <a-button type="primary" :loading="addSaving" @click="onAddSave">保存并验证</a-button>
          </div>
        </div>
      </div>
    </a-modal>

    
    <a-modal
      v-model:open="credOpen"
      :title="credTitle"
      :confirm-loading="credSaving"
      ok-text="保存并验证"
      @ok="onCredSave"
    >
      <p class="small" style="color: var(--text3); margin-bottom: 10px">
        到对应网盘网页版按 F12 → 网络 → 任一请求的请求头里复制整串 Cookie 粘贴到下面。
        凭据加密存储，任何接口都不会回填明文。
      </p>
      <a-textarea
        v-model:value="credCookies"
        :rows="5"
        placeholder="粘贴整串 Cookie，例如：UID=...; CID=...; SEID=...; __pus=...; __puus=..."
      />
    </a-modal>

    
    <a-modal
      :open="bdOpen"
      :width="480"
      ok-text="保存到此处"
      :confirm-loading="bdSaving"
      :destroy-on-close="true"
      @ok="onConfirmBaseDir"
      @update:open="(v: boolean) => (bdOpen = v)"
    >
      <template #title>
        <div class="bd-titlebar">
          <span>{{ bdTitle }}</span>
          
          <a-button size="small" :loading="bdRefreshing" @click="onRefreshTree">刷新</a-button>
        </div>
      </template>
      <p class="small" style="color: var(--text3); margin-bottom: 10px">
        从网盘真根选择一个目录作为本网盘的默认根目录；保存后所有目录树弹窗都从这里开始浏览。
      </p>
      
      <LazyDirTree ref="bdTree" :key="bdType" :type="bdType" :initial-path="rootDirOf(bdType)" @select="onTreePick" />
      <p class="bd-picked">已选目录：<b>{{ bdPath || '/' }}</b></p>
    </a-modal>
    
    <a-modal
      v-model:open="aliasOpen"
      :width="380"
      :title="aliasAcc ? `修改别名 · ${aliasAcc.alias || DRIVE_META[aliasAcc.type].full}` : '修改别名'"
      :confirm-loading="aliasSaving"
      ok-text="保存"
      @ok="onAliasSave"
    >
      <a-input v-model:value="aliasText" allow-clear />
      <p class="small" style="color: var(--text3); margin: 10px 0 0">
        别名用于区分同网盘的多个账号；可留空。
      </p>
    </a-modal>

  </div>
</template>

<style scoped>
/* 卡片网格/卡片本体的视觉在 pk.css 共享段（.accgrid/.acc/.acchead/...），这里只补页面私有微调 */
/* 品牌色顶条：与首页网盘卡同款分层（百度蓝/夸克青/115 紫） */
/* 目录选择弹窗底部的「已选目录」回显 */
.bd-picked {
  margin: 12px 0 0;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--surface-2);
  font-size: 12.5px;
  color: var(--text3);
}
.bd-picked b { color: var(--primary); font-weight: 500; }

/* 目录弹窗标题栏：标题 + 刷新按钮（右侧留出关闭 X 的位置，别挤在一起） */
.bd-titlebar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  /* antd 关闭 X 占位约 22px + 间距 */
  padding-right: 34px;
}

.acc {
  position: relative;
  overflow: hidden;
  /* 首页网盘卡同款：hover 轻浮起 + 边框和阴影染上品牌色（颜色经 --acc 传入） */
  transition: transform 0.18s, box-shadow 0.18s, border-color 0.18s;
}
.acc:hover {
  border-color: var(--acc);
  box-shadow:
    var(--shadow-lg),
    0 0 22px color-mix(in srgb, var(--acc) 16%, transparent);
}
.acc::before {
  content: "";
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  /* 首页网盘卡同款品牌色光晕条：渐变收边 + 品牌色微发光（颜色经 --acc 传入） */
  background: linear-gradient(90deg, transparent, var(--acc) 35%, var(--acc) 65%, transparent);
  box-shadow: 0 0 10px var(--acc);
}
/* 首页同款扫描线：品牌色自上而下缓缓掠过（console 质感） */
.acc::after {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  height: 40px;
  top: -46px;
  background: linear-gradient(to bottom, transparent, color-mix(in srgb, var(--acc) 7%, transparent), transparent);
  animation: accScan 5.5s linear infinite;
  pointer-events: none;
}
@keyframes accScan {
  0% { top: -46px; }
  70%, 100% { top: 105%; }
}
/* 未配置的卡：整体半透明 + 扫描线停住（与首页 .is-off 同语义） */
.acc.off { opacity: 0.72; }
.acc.off::after { animation-play-state: paused; opacity: 0; }
@media (prefers-reduced-motion: reduce) {
  .acc::after { animation: none; }
}
/* 状态 tag 挤在卡头右侧，去掉共享 .tag 的右边距避免顶着卡片边 */
.acchead .tag {
  margin-right: 0;
}
/* 状态标签已挪进 .accname（紧跟网盘名）：收回从标题继承来的字重，保持标签原观感 */
.acchead .accname .acc-tag {
  font-size: 12px;
  font-weight: 500;
}
/* 默认账号标签：可点击切换；on 态品牌色实底 */
.accname .def-tag {
  cursor: pointer;
  color: var(--text3);
  border: 1px dashed var(--border);
  background: transparent;
  user-select: none;
}
.accname .def-tag.on {
  color: #fff;
  background: var(--acc);
  border: 1px solid var(--acc);
}

/* 别名按钮：卡片头右侧低调小胶囊，hover 才亮起品牌色 */
.acc-alias-btn {
  height: 24px;
  padding: 0 10px;
  font-size: 12px;
  color: var(--text3);
  background: transparent;
  border: 1px solid var(--border);
  border-radius: 999px;
  cursor: pointer;
  transition: all 0.15s;
}
.acc-alias-btn:hover {
  color: var(--acc);
  border-color: var(--acc);
  background: color-mix(in srgb, var(--acc) 10%, transparent);
}

/* ===== 新增账号弹窗（acc-add- 前缀页面私有） ===== */
.acc-add-label {
  margin: 14px 0 6px;
  font-size: 13px;
  font-weight: 500;
  color: var(--text2);
}
.acc-add-label:first-child {
  margin-top: 2px;
}
.acc-add-opt {
  margin-left: 8px;
  font-size: 12px;
  font-weight: 400;
  color: var(--text3);
}
/* 平台选块：品牌色图标方块 + 名称，选中染品牌色边框和浅底（颜色经 --pc 传入） */
.acc-add-plates {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
}
.acc-add-plate {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface-2);
  font-size: 13px;
  color: var(--text2);
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s, box-shadow 0.15s;
}
.acc-add-plate:hover {
  border-color: color-mix(in srgb, var(--pc) 55%, var(--border));
}
.acc-add-plate.on {
  border-color: var(--pc);
  background: color-mix(in srgb, var(--pc) 8%, var(--surface-2));
  box-shadow: 0 0 0 1px var(--pc);
  color: var(--text1, var(--text2));
  font-weight: 500;
}
/* 图标方块：与首页/卡片同款圆角短名块 */
.acc-add-ic {
  flex: none;
  width: 30px;
  height: 30px;
  display: grid;
  place-items: center;
  border-radius: 8px;
  background: var(--pc);
  color: #fff;
  font-size: 12px;
  font-style: normal;
  font-weight: 600;
  letter-spacing: 0.5px;
}
/* 获取方式提示块 */
.acc-add-hint {
  margin-top: 8px;
  padding: 8px 11px;
  border-radius: 8px;
  background: var(--surface-2);
  border-left: 3px solid var(--primary);
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--text3);
}
.acc-add-hint b {
  color: var(--text2);
  font-weight: 600;
}
/* 底栏：左说明右按钮 */
.acc-add-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 16px;
}
.acc-add-foot > span {
  font-size: 12px;
  color: var(--text3);
}
.acc-add-btns {
  display: flex;
  gap: 8px;
  flex: none;
}
/* 保存并验证进行中：按钮区整体替换成 loading 文案 */
.acc-checking {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-size: 12.5px;
  color: var(--text3);
}
</style>
