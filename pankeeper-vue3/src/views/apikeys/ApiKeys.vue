<script setup lang="ts">
/* API Key 管理页（侧边栏：系统管理 → API Key）
 * 外部调用凭据：拿着 Key 可跳过登录调接口（改密码除外，后端 403 拦）。
 * 口径对齐 QMS：完整密钥仅创建时显示一次，列表只回前 8 位前缀；状态开关停用/启用。
 * 数据源 GET/POST /api/apikeys、PUT /{id}/status、DELETE /{id}。
 * 表格口径与推送历史页一致：全局基础样式 + 本页 ak- 前缀。 */
import { onMounted, ref } from 'vue'
import { useBackGuard } from '@/composables/useBackGuard'
import { useIsMobile } from '@/composables/useIsMobile'
import { message } from 'ant-design-vue'
import dayjs from 'dayjs'
import {
  DeleteOutlined,
  InfoCircleFilled,
  PlusOutlined,
  ReloadOutlined,
} from '@ant-design/icons-vue'
import {
  listApiKeys,
  createApiKey,
  setApiKeyStatus,
  deleteApiKey,
  type ApiKeyItem,
} from '@/api/modules/apikeys'

const isMobile = useIsMobile()

const rows = ref<ApiKeyItem[]>([])
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    rows.value = (await listApiKeys()).items
  } catch {
    message.error('API Key 列表加载失败')
  } finally {
    loading.value = false
  }
}
onMounted(load)

function fmtTs(unix: number) {
  return unix ? dayjs(unix * 1000).format('YYYY-MM-DD HH:mm:ss') : '从未使用'
}

/* ===== 生成：输入名称 → 成功后原地切结果态（完整密钥仅此一次） ===== */
const createOpen = ref(false)
useBackGuard(createOpen, () => {
  createOpen.value = false
  resetCreate()
})
const createName = ref('')
const creating = ref(false)
/** 创建成功的结果；非空 = 弹窗切「仅显示一次」结果态 */
const created = ref<ApiKeyItem & { key: string } | null>(null)

function openCreate() {
  resetCreate()
  createOpen.value = true
}

function resetCreate() {
  createName.value = ''
  created.value = null
}

async function submitCreate() {
  const name = createName.value.trim()
  if (!name) {
    message.warning('请填写 Key 名称，标记用途方便日后管理')
    return
  }
  creating.value = true
  try {
    created.value = await createApiKey(name)
    message.success('API Key 已生成')
  } catch (e) {
    message.error((e as Error)?.message || '生成失败')
  } finally {
    creating.value = false
  }
}

function finishCreate() {
  createOpen.value = false
  resetCreate()
  load()
}

async function copyKey() {
  if (!created.value) return
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(created.value.key)
    } else {
      // 非安全上下文（http 部署）兜底：临时 textarea + execCommand
      const ta = document.createElement('textarea')
      ta.value = created.value.key
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
    }
    message.success('已复制到剪贴板')
  } catch {
    message.error('复制失败，请手动选中复制')
  }
}

/* ===== 状态开关 / 删除 ===== */
const busyId = ref(0)
async function toggle(r: ApiKeyItem) {
  busyId.value = r.id
  try {
    await setApiKeyStatus(r.id, !r.is_active)
    r.is_active = !r.is_active
    message.success(r.is_active ? '已启用' : '已停用')
  } catch {
    message.error('状态更新失败')
  } finally {
    busyId.value = 0
  }
}

async function remove(r: ApiKeyItem) {
  try {
    await deleteApiKey(r.id)
    message.success('已删除')
    await load()
  } catch {
    message.error('删除失败')
  }
}
</script>

