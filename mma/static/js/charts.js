/* ============================================================
   Octagon IQ — Chart.js rendering helpers
   ============================================================ */

// Shared colour palette
const COLORS = {
  koTko:      '#ef4444',
  koTkoBg:    'rgba(239,68,68,0.15)',
  sub:        '#f59e0b',
  subBg:      'rgba(245,158,11,0.15)',
  dec:        '#3b82f6',
  decBg:      'rgba(59,130,246,0.15)',
  dq:         '#8b5cf6',
  dqBg:       'rgba(139,92,246,0.15)',
  other:      '#6b7280',
  otherBg:    'rgba(107,114,128,0.15)',
  red:        '#dc2626',
  redBright:  '#ef4444',
  gold:       '#f59e0b',
  green:      '#22c55e',
  blue:       '#3b82f6',
  text:       '#f0f0f4',
  textDim:    '#a0a0b8',
  gridLine:   'rgba(42,42,56,0.8)',
};

// Shared Chart.js defaults for dark theme
Chart.defaults.color = COLORS.textDim;
Chart.defaults.borderColor = COLORS.gridLine;
Chart.defaults.font.family = "'Inter', sans-serif";

// ── Doughnut helper ────────────────────────────────────────────────────────
function buildDoughnut(canvasId, labels, data, colors, bgColors) {
  const ctx = document.getElementById(canvasId);
  if (!ctx) return null;

  // Filter out zero-value slices so the chart isn't cluttered
  const filtered = labels.reduce((acc, label, i) => {
    if (data[i] > 0) {
      acc.labels.push(label);
      acc.data.push(data[i]);
      acc.colors.push(colors[i]);
      acc.bgColors.push(bgColors[i]);
    }
    return acc;
  }, { labels: [], data: [], colors: [], bgColors: [] });

  if (filtered.data.length === 0) {
    ctx.parentElement.innerHTML = '<p style="color:#6b7280;font-size:0.8rem;text-align:center;padding:1rem;">No data</p>';
    return null;
  }

  return new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: filtered.labels,
      datasets: [{
        data:            filtered.data,
        backgroundColor: filtered.bgColors,
        borderColor:     filtered.colors,
        borderWidth:     2,
        hoverBorderWidth: 3,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: '65%',
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => {
              const total = ctx.dataset.data.reduce((a, b) => a + b, 0);
              const pct   = total > 0 ? Math.round(ctx.raw / total * 100) : 0;
              return ` ${ctx.label}: ${ctx.raw} (${pct}%)`;
            }
          }
        }
      }
    }
  });
}

// ── Bar chart helper ───────────────────────────────────────────────────────
function buildBar(canvasId, labels, datasets, options = {}) {
  const ctx = document.getElementById(canvasId);
  if (!ctx) return null;

  return new Chart(ctx, {
    type: 'bar',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      indexAxis: options.horizontal ? 'y' : 'x',
      plugins: {
        legend: {
          display: datasets.length > 1,
          labels: { color: COLORS.textDim, font: { size: 11 }, boxWidth: 12 }
        },
        tooltip: { mode: 'index', intersect: false }
      },
      scales: {
        x: {
          grid: { color: COLORS.gridLine },
          ticks: { color: COLORS.textDim, font: { size: 10 } }
        },
        y: {
          beginAtZero: true,
          grid: { color: COLORS.gridLine },
          ticks: { color: COLORS.textDim, font: { size: 10 } }
        }
      }
    }
  });
}

// ── Radar chart helper ─────────────────────────────────────────────────────
function buildRadar(canvasId, labels, datasets) {
  const ctx = document.getElementById(canvasId);
  if (!ctx) return null;

  return new Chart(ctx, {
    type: 'radar',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      plugins: {
        legend: {
          labels: { color: COLORS.text, font: { size: 12 }, padding: 16 }
        },
        tooltip: { mode: 'point' }
      },
      scales: {
        r: {
          beginAtZero: true,
          grid:       { color: COLORS.gridLine },
          angleLines: { color: COLORS.gridLine },
          pointLabels: { color: COLORS.textDim, font: { size: 11 } },
          ticks: { display: false }
        }
      }
    }
  });
}

