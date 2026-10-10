<script setup lang="ts">
/* =====================================================================
 * 队列配置页 —— 原型 parts/page-queuecfg.html（cq- 前缀）的 Vue 移植。
 * 读写直连队列引擎：pkQueueCfgGet/pkQueueCfgSet（localStorage `pkq_cfg`），
 * 改完即时生效——正在执行的任务不受影响，排队任务被提上来时才读最新配置。
 * 卡底摘要条实时汇总当前节奏，改任意一项立即重算。
 * ===================================================================== */
import { reactive, watch } from 'vue'
import { message } from 'ant-design-vue'
import { pkQueueCfgGet, pkQueueCfgFetch, pkQueueCfgSet } from '@/queue/engine'
import type { QueueCfg } from '@/types/model'

/* ===== 配置：先快照起步（F5 时引擎镜像还是默认值），异步拉到后端最新值再回填表单 =====
 * 只用 pkQueueCfgGet 的同步快照是 2026-10-03「反转刷新自己关闭」的根因：
 * 后端存着 true，表单却从默认 false 起步，看起来像开关自己弹回。 */
const form = reactive<QueueCfg>({ ...pkQueueCfgGet() })
let ready = false
pkQueueCfgFetch().then((c) => {
  Object.assign(form, c)
  ready = true
})

/* 任意一项变化 → 夹紧合法范围 → 写回引擎（即时生效）；提示防抖，数字框连点不刷屏。
 * ready 前的变更（回填触发）不写回不弹提示——那不是用户在改。 */
let toastTimer: number | undefined
watch(form, (v) => {
  if (!ready) return
  v.threads = Math.min(4, Math.max(1, Math.round(Number(v.threads) || 1)))
  v.gap = Math.max(0, Math.round(Number(v.gap) || 0))
  v.qms = Math.max(0, Math.round(Number(v.qms) || 0))
  v.strm = Math.max(0, Math.round(Number(v.strm) || 0))
  pkQueueCfgSet({ ...v })
  window.clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => message.success('队列配置已更新，对后续任务生效'), 400)
})
</script>

<template>
  <div>
    <div class="card cq-card">
      <div class="cq-cardhd">
        <div class="cq-headtt">
          <h2>队列配置</h2>
          <div class="cq-headdesc">
            所有转存都进队列慢慢跑，这里决定<b>跑多快、什么时候触发后续动作</b>。改完即时生效，
            正在执行的任务不受影响，只对后面的任务生效。
          </div>
        </div>
      </div>

      <div class="formrow">
        <label>线程数</label>
        <div class="ctl">
          <a-input-number v-model:value="form.threads" :min="1" :max="4" class="cq-num" />
        </div>
      </div>
      <div class="formrow">
        <label>转存触发间隔</label>
        <div class="ctl">
          <a-input-number v-model:value="form.gap" :min="0" class="cq-num" />
          <span class="muted cq-unit">秒</span>
        </div>
      </div>
      <div class="formrow">
        <label>QMS 触发时间</label>
        <div class="ctl">
          <a-input-number v-model:value="form.qms" :min="0" class="cq-num" />
          <span class="muted cq-unit">秒</span>
        </div>
      </div>
      <div class="formrow">
        <label>STRM 触发时间</label>
        <div class="ctl">
          <a-input-number v-model:value="form.strm" :min="0" class="cq-num" />
          <span class="muted cq-unit">秒</span>
        </div>
      </div>
      <div class="formrow">
        <label>QMS/STRM 反转</label>
        <div class="ctl">
          <a-switch v-model:checked="form.reverse" />
        </div>
      </div>
      <div class="cq-reverse-desc">
        开启后触发顺序<b>反转</b>：转存完成不再「先 QMS 刮削、等刮完再生成 STRM」，而是
        <b>先生成 STRM、再触发 QMS 刮削</b>（不等刮削完成，STRM 扫的是转存原目录）。
      </div>

      <!-- 摘要条：模板直接绑表单值，改任意一项立即重算 -->
      <div class="cq-sum">
        <template v-if="form.reverse">
          <b>反转已开启</b>：单任务在转存完成后 <b>{{ form.qms }}s</b> 先生成 STRM、再隔 <b>{{ form.strm }}s</b> 触发 QMS 刮削（任务之间仍隔 <b>{{ form.gap }}s</b>，线程数 <b>{{ form.threads }}</b>）。
        </template>
        <template v-else>
          按当前配置：单任务在转存完成后 <b>{{ form.qms }}s</b> 触发 QMS、QMS 完成后 <b>{{ form.strm }}s</b> 触发 STRM，任务之间再隔 <b>{{ form.gap }}s</b>；线程数 <b>{{ form.threads }}</b>，同一时刻最多 {{ form.threads }} 个任务在跑。
        </template>
      </div>
    </div>


  </div>
</template>

<style scoped>
/* 卡片去内边距：卡头/表单行/摘要条连成一张完整卡（原型 padding:0; overflow:hidden） */
.cq-card {
  padding: 0;
  overflow: hidden;
}
.cq-cardhd {
  padding: 18px 22px 16px;
  border-bottom: 1px solid var(--split);
}
.cq-headtt h2 {
  font-size: 15.5px;
  font-weight: 600;
  margin: 0 0 5px;
  line-height: 1.35;
}
.cq-headdesc {
  font-size: 12.5px;
  color: var(--text3);
  line-height: 1.7;
  max-width: 660px;
}
.cq-headdesc b {
  color: var(--text2);
  font-weight: 500;
}
/* 摘要条：虚线框灰底，数字主色高亮（原型 cq-sum） */
.cq-sum {
  margin: 2px 22px 16px;
  padding: 10px 14px;
  border-radius: 8px;
  background: var(--surface-2);
  border: 1px dashed var(--border);
  font-size: 12.5px;
  color: var(--text2);
  line-height: 1.7;
}
.cq-sum b {
  color: var(--primary);
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
/* 数字输入框统一窄宽 + 单位间距（原型 cq-num/cq-unit） */
.cq-num {
  width: 110px;
}
.cq-unit {
  margin-left: 8px;
}
/* 反转说明：紧贴开关行下方的浅色小字（cq-reverse-desc） */
.cq-reverse-desc {
  margin: 2px 22px 12px;
  padding: 10px 14px;
  border-radius: 8px;
  background: var(--surface-2);
  border: 1px dashed var(--border);
  font-size: 12.5px;
  color: var(--text2);
  line-height: 1.7;
}
.cq-reverse-desc b {
  color: var(--primary);
  font-weight: 500;
}
</style>