<template>
  <div>
    <div class="card ak-flush">
      <!-- 说明条：调用方式 + 仅显示一次警示 -->
      <div class="ak-note">
        <InfoCircleFilled class="ak-note-icon" />
        <div>
          拿着 API Key 可跳过登录调用接口（修改密码除外）：推荐在请求头携带
          <code>X-API-Key</code>，也可以在 URL 追加 <code>api_key</code> 参数。
          <b>完整密钥仅在创建时显示一次</b>，请妥善保存。
        </div>
      </div>

      <div class="filterbar fb-head">
        <span class="small muted">
          共 {{ rows.length }} 个 Key · 启用 {{ rows.filter((r) => r.is_active).length }} 个
        </span>
        <span class="ak-flex1"></span>
        <a-button type="primary" @click="openCreate">
          <template #icon><PlusOutlined /></template>
          生成 API Key
        </a-button>
        <a-button :loading="loading" type="primary" ghost @click="load">
          <template #icon><ReloadOutlined /></template>
          刷新
        </a-button>
      </div>

      <table v-if="!isMobile" class="ak-table">
        <thead>
          <tr>
            <th>名称</th>
            <th style="width: 140px">API Key 前缀</th>
            <th style="width: 170px">状态</th>
            <th style="width: 190px">最后使用</th>
            <th style="width: 190px">创建时间</th>
            <th style="width: 64px; text-align: left">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id">
            <td>
              <div class="ak-name">{{ r.name }}</div>
            </td>
            <td><code class="ak-prefix">{{ r.key_prefix }}</code></td>
            <td>
              <span class="ak-status">
                <span class="small muted">停用</span>
                <a-switch
                  size="small"
                  :checked="r.is_active"
                  :loading="busyId === r.id"
                  @change="toggle(r)"
                />
                <span class="small">启用</span>
              </span>
            </td>
            <td class="small muted ak-nowrap">{{ fmtTs(r.last_used_at) }}</td>
            <td class="small muted ak-nowrap">{{ fmtTs(r.created_at) }}</td>
            <td>
              <a-popconfirm
                :title="`删除后用这个 Key 的调用会立即失效，确定删除「${r.name}」？`"
                ok-text="删除"
                cancel-text="取消"
                @confirm="remove(r)"
              >
                <a class="ak-del"><DeleteOutlined /> 删除</a>
              </a-popconfirm>
            </td>
          </tr>
          <tr v-if="!rows.length">
            <td colspan="6" class="pq-empty">
              {{ loading ? '加载中…' : '还没有 API Key · 点右上角「生成 API Key」创建第一个' }}
            </td>
          </tr>
        </tbody>
      </table>

      <!-- 手机端：一行一张卡片 -->
      <div v-else class="ak-cards">
        <div v-for="r in rows" :key="r.id" class="ak-card">
          <div class="ak-card-top">
            <span class="ak-name">{{ r.name }}</span>
            <span class="ak-status">
              <span class="small muted">停用</span>
              <a-switch size="small" :checked="r.is_active" :loading="busyId === r.id" @change="toggle(r)" />
              <span class="small">启用</span>
            </span>
          </div>
          <div class="ak-card-meta small muted">
            <code class="ak-prefix">{{ r.key_prefix }}</code> · 创建 {{ fmtTs(r.created_at) }}
          </div>
          <div class="ak-card-meta small muted">最后使用 {{ fmtTs(r.last_used_at) }}</div>
          <div class="ak-card-ops">
            <a-popconfirm
              :title="`删除后用这个 Key 的调用会立即失效，确定删除？`"
              ok-text="删除"
              cancel-text="取消"
              @confirm="remove(r)"
            >
              <a class="ak-del"><DeleteOutlined /> 删除</a>
            </a-popconfirm>
          </div>
        </div>
        <div v-if="!rows.length" class="pq-empty">
          {{ loading ? '加载中…' : '还没有 API Key' }}
        </div>
      </div>
    </div>

    <!-- 生成弹窗：两态复用（输入名称 → 完整密钥仅显示一次） -->
    <a-modal
      v-model:open="createOpen"
      :title="created ? 'API Key 已生成' : '生成 API Key'"
      :width="isMobile ? '94%' : 520"
      :mask-closable="false"
      destroy-on-close
    >
      <template v-if="!created">
        <div class="ak-form-row">
          <div class="ak-label">名称</div>
          <a-input
            v-model:value="createName"
            placeholder="标记用途，如：NAS 自动化脚本"
            :maxlength="64"
            @press-enter="submitCreate"
          />
        </div>
        <div class="small muted">名称用于列表辨认，不影响 Key 的任何能力。</div>
      </template>
      <template v-else>
        <div class="ak-warn">
          请立即保存这份密钥，<b>关闭后不再显示</b>（系统只存哈希，丢了只能删掉重建）。
        </div>
        <div class="ak-keybox">
          <code>{{ created.key }}</code>
        </div>
        <a-button type="primary" ghost @click="copyKey">复制完整密钥</a-button>
      </template>
      <template #footer>
        <template v-if="!created">
          <a-button @click="createOpen = false">取消</a-button>
          <a-button type="primary" :loading="creating" @click="submitCreate">生成</a-button>
        </template>
        <template v-else>
          <a-button type="primary" @click="finishCreate">我已保存，关闭</a-button>
        </template>
      </template>
    </a-modal>
  </div>
