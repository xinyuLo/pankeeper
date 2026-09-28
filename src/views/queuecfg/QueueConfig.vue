<script setup lang="ts">
/* =====================================================================
 * 队列配置页 —— 原型 parts/page-queuecfg.html（cq- 前缀）的 Vue 移植。
 * 读写直连队列引擎：pkQueueCfgGet/pkQueueCfgSet（localStorage `pkq_cfg`），
 * 改完即时生效——正在执行的任务不受影响，排队任务被提上来时才读最新配置。
 * 卡底摘要条实时汇总当前节奏，改任意一项立即重算。
 * ===================================================================== */
import { reactive, watch } from 'vue'
import { message } from 'ant-design-vue'
import { pkQueueCfgGet, pkQueueCfgSet } from '@/queue/engine'
import type { QueueCfg } from '@/types/model'

/* ===== 四项配置：初始值从引擎取一次，此后本地表单为准（引擎镜像 cfgView 供别处渲染用） ===== */
const form = reactive<QueueCfg>({ ...pkQueueCfgGet() })

/* 任意一项变化 → 夹紧合法范围 → 写回引擎（即时生效）；提示防抖，数字框连点不刷屏 */
let toastTimer: number | undefined
watch(form, (v) => {
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
          <span class="muted small">同一时刻并行处理的任务数；调大会快，但同一网盘并行请求多、更容易触发风控</span>
        </div>
      </div>
      <div class="formrow">
        <label>转存触发间隔</label>
        <div class="ctl">
          <a-input-number v-model:value="form.gap" :min="0" class="cq-num" />
          <span class="muted cq-unit">秒</span>
          <span class="muted small">一个任务结束后，等这么久再开始下一个</span>
        </div>
      </div>
      <div class="formrow">
        <label>QMS 触发时间</label>
        <div class="ctl">
          <a-input-number v-model:value="form.qms" :min="0" class="cq-num" />
          <span class="muted cq-unit">秒</span>
          <span class="muted small">转存结束后，延迟这么久触发 QMS 刮削</span>
        </div>
      </div>
      <div class="formrow">
        <label>STRM 触发时间</label>
        <div class="ctl">
          <a-input-number v-model:value="form.strm" :min="0" class="cq-num" />
          <span class="muted cq-unit">秒</span>
          <span class="muted small">QMS 整理触发完成后再等这么久，触发 STRM 生成</span>
        </div>
      </div>

      <!-- 摘要条：模板直接绑表单值，改任意一项立即重算 -->
      <div class="cq-sum">
        按当前配置：单任务在转存完成后 <b>{{ form.qms }}s</b> 触发 QMS、QMS 完成后 <b>{{ form.strm }}s</b> 触发 STRM，任务之间再隔 <b>{{ form.gap }}s</b>；线程数 <b>{{ form.threads }}</b>，同一时刻最多 {{ form.threads }} 个任务在跑。
      </div>
    </div>

    <div class="note-box">
      <b>设计说明</b>
      <ul>
        <li><b>线程数</b>是全局并发上限：多任务同时跑时，每个任务独立走「转存 → QMS → STRM」全链路。</li>
        <li><b>转存触发间隔</b>从上一个任务完全结束（STRM 都发完）才开始计时，不是从转存结束。</li>
        <li>数值都存本地（pkq_cfg），刷新不丢；排队中的任务被提上来时才读取最新配置。</li>
      </ul>
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
</style>
