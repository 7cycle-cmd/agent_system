/**
 * Telemetry HUD — ApexCharts bar charts, dark devops instrument-panel style.
 *
 * Design: "Style C" — dark slate panel, orange accent, monospace axes,
 * rounded bar tops, gradient fills, muted gridlines, glow on hover.
 * Detail lives in a pop-up modal + a full drill-down page, so the charts
 * themselves stay quiet (user requirement: no noise).
 *
 * Bundle: tree-shaken to the bar chart type only.
 */
import ApexCharts from 'apexcharts/core';
import 'apexcharts/bar';
import 'apexcharts/features/legend';

/* ---------------------------------------------------------------- theme -- */

export const HUD = {
  bg: '#0d1117',
  panel: '#161b22',
  panelAlt: '#1c2128',
  line: '#30363d',
  grid: 'rgba(255, 255, 255, 0.06)',
  ink: '#e6edf3',
  muted: '#8b949e',
  accent: '#f97316', // orange
  accentSoft: 'rgba(249, 115, 22, 0.14)',
  // Series palette: orange family first, then complementary HUD hues.
  series: ['#f97316', '#fbbf24', '#fb7185', '#a78bfa', '#38bdf8', '#34d399'],
  levels: {
    alert: '#f43f5e',
    warn: '#f59e0b',
    info: '#38bdf8',
    ok: '#10b981',
  },
};

const FONT_MONO =
  "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace";

/** Level name -> HUD colour, so watchdog bars are coloured by severity. */
function seriesColor(name, i) {
  const key = String(name || '').toLowerCase();
  if (HUD.levels[key]) return HUD.levels[key];
  return HUD.series[i % HUD.series.length];
}

/* ------------------------------------------------------------- formatter -- */