</template>

<style scoped>
/* 卡片去内边距：说明条/筛选条/表格连成一张卡（推送历史页同款） */
.ak-flush { padding: 0; overflow: hidden; }
.ak-flex1 { flex: 1; }
.ak-nowrap { white-space: nowrap; }

/* 说明条：浅底信息条（页面顶部，与表格同卡） */
.ak-note {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 12px 16px;
  background: var(--surface-2);
  border-bottom: 1px solid var(--split);
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--text2, var(--text));
}
.ak-note-icon { color: var(--primary); margin-top: 3px; }
.ak-note code {
  background: rgba(22, 119, 255, 0.08);
  color: var(--primary);
  padding: 0 5px;
  border-radius: 4px;
  font-size: 12px;
}

.ak-table { table-layout: fixed; }
.ak-table th,
.ak-table td { padding-left: 16px; padding-right: 16px; }
.ak-name { font-size: 13.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ak-prefix {
  background: var(--surface-2);
  border: 1px solid var(--split);
  padding: 1px 7px;
  border-radius: 5px;
  font-size: 12px;
}
.ak-status { display: inline-flex; align-items: center; gap: 7px; }
.ak-del {
  font-size: 12.5px;
  color: var(--error);
  white-space: nowrap;
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 3px;
  text-decoration-color: rgba(255, 77, 79, 0.35);
}
.ak-del:hover { text-decoration-color: var(--error); }

/* 手机卡片 */
.ak-cards { display: block; }
.ak-card { padding: 12px 14px; border-bottom: 1px solid var(--split); }
.ak-card:last-child { border-bottom: none; }
.ak-card-top { display: flex; align-items: center; gap: 8px; }
.ak-card-top .ak-name { flex: 1; min-width: 0; }
.ak-card-meta { margin-top: 5px; }
.ak-card-ops { margin-top: 8px; }

/* 生成弹窗：密钥展示盒 */
.ak-form-row { margin-bottom: 10px; }
.ak-label { font-size: 12px; color: var(--text3); margin-bottom: 6px; }
.ak-warn {
  font-size: 12.5px;
  color: #d46b08;
  background: #fff7e6;
  border: 1px solid #ffd591;
  border-radius: 8px;
  padding: 9px 12px;
  margin-bottom: 12px;
  line-height: 1.7;
}
html[data-theme='dark'] .ak-warn { background: rgba(212, 107, 8, 0.12); border-color: rgba(255, 213, 145, 0.3); }
.ak-keybox {
  background: var(--surface-2);
  border: 1px dashed var(--border);
  border-radius: 8px;
  padding: 12px 14px;
  margin-bottom: 14px;
  font-size: 13.5px;
  word-break: break-all;
  user-select: all;
}
</style>
