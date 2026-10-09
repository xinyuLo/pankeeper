<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { DRIVE_META, MAIN_ORDER } from '@/api/mock/meta';
import { LEVEL_META, getDriveLogs, type DriveLogData, type LogLevel } from '@/api/modules/driveLogs';
import { useThemeStore } from '@/store/theme';
echarts.use([LineChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer]);
const theme = useThemeStore();
const days = ref(7);
const loading = ref(true);
const err = ref('');
const data = ref<DriveLogData | null>(null);
async function load() {
    loading.value = true;
    err.value = '';
    try {
        data.value = await getDriveLogs(days.value);
    }
    catch (e: unknown) {
        const detail = (e as {
            response?: {
                data?: {
                    detail?: string;
                };
            };
        })?.response?.data?.detail;
        err.value = detail || '加载失败';
    }
    finally {
        loading.value = false;
    }
}
function setDays(d: number) {
    if (days.value === d)
        return;
    days.value = d;
    load();
}
onMounted(load);
function meta(drive: string) {
    return DRIVE_META[drive as keyof typeof DRIVE_META] || { name: drive, full: drive, color: '#8c8c8c' };
}
function levelMeta(l: LogLevel) {
    return LEVEL_META[l] || LEVEL_META.ok;
}
function shortDate(d: string) {
    return d.slice(5).replace('-', '/');
}
function tint(hex: string, alpha: number) {
    const n = parseInt(hex.replace('#', ''), 16);
    const r = (n >> 16) & 255;
    const g = (n >> 8) & 255;
    const b = n & 255;
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}
function cardStyle(drive: string) {
    const c = meta(drive).color;
    return {
        background: `linear-gradient(135deg, ${tint(c, 0.12)}, ${tint(c, 0.03)} 62%, transparent)`,
        borderColor: tint(c, 0.2),
    };
}
const chartEl = ref<HTMLDivElement | null>(null);
let chart: ReturnType<typeof echarts.init> | null = null;
function cssVar(name: string, fallback: string) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
}
function buildOption() {
    const trend = data.value?.trend || [];
    const axisColor = cssVar('--text3', '#8c8c8c');
    const splitColor = cssVar('--split', 'rgba(0,0,0,0.06)');
    const cardBg = cssVar('--card', '#ffffff');
    const textColor = cssVar('--text2', '#4b5563');
    const series = MAIN_ORDER.map((t) => ({
        name: DRIVE_META[t]?.full || t,
        color: DRIVE_META[t]?.color || '#8c8c8c',
        values: trend.map((d) => d.by[t] || 0),
    })).filter((s) => s.values.some((v) => v > 0));
    return {
        animationDuration: 420,
        grid: { left: 4, right: 18, top: 40, bottom: 2, containLabel: true },
        tooltip: {
            trigger: 'axis',
            backgroundColor: cardBg,
            borderColor: splitColor,
            textStyle: { color: textColor, fontSize: 12 },
            axisPointer: { type: 'line', lineStyle: { color: splitColor } },
            valueFormatter: (v: number) => `${v} 次`,
        },
        legend: {
            top: 0,
            right: 0,
            icon: 'roundRect',
            itemWidth: 10,
            itemHeight: 10,
            itemGap: 14,
            textStyle: { color: axisColor, fontSize: 12 },
        },
        xAxis: {
            type: 'category',
            boundaryGap: false,
            data: trend.map((d) => shortDate(d.date)),
            axisLine: { lineStyle: { color: splitColor } },
            axisTick: { show: false },
            axisLabel: { color: axisColor, fontSize: 11.5 },
        },
        yAxis: {
            type: 'value',
            minInterval: 1,
            splitLine: { lineStyle: { color: splitColor } },
            axisLabel: { color: axisColor, fontSize: 11.5 },
        },
        series: series.map((s) => ({
            name: s.name,
            type: 'line',
            smooth: 0.35,
            symbol: 'circle',
            symbolSize: 6,
            showSymbol: trend.length <= 31,
            data: s.values,
            itemStyle: { color: s.color },
            lineStyle: { width: 2.5, color: s.color },
            areaStyle: {
                color: {
                    type: 'linear',
                    x: 0,
                    y: 0,
                    x2: 0,
                    y2: 1,
                    colorStops: [
                        { offset: 0, color: tint(s.color, 0.26) },
                        { offset: 1, color: tint(s.color, 0.01) },
                    ],
                },
            },
        })),
    };
}
function renderChart() {
    if (!chartEl.value || !data.value)
        return;
    if (!chart)
        chart = echarts.init(chartEl.value);
    chart.setOption(buildOption(), true);
    chart.resize();
}
watch(data, async () => {
    await nextTick();
    renderChart();
});
watch(() => theme.isDark, async () => {
    await nextTick();
    renderChart();
});
function onResize() {
    chart?.resize();
}
window.addEventListener('resize', onResize);
onBeforeUnmount(() => {
    window.removeEventListener('resize', onResize);
    chart?.dispose();
    chart = null;
});
const notes = computed(() => [
    {
        t: '统计口径',
        d: '只统计 PanKeeper 自己发往网盘的请求（探活、检测连通、转存全过程）。你在网盘网页或 App 里的手动操作不计入。',
    },
    {
        t: '为什么要盯这个数',
        d: '请求过密是 Cookie 被平台判定为「非真人」的主要原因。夸克这类平台有设备指纹与频率限制，调得越勤，反而可能越快失效。',
    },
    {
        t: '建议区间',
        d: `自用场景（每天 1–3 个转存 + 一次探活）通常在 ${data.value?.thresholds.warn ?? 150} 次/天以内。越过这条线就该看看是不是任务跑重了。`,
    },
    {
        t: '每日探活会跳过忙碌的网盘',
        d: '当天已经产生过请求的网盘，探活会自动跳过——那次请求本身就是凭据有效的证明，没必要再问一遍。',
    },
    {
        t: '数据保留',
        d: `请求统计默认保留 ${data.value?.retain_days ?? 180} 天（半年），超期由每日定时任务自动清理。`,
    },
]);
</script>