/** Compact large numbers for axis labels: 15883 -> "15.9k". */
export function fmtCompact(n) {
  const v = Number(n) || 0;
  const abs = Math.abs(v);
  if (abs >= 1e9) return (v / 1e9).toFixed(1).replace(/\.0$/, '') + 'B';
  if (abs >= 1e6) return (v / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
  if (abs >= 1e4) return (v / 1e3).toFixed(1).replace(/\.0$/, '') + 'k';
  if (abs >= 1e3) return (v / 1e3).toFixed(1).replace(/\.0$/, '') + 'k';
  return String(v);
}

export function fmtFull(n) {
  const v = Number(n) || 0;
  return v.toLocaleString('en-US');
}

/* ----------------------------------------------------------------- chart -- */

const registry = new WeakMap(); // el -> ApexCharts instance

/**
 * Render (or update) a HUD bar chart.
 *
 * @param {HTMLElement} el        container
 * @param {object} metric         telemetry envelope from /api/telemetry/*
 * @param {object} [opts]
 * @param {Function} [opts.onBarClick]  (category, seriesName) => void
 * @param {string} [opts.height]  css height, default '220px'
 */
export function hudBarChart(el, metric, opts = {}) {
  if (!el) return null;
  const categories = metric?.categories || [];
  const series = (metric?.series || []).map((s, i) => ({
    name: s.name,
    data: s.data,
  }));
  const stackable = series.length > 1 && metric.bucket !== 'category';

  const options = {
    chart: {
      type: 'bar',
      height: opts.height || 220,
      background: 'transparent',
      fontFamily: FONT_MONO,
      toolbar: {
        show: false, // quiet by default; export lives in the modal
      },
      animations: {
        enabled: true,
        speed: 420,
        animateGradually: { enabled: true, delay: 60 },
        dynamicAnimation: { enabled: true, speed: 250 },
      },
      events: {
        dataPointSelection: (_e, _ctx, cfg) => {
          if (opts.onBarClick) {
            opts.onBarClick(categories[cfg.dataPointIndex], series[cfg.seriesIndex]?.name);
          }
        },
      },
    },
    theme: { mode: 'dark' },
    colors: series.map((s, i) => seriesColor(s.name, i)),
    series,
    plotOptions: {
      bar: {
        borderRadius: 5,
        borderRadiusApplication: 'end',
        borderRadiusWhenStacked: 'last',
        columnWidth: categories.length > 16 ? '62%' : '48%',
        stacked: stackable,
        horizontal: false,
      },
    },
    dataLabels: { enabled: false },
    stroke: { show: false },
    fill: {
      type: 'gradient',
      gradient: {
        shade: 'dark',
        type: 'vertical',
        shadeIntensity: 0.35,
        opacityFrom: 0.95,
        opacityTo: 0.62,
        stops: [0, 100],
      },
    },
    legend: {
      show: series.length > 1,
      position: 'top',
      horizontalAlign: 'right',
      fontSize: '11px',
      fontFamily: FONT_MONO,
      labels: { colors: HUD.muted },
      markers: { size: 5, strokeWidth: 0 },
      itemMargin: { horizontal: 8, vertical: 0 },
    },
    xaxis: {
      categories,
      tickAmount: Math.min(categories.length, 12),
      labels: {
        style: { colors: HUD.muted, fontSize: '10px', fontFamily: FONT_MONO },
        rotate: categories.length > 8 ? -45 : 0,
        rotateAlways: false,
        trim: true,
        hideOverlappingLabels: true,
      },
      axisBorder: { show: false },
      axisTicks: { show: false },
      tooltip: { enabled: false },
    },
    yaxis: {
      labels: {
        style: { colors: HUD.muted, fontSize: '10px', fontFamily: FONT_MONO },
        formatter: (v) => fmtCompact(v),
      },
    },
    grid: {
      borderColor: HUD.grid,
      strokeDashArray: 3,
      xaxis: { lines: { show: false } },
      yaxis: { lines: { show: true } },
      padding: { top: 0, right: 4, bottom: 0, left: 4 },
    },
    tooltip: {
      theme: 'dark',
      shared: stackable,
      intersect: !stackable,
      followCursor: true,
      style: { fontSize: '12px', fontFamily: FONT_MONO },
      custom: ({ series: s, seriesIndex, dataPointIndex, w }) => {
        const cat = w.globals.labels[dataPointIndex] ?? '';
        const rows = stackable
          ? w.globals.seriesNames
              .map(
                (n, i) =>
                  `<div style="display:flex;justify-content:space-between;gap:14px">
                     <span style="color:${HUD.muted}">${n}</span>
                     <b style="color:${HUD.ink}">${fmtFull(s[i][dataPointIndex])}</b>
                   </div>`
              )
              .join('')
          : `<div style="display:flex;justify-content:space-between;gap:14px">
               <span style="color:${HUD.muted}">${w.globals.seriesNames[seriesIndex]}</span>
               <b style="color:${HUD.ink}">${fmtFull(s[seriesIndex][dataPointIndex])}</b>
             </div>`;
        return `<div style="background:${HUD.panelAlt};border:1px solid ${HUD.line};
                       border-radius:8px;padding:8px 10px;min-width:150px">
                  <div style="color:${HUD.accent};font-weight:600;margin-bottom:4px">${cat}</div>
                  ${rows}
                </div>`;
      },
    },
    states: {
      hover: { filter: { type: 'lighten', value: 0.12 } },
      active: { filter: { type: 'none' } },
    },
    noData: {
      text: 'no data in range',
      align: 'center',
      verticalAlign: 'middle',
      style: { color: HUD.muted, fontSize: '12px', fontFamily: FONT_MONO },
    },
  };

  const existing = registry.get(el);
  if (existing) {
    existing.updateOptions(options, false, true);
    return existing;
  }
  el.innerHTML = '';
  const chart = new ApexCharts(el, options);
  chart.render();
  registry.set(el, chart);
  return chart;
}

/** Tear down every chart bound to a container (call before replacing the DOM). */
export function destroyCharts(root) {
  if (!root) return;
  root.querySelectorAll('.hud-chart').forEach((el) => {
    const c = registry.get(el);
    if (c) {
      try {
        c.destroy();
      } catch (_) {}
      registry.delete(el);
    }
  });
}

/* ------------------------------------------------------------------ card -- */

/**
 * HUD card wrapper — the quiet unit of the telemetry page.
 * One card = one metric: header, totals, chart. Everything else is in the modal.
 *
 * @param {object} metric
 * @param {object} [opts]
 * @param {boolean} [opts.open]   render the drill-down link
 */
export function hudCardHtml(metric, opts = {}) {
  const m = metric || {};
  const totals = m.totals || {};
  const ok = !!m.ok;
  const hasData = (m.series || []).some((s) => (s.data || []).some((v) => Number(v) > 0));

  const totalsHtml = Object.entries(totals)
    .filter(([k]) => !['by_type', 'by_kind', 'by_model'].includes(k))
    .slice(0, 4)
    .map(
      ([k, v]) =>
        `<div class="hud-stat">
           <div class="hud-stat-k">${k.replace(/_/g, ' ')}</div>
           <div class="hud-stat-v">${
             typeof v === 'number' ? (Number.isInteger(v) ? fmtFull(v) : v) : v ?? '-'
           }</div>
         </div>`
    )
    .join('');

  const badge = !ok
    ? `<span class="hud-badge hud-badge-err" title="${m.error || ''}">degraded</span>`
    : hasData
      ? '<span class="hud-badge hud-badge-ok">live</span>'
      : '<span class="hud-badge">empty</span>';

  return `
    <section class="hud-card" data-metric="${m.metric || ''}">
      <header class="hud-card-h">
        <div>
          <h3 class="hud-card-t">${m.title || m.metric || 'metric'}</h3>
          <p class="hud-card-s">${m.bucket || ''} · ${m.unit || ''}</p>
        </div>
        <div class="hud-card-hr">${badge}
          ${opts.open ? `<button type="button" class="hud-open" data-hud-open="${m.metric || ''}">detail →</button>` : ''}
        </div>
      </header>
      ${totalsHtml ? `<div class="hud-stats">${totalsHtml}</div>` : ''}
      ${
        ok
          ? `<div class="hud-chart" data-hud-chart="${m.metric || ''}"></div>`
          : `<div class="hud-err">${m.error || 'metric unavailable'}</div>`
      }
    </section>`;
}