// ── Dynamic method breakdown from fight_history ────────────────────────────
function computeMethodBreakdown(history, promoFilter) {
  const fights = promoFilter === 'ufc'
    ? history.filter(f => (f.promotion || '').toUpperCase() === 'UFC')
    : history;

  const count = (result, method) =>
    fights.filter(f => f.result === result && f.method === method).length;

  const wins   = fights.filter(f => f.result === 'W');
  const losses = fights.filter(f => f.result === 'L');

  const wko  = count('W', 'KO/TKO');
  const wsub = count('W', 'Submission');
  const wdec = count('W', 'Decision');
  const wdq  = count('W', 'DQ');
  const woth = fights.filter(f => f.result === 'W' && !['KO/TKO','Submission','Decision','DQ'].includes(f.method)).length;

  const lko  = count('L', 'KO/TKO');
  const lsub = count('L', 'Submission');
  const ldec = count('L', 'Decision');
  const ldq  = count('L', 'DQ');
  const loth = fights.filter(f => f.result === 'L' && !['KO/TKO','Submission','Decision','DQ'].includes(f.method)).length;

  const pct = (n, d) => d > 0 ? Math.round(n / d * 100) : 0;
  const wt  = wins.length;
  const lt  = losses.length;

  return {
    wins:  { ko: wko, sub: wsub, dec: wdec, dq: wdq, other: woth, total: wt },
    losses: { ko: lko, sub: lsub, dec: ldec, dq: ldq, other: loth, total: lt },
    pct: {
      wko: pct(wko, wt), wsub: pct(wsub, wt), wdec: pct(wdec, wt),
      lko: pct(lko, lt), lsub: pct(lsub, lt), ldec: pct(ldec, lt),
    }
  };
}

function renderMethodLegend(elId, counts, pcts) {
  const el = document.getElementById(elId);
  if (!el) return;
  const items = [
    ['ko', 'legend-dot--ko',  'KO/TKO'],
    ['sub','legend-dot--sub', 'Sub'],
    ['dec','legend-dot--dec', 'Dec'],
    ['dq', 'legend-dot--dq',  'DQ'],
  ];
  el.innerHTML = items
    .filter(([key]) => counts[key] > 0)
    .map(([key, cls, label]) =>
      `<div class="legend-item"><span class="legend-dot ${cls}"></span>${label} <strong>${counts[key]}</strong> (${pcts[key] !== undefined ? pcts[key] : '?'}%)</div>`
    ).join('');
}

// Destroy and recreate a chart
const _chartInstances = {};
function _rebuildDoughnut(id, labels, data, colors, bgColors) {
  if (_chartInstances[id]) { _chartInstances[id].destroy(); }
  _chartInstances[id] = buildDoughnut(id, labels, data, colors, bgColors);
}

// ── Fighter detail charts ──────────────────────────────────────────────────
function renderFighterCharts(f, promoFilter) {
  const history = f.fight_history || [];
  const bd = computeMethodBreakdown(history, promoFilter || 'all');

  // Wins doughnut
  _rebuildDoughnut(
    'winsChart',
    ['KO/TKO', 'Submission', 'Decision', 'DQ', 'Other'],
    [bd.wins.ko, bd.wins.sub, bd.wins.dec, bd.wins.dq, bd.wins.other],
    [COLORS.koTko, COLORS.sub, COLORS.dec, COLORS.dq, COLORS.other],
    [COLORS.koTkoBg, COLORS.subBg, COLORS.decBg, COLORS.dqBg, COLORS.otherBg]
  );
  renderMethodLegend('winsLegend', bd.wins, {
    ko: bd.pct.wko, sub: bd.pct.wsub, dec: bd.pct.wdec, dq: 0
  });

  // Losses doughnut
  _rebuildDoughnut(
    'lossesChart',
    ['KO/TKO', 'Submission', 'Decision', 'DQ', 'Other'],
    [bd.losses.ko, bd.losses.sub, bd.losses.dec, bd.losses.dq, bd.losses.other],
    [COLORS.koTko, COLORS.sub, COLORS.dec, COLORS.dq, COLORS.other],
    [COLORS.koTkoBg, COLORS.subBg, COLORS.decBg, COLORS.dqBg, COLORS.otherBg]
  );
  renderMethodLegend('lossesLegend', bd.losses, {
    ko: bd.pct.lko, sub: bd.pct.lsub, dec: bd.pct.ldec, dq: 0
  });

  // Striking & Grappling bar chart (career stats — independent of toggle)
  if (!_chartInstances['strikingChart']) {
    buildBar('strikingChart', ['Landed/min', 'Absorbed/min', 'TD/15m', 'Sub/15m'], [{
      label:           'Career Avg',
      data:            [f.slpm ?? 0, f.sapm ?? 0, f.td_avg ?? 0, f.sub_avg ?? 0],
      backgroundColor: [COLORS.koTkoBg, 'rgba(239,68,68,0.08)', COLORS.decBg, COLORS.subBg],
      borderColor:     [COLORS.koTko, COLORS.redBright, COLORS.dec, COLORS.sub],
      borderWidth: 2,
      borderRadius: 4,
    }]);
    _chartInstances['strikingChart'] = true;
  }
}