<template>
  <div>
    
    <div class="lg-grid">
      <div
        v-for="c in data?.today.drives || []"
        :key="c.drive"
        class="card lg-card"
        :style="cardStyle(c.drive)"
      >
        <div class="lg-hd">
          <span class="lg-chip" :style="{ background: meta(c.drive).color }">{{ meta(c.drive).name }}</span>
          <span class="lg-name">{{ meta(c.drive).full }}</span>
          <span class="lg-tag" :class="c.level" :title="levelMeta(c.level).hint">
            <span class="dot"></span>{{ levelMeta(c.level).label }}
          </span>
        </div>
        <div class="lg-num">
          <b>{{ c.count }}</b><span>次 · 今日请求</span>
        </div>
      </div>
      
      <template v-if="loading && !data">
        <div v-for="i in 3" :key="'sk' + i" class="card lg-card">
          <div class="lg-hd"><span class="lg-skel" style="width: 120px"></span></div>
          <div class="lg-num"><span class="lg-skel" style="width: 84px; height: 26px"></span></div>
        </div>
      </template>
    </div>

    
    <div class="card lg-chart-card">
      <div class="lg-chart-hd">
        <div>
          <b>每日请求量</b>
          <span class="lg-sub">发往各网盘的 HTTP 请求总数（含探活与转存全过程）</span>
        </div>
        <div class="lg-range">
          <span :class="{ on: days === 7 }" @click="setDays(7)">近 7 天</span>
          <span :class="{ on: days === 30 }" @click="setDays(30)">近 30 天</span>
          <span :class="{ on: days === 90 }" @click="setDays(90)">近 90 天</span>
        </div>
      </div>

      <div v-if="err" class="lg-err">{{ err }}</div>
      <div v-else-if="loading && !data" class="lg-chart-skel">
        <span v-for="i in 7" :key="i" class="lg-skel-bar" :style="{ height: 30 + ((i * 13) % 55) + '%' }"></span>
      </div>
      <div v-show="!err && !!data" ref="chartEl" class="lg-chart-box"></div>

      <div class="lg-legend">
        <span><i class="lg-dot ok"></i>正常 ≤ {{ data?.thresholds.warn ?? 150 }}</span>
        <span><i class="lg-dot warn"></i>偏多 {{ (data?.thresholds.warn ?? 150) + 1 }}–{{ data?.thresholds.danger ?? 400 }}</span>
        <span><i class="lg-dot danger"></i>频繁 &gt; {{ data?.thresholds.danger ?? 400 }}</span>
        <span class="lg-legend-hint">（单位：次/天）</span>
      </div>
    </div>

    
    <div class="lg-notes">
      <div v-for="n in notes" :key="n.t" class="lg-note">
        <h4>{{ n.t }}</h4>
        <p>{{ n.d }}</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ===== 顶部卡片 ===== */