// ── Matchup charts ─────────────────────────────────────────────────────────
function renderMatchupCharts(fa, fb) {
  // Method doughnuts for each fighter
  [
    ['winsChartA',   fa, 'wins'],
    ['winsChartB',   fb, 'wins'],
    ['lossesChartA', fa, 'losses'],
    ['lossesChartB', fb, 'losses'],
  ].forEach(([id, fighter, side]) => {
    const prefix = side === 'wins' ? 'wins_by' : 'losses_by';
    const result = side === 'wins' ? 'W' : 'L';
    buildDoughnut(
      id,
      ['KO/TKO', 'Sub', 'Dec', 'DQ', 'Other'],
      [
        fighter[`${prefix}_ko_tko`]  || 0,
        fighter[`${prefix}_sub`]     || 0,
        fighter[`${prefix}_dec`]     || 0,
        fighter[`${prefix}_dq`]      || 0,
        fighter[`${prefix}_other`]   || 0,
      ],
      [COLORS.koTko, COLORS.sub, COLORS.dec, COLORS.dq, COLORS.other],
      [COLORS.koTkoBg, COLORS.subBg, COLORS.decBg, COLORS.dqBg, COLORS.otherBg]
    );
  });

  // Radar chart — normalise each stat to 0-100 scale across both fighters
  const radarStats = [
    { label: 'Landed/min', a: fa.slpm    ?? 0,  b: fb.slpm    ?? 0,  max: 10 },
    { label: 'Str Acc%',   a: (fa.str_acc ?? 0) * 100, b: (fb.str_acc ?? 0) * 100, max: 100 },
    { label: 'Str Def%',   a: (fa.str_def ?? 0) * 100, b: (fb.str_def ?? 0) * 100, max: 100 },
    { label: 'TD/15m',     a: fa.td_avg  ?? 0,  b: fb.td_avg  ?? 0,  max: 6  },
    { label: 'TD Def%',    a: (fa.td_def ?? 0) * 100, b: (fb.td_def ?? 0) * 100, max: 100 },
    { label: 'Finish%',    a: fa.finish_rate ?? 0, b: fb.finish_rate ?? 0, max: 100 },
    { label: 'Win Rate%',  a: fa.win_rate ?? 0,   b: fb.win_rate ?? 0,   max: 100 },
  ];

  const normalise = (val, max) => max > 0 ? Math.min((val / max) * 100, 100) : 0;

  buildRadar(
    'radarChart',
    radarStats.map(s => s.label),
    [
      {
        label:           fa.name,
        data:            radarStats.map(s => normalise(s.a, s.max)),
        backgroundColor: 'rgba(220,38,38,0.15)',
        borderColor:     COLORS.red,
        pointBackgroundColor: COLORS.red,
        borderWidth: 2,
        pointRadius: 4,
      },
      {
        label:           fb.name,
        data:            radarStats.map(s => normalise(s.b, s.max)),
        backgroundColor: 'rgba(59,130,246,0.15)',
        borderColor:     COLORS.blue,
        pointBackgroundColor: COLORS.blue,
        borderWidth: 2,
        pointRadius: 4,
      }
    ]
  );
}