.lg-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 18px;
  margin-bottom: 18px;
}
.lg-card {
  padding: 18px 20px;
  border: 1px solid transparent;
  transition: box-shadow 0.18s, transform 0.18s;
}
.lg-card:hover {
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.07);
  transform: translateY(-1px);
}
.lg-hd { display: flex; align-items: center; gap: 8px; margin-bottom: 14px; }
.lg-chip {
  flex: none;
  width: 22px;
  height: 22px;
  border-radius: 6px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 11px;
  font-weight: 600;
}
.lg-name { font-size: 15px; font-weight: 600; min-width: 0; }
.lg-tag {
  margin-left: auto;
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 1px 9px;
  border-radius: 5px;
  font-size: 12px;
  font-weight: 500;
  border: 1px solid transparent;
}
.lg-tag .dot { width: 6px; height: 6px; border-radius: 50%; }
.lg-tag.ok { color: #389e0d; background: rgba(82, 196, 26, 0.12); border-color: rgba(82, 196, 26, 0.35); }
.lg-tag.ok .dot { background: var(--success); }
.lg-tag.warn { color: #d46b08; background: rgba(250, 173, 20, 0.14); border-color: rgba(250, 173, 20, 0.4); }
.lg-tag.warn .dot { background: var(--warning); }
.lg-tag.danger { color: #cf1322; background: rgba(255, 77, 79, 0.12); border-color: rgba(255, 77, 79, 0.38); }
.lg-tag.danger .dot { background: var(--error); }

.lg-num { display: flex; align-items: baseline; gap: 8px; }
.lg-num b { font-size: 30px; font-weight: 600; line-height: 1.1; letter-spacing: -0.02em; font-variant-numeric: tabular-nums; }
.lg-num span { font-size: 12.5px; color: var(--text3); }

/* ===== 趋势图 ===== */
.lg-chart-card { padding: 20px 22px 16px; }
.lg-chart-hd {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.lg-chart-hd b { font-size: 15px; }
.lg-sub { display: block; font-size: 12.5px; color: var(--text3); margin-top: 4px; }
.lg-range { display: inline-flex; gap: 4px; background: var(--split); border-radius: 9px; padding: 3px; flex: none; }
.lg-range span {
  padding: 4px 12px;
  font-size: 12.5px;
  border-radius: 7px;
  cursor: pointer;
  color: var(--text2);
  transition: all 0.16s;
  user-select: none;
}
.lg-range span:hover { color: var(--primary); }
.lg-range span.on { background: var(--card); color: var(--primary); font-weight: 500; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08); }

.lg-chart-box { height: 268px; }

.lg-legend { display: flex; align-items: center; gap: 16px; margin-top: 14px; font-size: 12.5px; color: var(--text3); flex-wrap: wrap; }
.lg-legend > span { display: inline-flex; align-items: center; gap: 5px; }
.lg-dot { width: 8px; height: 8px; border-radius: 2px; display: inline-block; }
.lg-dot.ok { background: var(--success); }
.lg-dot.warn { background: var(--warning); }
.lg-dot.danger { background: var(--error); }
.lg-legend-hint { color: var(--text4); }

.lg-err { padding: 40px 0; text-align: center; color: var(--error); font-size: 13px; }

/* ===== 底部说明区 ===== */
.lg-notes {
  margin-top: 18px;
  padding: 20px 22px;
  border-radius: 14px;
  border: 1px solid rgba(22, 119, 255, 0.14);
  background: linear-gradient(135deg, rgba(22, 119, 255, 0.07), rgba(114, 46, 209, 0.055) 58%, rgba(19, 194, 194, 0.04));
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 18px 26px;
}
.lg-note h4 {
  margin: 0 0 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text1, var(--text));
  display: flex;
  align-items: center;
  gap: 7px;
}
.lg-note h4::before {
  content: '';
  width: 3px;
  height: 13px;
  border-radius: 2px;
  background: linear-gradient(180deg, #1677ff, #722ed1);
  flex: none;
}
.lg-note p {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.75;
  color: var(--text2);
}
/* 暗色主题：浅色渐变在深底上几乎看不见，提高透明度并换用更亮的蓝紫 */
html[data-theme='dark'] .lg-notes {
  border-color: rgba(64, 150, 255, 0.16);
  background: linear-gradient(135deg, rgba(64, 150, 255, 0.11), rgba(146, 84, 222, 0.08) 58%, rgba(19, 194, 194, 0.05));
}

/* 骨架 */
.lg-skel {
  display: inline-block;
  height: 18px;
  border-radius: 6px;
  background: linear-gradient(90deg, var(--surface-2) 20%, var(--split) 45%, var(--surface-2) 70%);
  background-size: 220% 100%;
  animation: lgSweep 1.35s cubic-bezier(0.4, 0, 0.6, 1) infinite;
}
.lg-chart-skel { display: flex; align-items: flex-end; gap: 6px; height: 268px; }
.lg-skel-bar {
  flex: 1;
  border-radius: 4px 4px 2px 2px;
  background: linear-gradient(90deg, var(--surface-2) 20%, var(--split) 45%, var(--surface-2) 70%);
  background-size: 220% 100%;
  animation: lgSweep 1.35s cubic-bezier(0.4, 0, 0.6, 1) infinite;
}
@keyframes lgSweep {
  0% { background-position: 120% 0; }
  100% { background-position: -80% 0; }
}

@media (max-width: 767px) {
  .lg-grid { grid-template-columns: 1fr; gap: 12px; }
  .lg-chart-card { padding: 16px 14px 12px; }
  .lg-chart-box { height: 210px; }
  .lg-chart-skel { height: 210px; }
  .lg-notes { padding: 16px; gap: 14px; }
}
@media (prefers-reduced-motion: reduce) {
  .lg-skel, .lg-skel-bar { animation: none; }
  .lg-card { transition: none; }
}
</style>
