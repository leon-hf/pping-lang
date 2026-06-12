let _chart = null;
let _ttftChart = null;
let _tpotChart = null;
let _e2eChart = null;
let _kClassChart = null;   // kernel 类占比堆叠面积(实时)
let _kUtilChart = null;    // GPU busy + 同步等待(实时)
let _kRoofChart = null;    // Kernel tab 里复用的第二个 roofline 图(懒建,与 Overview 同数据)
let _lastRoofline = null;  // 最近一次 /api/roofline 数据,懒建第二个图时回填

// 在点旁绘制文字标签(簇语义 / 并发标记)—— data 点带 label 字段即画
const _roofLabelsPlugin = {
  id: 'roofLabels',
  afterDatasetsDraw(chart) {
    const { ctx } = chart;
    chart.data.datasets.forEach((ds, di) => {
      const meta = chart.getDatasetMeta(di);
      if (!meta || meta.hidden) return;
      ds.data.forEach((p, i) => {
        if (!p || !p.label || !meta.data[i]) return;
        const el = meta.data[i];
        ctx.save();
        ctx.font = (p.labelBold ? '600 ' : '400 ') + '10.5px Inter, "PingFang SC", sans-serif';
        ctx.fillStyle = p.labelColor || '#7a6e63';
        ctx.textAlign = 'left';
        ctx.fillText(p.label, el.x + 9, el.y + (p.labelDy != null ? p.labelDy : 4));
        ctx.restore();
      });
    });
  },
};

// roofline 散点图配置工厂 —— Overview 与 Kernel tab 两个图共用同一份配置
function _makeRooflineChart(ctx) {
  return new Chart(ctx, {
    type: 'scatter',
    plugins: [_roofLabelsPlugin],
    data: {
      datasets: [
        {
          label: '当前样本', data: [],
          backgroundColor: 'rgba(13, 139, 128, 0.55)', borderColor: '#0d8b80', borderWidth: 1,
          pointRadius: 4, pointHoverRadius: 7, pointHoverBackgroundColor: '#0d8b80',
          pointHoverBorderColor: '#fff', pointHoverBorderWidth: 2, showLine: false, order: 3,
        },
        {
          label: 'Compute roof', data: [], showLine: true, borderColor: '#dc4d3e', borderWidth: 2.5,
          pointRadius: 0, fill: 'origin', backgroundColor: 'rgba(220, 77, 62, 0.06)', tension: 0, order: 1,
        },
        {
          label: 'Memory roof', data: [], showLine: true, borderColor: '#5147c8', borderWidth: 2.5,
          pointRadius: 0, fill: 'origin', backgroundColor: 'rgba(81, 71, 200, 0.06)', tension: 0, order: 2,
        },
        {
          // 调优地图:decode 的算术强度≈batch → 扩 batch 沿带宽上界向右爬,ridge point 后 compute-bound
          label: 'batch scaling envelope', data: [], showLine: true, borderColor: '#a8998a',
          borderDash: [5, 4], borderWidth: 1.5, pointRadius: 3.5, pointStyle: 'rectRot',
          backgroundColor: '#a8998a', fill: false, order: 4,
        },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#1c1410', titleColor: '#fff', bodyColor: '#fdf9f2', padding: 11,
          cornerRadius: 8, displayColors: false, borderWidth: 0, titleFont: { weight: '600' },
          callbacks: {
            title: () => '',
            label: (ctx) => {
              const ds = ctx.dataset.label;
              if (ds === '当前样本') {
                const n = ctx.raw && ctx.raw.n > 1 ? [`合并 ${ctx.raw.n} 个 step`] : [];
                return [`AI:  ${ctx.parsed.x.toFixed(2)} FLOPs/byte`, `TPut: ${ctx.parsed.y.toFixed(1)} TFLOPs/s`, ...n];
              }
              if (ds === 'batch scaling envelope') {
                return `B=${ctx.raw.b}: bandwidth-bound 上界 ${ctx.parsed.y.toFixed(1)} TFLOPs/s`;
              }
              return `${ds}: ${ctx.parsed.y.toFixed(1)} TFLOPs/s`;
            },
          },
        },
      },
      scales: {
        x: {
          type: 'logarithmic',
          title: { display: true, text: 'Arithmetic Intensity (FLOPs / byte)', color: '#7a6e63', font: { size: 11.5, weight: '600' } },
          ticks: { color: '#a8998a', font: { size: 11 } }, grid: { color: '#f3ebdb', drawBorder: false },
        },
        y: {
          type: 'logarithmic',
          title: { display: true, text: 'Achieved Throughput (TFLOPs/s)', color: '#7a6e63', font: { size: 11.5, weight: '600' } },
          ticks: { color: '#a8998a', font: { size: 11 } }, grid: { color: '#f3ebdb', drawBorder: false },
        },
      },
    },
  });
}

// 相近 step 合并成簇心:log 网格分桶(x 每十倍程 6 桶、y 4 桶),桶内取几何均值,
// n = 合并步数 → 点大小。免得 60s 内每 step 一个点密密麻麻(信息在簇,不在单点)。
function _aggRooflinePoints(raw) {
  const bins = new Map();
  for (const p of raw) {
    if (!(p.x > 0) || !(p.y > 0)) continue;
    const k = Math.round(Math.log10(p.x) * 6) + '|' + Math.round(Math.log10(p.y) * 4);
    let b = bins.get(k);
    if (!b) { b = { sx: 0, sy: 0, n: 0 }; bins.set(k, b); }
    b.sx += Math.log10(p.x); b.sy += Math.log10(p.y); b.n++;
  }
  const out = [];
  for (const b of bins.values()) {
    out.push({ x: Math.pow(10, b.sx / b.n), y: Math.pow(10, b.sy / b.n), n: b.n });
  }
  return out;
}

// 把 /api/roofline 数据填进一个 roofline 图(点 + 两条 roof)
function _applyRooflineData(chart, data) {
  if (!chart) return;
  const agg = _aggRooflinePoints((data.points || []).map(p => ({ x: p.ai, y: p.throughput_tflops })));
  // A:簇语义标签 —— 步数最多的簇 = decode 主体(decode 步数远多于 prefill);
  // 其余里 x 明显更大的标 prefill
  if (agg.length) {
    const dec = agg.reduce((a, p) => (p.n > a.n ? p : a));
    dec.label = 'decode · operating point';
    dec.labelBold = true;
    dec.labelColor = '#0d8b80';
    const rest = agg.filter(p => p !== dec && p.n > 0);
    if (rest.length) {
      const pf = rest.reduce((a, p) => (p.x > a.x ? p : a));
      if (pf.x > dec.x * 2.5) { pf.label = 'prefill'; pf.labelColor = '#7a6e63'; }
    }
  }
  chart.data.datasets[0].data = agg;
  // 点半径 ∝ log(合并步数):单步 4px,几十步 ~10px,封顶 13px
  chart.data.datasets[0].pointRadius = agg.map(p => Math.min(13, 3 + 2.2 * Math.log2(1 + p.n)));
  chart.data.datasets[0].pointHoverRadius = agg.map(p => Math.min(15, 5 + 2.2 * Math.log2(1 + p.n)));
  if (data.peak && data.peak.compute_tflops && data.peak.mem_bw_tbs) {
    const peakC = data.peak.compute_tflops, peakBW = data.peak.mem_bw_tbs, knee = peakC / peakBW;
    const xMin = 0.1, xMax = Math.max(1000, knee * 3);
    chart.data.datasets[1].data = [{ x: knee, y: peakC }, { x: xMax, y: peakC }];
    chart.data.datasets[2].data = [{ x: xMin, y: peakBW * xMin }, { x: knee, y: peakC }];
    // B:batch scaling envelope —— decode AI≈batch,沿带宽上界标 B=1→ridge point
    const traj = [];
    for (const b of [1, 4, 8, 16, 32, 64, 128, 256, 512]) {
      if (b > knee * 1.1) break;
      traj.push({
        x: b, y: Math.min(peakBW * b, peakC), b,
        label: [1, 8, 32, 128].includes(b) ? `B=${b}` : '', labelDy: -9, labelColor: '#a8998a',
      });
    }
    traj.push({ x: knee, y: peakC, b: Math.round(knee), label: `ridge point (AI=${knee.toFixed(0)})`, labelDy: -9, labelColor: '#dc4d3e' });
    chart.data.datasets[3].data = traj;
  } else {
    chart.data.datasets[1].data = [];
    chart.data.datasets[2].data = [];
    chart.data.datasets[3].data = [];
  }
  chart.update('none');
}

function _createMiniLatencyChart(canvasId, color) {
  const ctx = document.getElementById(canvasId);
  if (!ctx) return null;
  return new Chart(ctx.getContext('2d'), {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        {
          label: 'p50',
          data: [],
          borderColor: color + '55',
          backgroundColor: 'transparent',
          borderWidth: 1.5,
          tension: 0.35,
          fill: false,
          pointRadius: 0,
        },
        {
          label: 'p99',
          data: [],
          borderColor: color,
          backgroundColor: color + '1a',
          borderWidth: 2,
          tension: 0.35,
          fill: true,
          pointRadius: 0,
          pointHoverRadius: 4,
          pointHoverBackgroundColor: color,
          pointHoverBorderColor: '#fff',
          pointHoverBorderWidth: 2,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#1c1410',
          titleColor: '#fff',
          bodyColor: '#fdf9f2',
          padding: 9,
          cornerRadius: 6,
          displayColors: true,
          borderWidth: 0,
          titleFont: { weight: '600', size: 11 },
          bodyFont: { size: 11 },
          callbacks: {
            label: (c) => `${c.dataset.label}: ${c.parsed.y == null ? '—' : c.parsed.y.toFixed(0) + ' ms'}`,
          },
        },
      },
      scales: {
        y: {
          beginAtZero: true,
          ticks: {
            color: '#a8998a',
            font: { size: 10 },
            maxTicksLimit: 4,
            callback: (v) => v + 'ms',
          },
          grid: { color: '#f3ebdb', drawBorder: false },
        },
        x: { display: false, grid: { display: false } },
      },
    },
  });
}

function _updateMiniLatencyChart(chart, buckets) {
  if (!chart) return;
  chart.data.labels = buckets.map(b => Math.round(b.t) + 's');
  chart.data.datasets[0].data = buckets.map(b => b.p50);
  chart.data.datasets[1].data = buckets.map(b => b.p99);
  chart.update('none');
}

// kernel 类(堆叠面积)— 顺序 = 画的层序
const _KCLASSES = [
  ['gemm', '#5147c8', 'GEMM'], ['attention', '#0d8b80', 'Attention'],
  ['comm', '#dc4d3e', '通信'], ['norm', '#c2660d', 'Norm'],
  ['activation', '#5a8f1f', 'Activation'], ['rotary', '#be1556', 'Rotary'],
  ['other', '#a8998a', '其它'],
];
const _kTip = {backgroundColor:'#1c1410',titleColor:'#fff',bodyColor:'#fdf9f2',padding:9,cornerRadius:6,borderWidth:0,titleFont:{weight:'600',size:11},bodyFont:{size:11}};
function _createKClassChart() {
  const ctx = document.getElementById('k-class-chart'); if (!ctx) return null;
  return new Chart(ctx.getContext('2d'), {
    type: 'line',
    data: {labels: [], datasets: _KCLASSES.map(([cls, c, label]) => ({
      label, data: [], borderColor: c, backgroundColor: c + 'd9',
      borderWidth: 0, fill: true, pointRadius: 0, tension: 0.25,
    }))},
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: {mode: 'index', intersect: false},
      plugins: {legend: {display: false},
        tooltip: {..._kTip, callbacks: {label: c => `${c.dataset.label}: ${c.parsed.y == null ? '—' : c.parsed.y.toFixed(0) + '%'}`}}},
      scales: {
        y: {stacked: true, min: 0, max: 100, ticks: {color: '#a8998a', font: {size: 10}, maxTicksLimit: 5, callback: v => v + '%'}, grid: {color: '#f3ebdb', drawBorder: false}},
        x: {display: false, grid: {display: false}},
      },
    },
  });
}
function _createKUtilChart() {
  const ctx = document.getElementById('k-util-chart'); if (!ctx) return null;
  return new Chart(ctx.getContext('2d'), {
    type: 'line',
    data: {labels: [], datasets: [
      {label: 'GPU busy', data: [], borderColor: '#0d8b80', backgroundColor: '#0d8b801a', borderWidth: 2, fill: true, pointRadius: 0, tension: 0.3},
      {label: '同步等待 (launch-bound)', data: [], borderColor: '#c2660d', backgroundColor: 'transparent', borderWidth: 2, fill: false, pointRadius: 0, tension: 0.3},
    ]},
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: {mode: 'index', intersect: false},
      plugins: {legend: {display: true, position: 'top', align: 'end', labels: {font: {size: 10}, boxWidth: 10, color: '#7a6e63'}},
        tooltip: {..._kTip, callbacks: {label: c => `${c.dataset.label}: ${c.parsed.y == null ? '—' : c.parsed.y.toFixed(0) + '%'}`}}},
      scales: {
        y: {min: 0, max: 100, ticks: {color: '#a8998a', font: {size: 10}, maxTicksLimit: 5, callback: v => v + '%'}, grid: {color: '#f3ebdb', drawBorder: false}},
        x: {display: false, grid: {display: false}},
      },
    },
  });
}
function _updateKernelTrends(data) {
  if (!data || !data.series) return;
  // 懒创建:canvas 在 x-if="kernels.enabled" 里,init() 时还不存在,数据到了才建
  if (!_kClassChart) _kClassChart = _createKClassChart();
  if (!_kUtilChart) _kUtilChart = _createKUtilChart();
  const s = data.series, now = data.now_ns;
  const base = (s.gpu_busy && s.gpu_busy.length) ? s.gpu_busy : (s.gemm || []);
  const labels = base.map(p => '-' + Math.round((now - p.t) / 1e9) + 's');
  if (_kClassChart) {
    _kClassChart.data.labels = labels;
    _KCLASSES.forEach(([cls], i) => { _kClassChart.data.datasets[i].data = (s[cls] || []).map(p => p.v); });
    _kClassChart.resize(); _kClassChart.update('none');
  }
  if (_kUtilChart) {
    _kUtilChart.data.labels = labels;
    _kUtilChart.data.datasets[0].data = (s.gpu_busy || []).map(p => p.v);
    _kUtilChart.data.datasets[1].data = (s.sync || []).map(p => p.v);
    _kUtilChart.resize(); _kUtilChart.update('none');
  }
}

function benchTab() {
  return {
    form: {
      name: '',
      endpoint: 'http://localhost:8000',
      model: '',
      prompt_tokens: 500,
      output_tokens: 100,
      concurrency: 16,
      boundedBy: 'duration',
      duration_s: 60,
      num_requests: 100,
      warmup_s: 5,
      timeout_s: 30,
      api: 'chat',
      sloRows: [],   // array of {metric, percentile, op, value, unit}
      prompt_source: 'synthetic',
    },
    promptSources: [
      { value: 'synthetic', label: '合成填充 (synthetic)', uses_prompt_tokens: true,
        description: '按 prompt_tokens 长度循环 the quick brown fox 句模板' },
    ],
    runs: [],
    status: { running: [] },
    nowNs: 0,
    submitting: false,
    selectedId: null,
    _timer: null,

    async init() {
      // Prefill endpoint + model from /api/system so user doesn't have to retype
      // what dashboard already knows. We don't overwrite values the user has
      // already entered (e.g. if they're switching to a different endpoint).
      try {
        const sys = await fetch('/api/system').then(r => r.json());
        // Prefer served_model_name — that's what clients put into the OpenAI
        // request body. `sys.model` is the disk path / HF id vllm was launched
        // with, which is wrong here.
        if (!this.form.model && (sys.served_model_name || sys.model)) {
          this.form.model = sys.served_model_name || sys.model;
        }
        // Endpoint heuristic: vLLM usually listens on :8000 on the same host
        // as the dashboard. Use the page's hostname so this works across
        // localhost / WSL forwarded / remote dashboard access.
        if (this.form.endpoint === 'http://localhost:8000') {
          this.form.endpoint = `http://${window.location.hostname}:8000`;
        }
      } catch (e) {
        console.warn('[bench] prefill from /api/system failed:', e);
      }
      // Discover builtin prompt datasets — populates the dropdown below
      // the synthetic default. Fails open: dropdown still has 'synthetic'.
      try {
        const r = await fetch('/api/bench/prompt-sources').then(r => r.json());
        if (r.sources && r.sources.length) {
          this.promptSources = r.sources;
        }
      } catch (e) {
        console.warn('[bench] prompt-sources discovery failed:', e);
      }
      await this.refresh();
      this._timer = setInterval(() => this.refresh(), 3000);
    },

    onPromptSourceChange() {
      // No-op for now — hook reserved for clearing prompt_tokens when not used,
      // or for showing a preview of dataset prompts.
    },

    currentPromptSourceDescription() {
      const src = (this.promptSources || []).find(s => s.value === this.form.prompt_source);
      return src ? src.description : '';
    },

    currentPromptSourceUsesPromptTokens() {
      const src = (this.promptSources || []).find(s => s.value === this.form.prompt_source);
      return src ? !!src.uses_prompt_tokens : true;
    },

    async refresh() {
      try {
        const [runsR, statusR] = await Promise.all([
          fetch('/api/bench/runs?limit=50').then(r => r.json()),
          fetch('/api/bench/status').then(r => r.json()),
        ]);
        this.runs = runsR.runs || [];
        this.nowNs = runsR.now_ns || 0;
        this.status = statusR;
      } catch (e) {
        console.warn('[bench] refresh failed:', e);
      }
    },

    agoText(ns) {
      if (!ns || !this.nowNs) return '—';
      const sec = Math.max(0, (this.nowNs - ns) / 1e9);
      if (sec < 60) return `${sec.toFixed(0)} 秒前`;
      if (sec < 3600) return `${(sec / 60).toFixed(0)} 分钟前`;
      if (sec < 86400) return `${(sec / 3600).toFixed(1)} 小时前`;
      return `${(sec / 86400).toFixed(1)} 天前`;
    },

    fmtMs(v) {
      if (v == null || isNaN(v)) return '—';
      return `${Number(v).toFixed(0)} ms`;
    },

    fmtTps(v) {
      if (v == null || isNaN(v)) return '—';
      return `${Number(v).toFixed(0)} tok/s`;
    },

    toggle(id) {
      this.selectedId = (this.selectedId === id) ? null : id;
    },

    // ===== SLO row builder =====
    addSloRow() {
      this.form.sloRows.push({
        metric: 'ttft', percentile: 'p99', op: '<', value: 500, unit: 'ms',
      });
    },
    removeSloRow(i) {
      this.form.sloRows.splice(i, 1);
    },
    onSloMetricChange(i) {
      // error_rate has no percentile / unit; clamp sensible defaults
      const row = this.form.sloRows[i];
      if (row.metric === 'error_rate') {
        if (row.value > 1) row.value = 0.01;
      } else if (row.unit !== 'ms' && row.unit !== 's') {
        row.unit = 'ms';
      }
    },
    buildSloSpec() {
      const parts = [];
      for (const r of this.form.sloRows) {
        if (r.value == null || r.value === '' || isNaN(r.value)) continue;
        if (r.metric === 'error_rate') {
          parts.push(`${r.metric}${r.op}${r.value}`);
        } else {
          parts.push(`${r.metric}:${r.percentile}${r.op}${r.value}${r.unit}`);
        }
      }
      return parts.join(';');
    },

    async submit() {
      if (this.submitting) return;
      if (!this.form.endpoint || !this.form.model) return;
      this.submitting = true;
      try {
        const payload = {
          name: this.form.name || null,
          endpoint: this.form.endpoint,
          model: this.form.model,
          prompt_tokens: this.form.prompt_tokens,
          output_tokens: this.form.output_tokens,
          concurrency: this.form.concurrency,
          warmup_s: this.form.warmup_s,
          timeout_s: this.form.timeout_s,
          api: this.form.api,
          slo: this.form.sloRows.length > 0 ? this.buildSloSpec() : null,
          prompt_source: this.form.prompt_source || 'synthetic',
        };
        if (this.form.boundedBy === 'duration') {
          payload.duration_s = this.form.duration_s;
          payload.num_requests = null;
        } else {
          payload.num_requests = this.form.num_requests;
          payload.duration_s = null;
        }
        const r = await fetch('/api/bench/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        if (!r.ok) {
          const err = await r.json().catch(() => ({ detail: r.statusText }));
          alert(`提交失败: ${err.detail || r.status}`);
          return;
        }
        await this.refresh();
      } catch (e) {
        alert(`错误: ${e}`);
      } finally {
        this.submitting = false;
      }
    },
  };
}

function rulesTab() {
  return {
    rules: [],
    availableMetrics: [],
    editing: null,
    editingExisting: false,
    testResult: {},
    toast: '',
    toastError: false,

    async init() {
      await Promise.all([this.loadRules(), this.loadMetrics()]);
    },
    ruleName(rule_id) {
      const r = this.rules.find(x => x.id === rule_id);
      return r ? r.name : rule_id;
    },
    ruleCategory(rule_id) {
      const r = this.rules.find(x => x.id === rule_id);
      return r ? r.category : '';
    },
    async loadRules() {
      const r = await fetch('/api/rules').then(r => r.json());
      this.rules = r.rules || [];
    },
    async loadMetrics() {
      const r = await fetch('/api/metrics/available').then(r => r.json());
      this.availableMetrics = r.metrics || [];
    },
    blank() {
      return {
        id: '', name: '', severity: 'warning', category: 'throughput',
        condition: {
          metric: this.availableMetrics[0] || 'gpu.utilization_pct',
          op: '<', threshold: 50, window_seconds: 30, aggregation: 'avg',
        },
        message: '', suggestion: '', enabled: true,
      };
    },
    newRule() { this.editing = this.blank(); this.editingExisting = false; },
    editRule(r) { this.editing = JSON.parse(JSON.stringify(r)); this.editingExisting = true; },
    cancelEdit() { this.editing = null; },
    async save() {
      try {
        const method = this.editingExisting ? 'PUT' : 'POST';
        const url = this.editingExisting ? `/api/rules/${this.editing.id}` : '/api/rules';
        const r = await fetch(url, {
          method, headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(this.editing),
        });
        if (!r.ok) {
          const err = await r.json().catch(() => ({detail: r.statusText}));
          this.showToast(`保存失败: ${err.detail || r.status}`, true);
          return;
        }
        this.showToast(this.editingExisting ? '已更新' : '已创建');
        this.editing = null;
        await this.loadRules();
      } catch (e) {
        this.showToast(`错误: ${e}`, true);
      }
    },
    async deleteRule(r) {
      const what = r.is_default ? `禁用默认规则 ${r.id}（仍保留可重启用）` : `删除规则 ${r.id}`;
      if (!confirm(what + '？')) return;
      const resp = await fetch(`/api/rules/${r.id}`, {method: 'DELETE'});
      if (!resp.ok) { this.showToast(`删除失败: ${resp.status}`, true); return; }
      this.showToast(r.is_default ? '已禁用' : '已删除');
      await this.loadRules();
    },
    async testRule(rule_id) {
      const r = await fetch(`/api/rules/${rule_id}/test`, {method: 'POST'});
      if (!r.ok) { this.showToast(`测试失败: ${r.status}`, true); return; }
      this.testResult[rule_id] = await r.json();
    },
    formatTestResult(t) {
      if (!t.data_available) return `当前窗口无数据（${t.window_seconds}s, ${t.aggregation}）`;
      const verb = t.would_fire ? '会触发' : '不触发';
      return `${verb} — ${t.aggregation}(${t.metric}) = ${Number(t.value).toFixed(3)} (阈值 ${t.threshold})`;
    },
    showToast(msg, error = false) {
      this.toast = msg; this.toastError = error;
      setTimeout(() => { this.toast = ''; }, 3000);
    },
  };
}

function dashboard() {
  return {
    tab: 'live',
    health: {},
    system: {},
    kpis: {
      // New shape: ttft/tpot are {p50, p95, p99, avg, n} objects
      ttft: null, tpot: null, tpot_source: 'tpot',
      output_tps: null, per_req_decode_tps: null,
      // Flat aliases kept so older bench/report cards keep rendering
      ttft_p50_ms: null, ttft_p99_ms: null,
      tpot_p50_ms: null, tpot_p99_ms: null,
      kv_cache: null,
      running_reqs: null, waiting_reqs: null,
      mfu: null, prefix_cache_hit: null,
      padding_ratio: null, preempt_per_min: null,
      gpu_util: null, gpu_mem_used_pct: null, gpu_mem_bw_pct: null,
    },
    gpu_util_pct: 0,
    // CUPTI kernel 时间分解 (阶段 1a)
    kernels: {
      enabled: false, class_shares: [], top_kernels: [], findings: [],
      gpu_busy_pct: null, launch_count_per_s: null, mean_dur_us: null,
      in_graph_pct: null, memcpy_share_pct: null, sync_share_pct: null,
      overhead_cb_ms: null, dropped_total: null,
      snapshot_age_s: null, rollup_window_s: null,
    },
    // Deep Evidence(阶段 2 PC Sampling 按需取证):为什么这些 kernel 慢
    deep: { running: false, available_now: false, result: null, findings: [], error: null },
    kernelShowAll: false,        // Kernel 明细表:false=只显示前 N 行
    kernelCollapsed: 10,         // 收起时显示的行数
    kernelExpanded: null,        // 展开看 stall 构成 + 建议的行索引(null=都收起)
    stallExpanded: null,         // Deep Evidence:展开看某 stall 类的原始 PerfWorks reason 名
    timeline: null,              // 执行时间线(最近 N 条 kernel 的 start/end/stream)
    tlFrozen: false,             // 冻结:停 2s 刷新,便于缩放/读
    tlPxPerMs: null,             // 时间线缩放:每毫秒像素;null=适应容器宽度
    tlSelIdx: null,              // 选中块索引(高亮)
    tlSelMs: null,               // 选中块中心时间(ms),缩放锚点
    tlSelName: '',               // 选中块名(显示)
    tpotSource: 'tpot',
    rooflineSource: 'measured',
    rooflineFormula: '',
    rooflineParamsB: '0',
    // Verdict card — populated each refresh from the recent points
    rooflineVerdict: null,    // {bound, computeUtil, bwUtil, knee, suggestions[]}
    rooflineScale: null,      // 调优指引:{ai, cur, t32, gain, knee}(decode 强度≈并发 → 扩并发能到哪)
    ttftHasData: false,
    tpotHasData: false,
    e2eHasData: false,
    e2eP99: null,
    diagnoses: [],
    benchRunning: 0,
    showStartupInfo: false,
    cmdlineCopyLabel: '复制',

    fmt(v, digits) {
      if (v == null || isNaN(v)) return '—';
      return Number(v).toFixed(digits);
    },

    // kernel 语义类 → 颜色 / 中文标签(分段条 + 图例)
    kernelColor(cls) {
      return {
        attention: '#0d8b80', gemm: '#5147c8', norm: '#c2660d',
        rotary: '#be1556', activation: '#5a8f1f', comm: '#dc4d3e',
        elementwise: '#3f7fa8', sampling: '#9b59b6', index: '#b8860b',
        memcpy: '#7a6e63', other: '#a8998a',
      }[cls] || '#a8998a';
    },
    kernelLabel(cls) {
      return {
        attention: 'Attention', gemm: 'GEMM', norm: 'Norm',
        rotary: 'Rotary', activation: 'Activation', comm: 'Comm (NCCL)',
        elementwise: 'Elementwise', sampling: 'Sampling', index: 'Index/Gather',
        other: 'Other',
      }[cls] || cls;
    },
    // === Kernel tab 诊断辅助(全部从 deep.result 现有数据推导,无需后端)===
    // #5 mangled 名 → 人话
    kernelFriendly(name, cls) {
      const n = (name || '').toLowerCase();
      const has = (...xs) => xs.some(x => n.includes(x));
      if (cls === 'gemm') {
        if (has('cutlass') && has('wmma')) return 'GEMM · cutlass WMMA TensorOp';
        if (has('cutlass')) return 'GEMM · cutlass TensorOp';
        if (has('splitkreduce')) return 'GEMM · cuBLAS splitK reduce';
        if (has('cublas')) return 'GEMM · cuBLAS';
        return 'GEMM (matmul)';
      }
      if (cls === 'attention') {
        if (has('splitkv')) return 'Attention · FlashAttention (split-KV)';
        if (has('flash')) return 'Attention · FlashAttention';
        if (has('reshape_and_cache')) return 'Attention · KV-cache write';
        if (has('paged')) return 'Attention · PagedAttention';
        return 'Attention';
      }
      if (cls === 'norm') {
        if (has('fused_add_rms')) return 'Norm · fused add + RMSNorm';
        if (has('rms')) return 'Norm · RMSNorm';
        if (has('layernorm') || has('layer_norm')) return 'Norm · LayerNorm';
        return 'Norm';
      }
      if (cls === 'rotary') return 'RoPE (rotary embedding)';
      if (cls === 'activation') {
        if (has('act_and_mul') || has('silu')) return 'Activation · SiLU×Mul';
        if (has('gelu')) return 'Activation · GELU';
        return 'Activation';
      }
      if (cls === 'sampling') {
        if (has('softmax')) return 'Sampling · Softmax';
        if (has('argmax')) return 'Sampling · ArgMax (greedy)';
        if (has('exponential') || has('distribution')) return 'Sampling · random sample';
        if (has('topk') || has('top_k')) return 'Sampling · Top-K';
        return 'Sampling';
      }
      if (cls === 'index') {
        if (has('gather')) return 'Index · gather';
        if (has('index')) return 'Index · indexSelect';
        return 'Index/Gather';
      }
      if (cls === 'elementwise') {
        if (has('direct_copy') || has('copy')) return 'Elementwise · copy/cast';
        if (has('div')) return 'Elementwise · div';
        if (has('add')) return 'Elementwise · add';
        if (has('mul')) return 'Elementwise · mul';
        return 'Elementwise';
      }
      if (cls === 'comm') return 'Comm (NCCL)';
      return this.kernelLabel(cls);
    },
    // #3 这个 kernel 浪费的"全局 GPU 时间"= 时间占比 × 它内部 stall 比例
    kernelStallTimePct(k) {
      if (!k || !k.samples) return 0;
      return (k.time_pct || 0) * (k.stall_samples || 0) / k.samples;
    },
    // #1 GPU 在干活 vs 在等(issued = 真正发指令的样本占比)
    issuedVerdict() {
      const r = this.deep.result;
      if (!r || !r.available) return null;
      const issued = r.issued_pct || 0;
      const stall = Math.max(0, 100 - issued);
      return { issued, stall, level: stall >= 70 ? 'high' : (stall >= 45 ? 'mid' : 'low') };
    },
    // #2 访存 / 算力 / 延迟 瓶颈判定(取 stall_shares 头部,排除非瓶颈项)
    bottleneckVerdict() {
      const r = this.deep.result;
      const sh = (r && r.stall_shares) || [];
      const top = sh.filter(s => !['scheduler_slack', 'issued'].includes(s.cls))
                    .slice().sort((a, b) => b.pct - a.pct)[0];
      if (!top) return null;
      const map = {
        memory_dependency: { t: '访存瓶颈', a: '数据在等内存加载。可试 fp8/int8 量化减少访存、算子融合减少往返、确认 KV cache 复用。' },
        memory_throttle: { t: '访存带宽瓶颈', a: '内存子系统被打满。降低精度 / 融合算子减少访存流量。' },
        math_pipe: { t: '算力瓶颈', a: '计算单元接近饱和(好事,已高效)。再压只能靠更低精度或更优 kernel。' },
        exec_dependency: { t: '指令延迟瓶颈', a: '指令间数据依赖等待,多由 kernel 内部结构决定,优化空间有限。' },
        shared_dependency: { t: '共享内存瓶颈', a: '等共享内存 / L1。检查 tile 大小与 bank conflict。' },
        sync: { t: '同步瓶颈', a: '线程在 barrier 等待。检查同步频率与负载均衡。' },
        fetch_control: { t: '前端取指瓶颈', a: '指令获取 / 分支,一般非主因。' },
        dispatch: { t: '发射瓶颈', a: '发射端口受限。' },
      };
      const m = map[top.cls] || { t: this.stallLabel(top.cls) + ' 为主', a: '' };
      return { cls: top.cls, pct: top.pct, type: m.t, action: m.a };
    },
    // #3 全局最大可回收点:stall 时间占比最高的 kernel
    topRecoverable() {
      const kt = (this.deep.result && this.deep.result.kernel_table) || [];
      let best = null, bestv = 0;
      for (const k of kt) {
        const v = this.kernelStallTimePct(k);
        if (v > bestv) { bestv = v; best = k; }
      }
      return best ? { k: best, pct: bestv } : null;
    },
    // #6 单个 kernel 的优化建议
    kernelSuggestion(k) {
      if (!k) return '';
      const ds = k.dominant_stall, c = k.cls;
      if (c === 'gemm' && (ds === 'memory_dependency' || ds === 'memory_throttle'))
        return '访存瓶颈的矩阵乘:fp8/int8 量化、增大 batch 提升计算密度、检查权重是否反复从显存读取。';
      if (c === 'gemm' && ds === 'math_pipe')
        return '矩阵乘已算力饱和(接近峰值),难再压;考虑更低精度。';
      if (c === 'attention' && (ds === 'memory_dependency' || ds === 'memory_throttle'))
        return '注意力访存瓶颈:确认 FlashAttention / PagedAttention 生效、KV cache 命中率。';
      if (c === 'elementwise')
        return '逐元素 / 拷贝:看能否算子融合,减少 kernel 数与显存往返。';
      if (c === 'sampling')
        return '采样 / 解码开销:批量解码、减少不必要的 host-device 往返。';
      if (c === 'index')
        return '索引 / 查表:确认访问模式连续,避免随机 gather 打散访存。';
      if (ds === 'exec_dependency')
        return '指令延迟为主,通常由 kernel 内部结构决定,优化空间有限。';
      return '';
    },
    // P3 行级归因:取该 kernel 的"最深热点"(源码行 / SASS 偏移)。按 .so 原始 functionName 精确匹配
    kernelHotspot(k) {
      if (!k) return null;
      const hs = (this.deep.result && this.deep.result.pc_hotspots) || [];
      return hs.find(h => h.kernel === k.kernel) || null;
    },
    // P3:所有"能定位到 Python 源码行"的 kernel(差异化能力,单独提到顶部,免得埋在长表里)
    sourceHotspots() {
      const hs = (this.deep.result && this.deep.result.pc_hotspots) || [];
      return hs.filter(h => h.mappable && h.lines && h.lines.length);
    },
    // 这些可映射 kernel 合计占多少 GPU 时间(诚实标注:小模型上往往很小,主导在闭源 GEMM)
    sourceHotspotsTimePct() {
      const kt = (this.deep.result && this.deep.result.kernel_table) || [];
      const names = new Set(this.sourceHotspots().map(h => h.kernel));
      let sum = 0;
      for (const k of kt) if (names.has(k.kernel)) sum += (k.time_pct || 0);
      return sum;
    },
    // === Deep Evidence(全局 / warp 效率 / 方法论)辅助 ===
    // Warp 周期三态(占全部样本):发指令 / 就绪未选中(余量) / 真 stall(在等)
    warpSplit() {
      const r = this.deep.result;
      if (!r || !r.available) return null;
      const issued = r.issued_pct || 0;
      const slackShare = ((r.stall_shares || []).find(s => s.cls === 'scheduler_slack') || {}).pct || 0;
      const slack = slackShare * (100 - issued) / 100;   // slack 是"占 stall",换算回占全部
      const stall = Math.max(0, 100 - issued - slack);
      return { issued, slack, stall };
    },
    // 每个 stall 语义类一句话含义
    stallMeaning(cls) {
      return {
        memory_dependency: '等全局/本地内存的数据返回(long scoreboard)',
        shared_dependency: '等共享内存 / L1(short scoreboard)',
        memory_throttle: '访存指令排队、内存子系统被打满',
        math_pipe: '计算管线忙(Tensor / ALU / FMA),接近算力上限',
        exec_dependency: '等前一条指令的结果(指令间依赖)',
        sync: '在 barrier / membar 等其他线程',
        fetch_control: '等取指 / 分支决议',
        dispatch: '发射端口受限',
        scheduler_slack: '有就绪 warp 但本周期没被选中(占用率有余量,非瓶颈)',
        other: '其它 / 杂项',
      }[cls] || '';
    },
    // 原始 PerfWorks reason 名:去掉公共前缀,留语义后缀(给专家看真实指标名)
    prettyReason(raw) {
      if (!raw) return '';
      return raw
        .replace(/^smsp__pcsamp_warps_issue_stalled_/, '')
        .replace(/^smsp__pcsamp_warps_issue_/, '')
        .replace(/^smsp__pcsamp_/, '')
        .replace(/^smsp__/, '');
    },

    // stall 语义类 → 中文标签 / 颜色(Deep Evidence 分解条)
    stallLabel(cls) {
      return {
        memory_dependency: '访存依赖', shared_dependency: 'shared/MIO 依赖',
        memory_throttle: '访存子系统压力', math_pipe: '计算管线',
        exec_dependency: '执行依赖', sync: '同步', fetch_control: '取指/控制流',
        dispatch: '调度分发', scheduler_slack: '调度余量(非瓶颈)', other: '其它',
      }[cls] || cls;
    },
    stallColor(cls) {
      return {
        memory_dependency: '#5147c8', shared_dependency: '#0d8b80',
        memory_throttle: '#7a5cc8', math_pipe: '#c2660d', exec_dependency: '#be1556',
        sync: '#dc4d3e', fetch_control: '#5a8f1f', dispatch: '#9a8f1f',
        scheduler_slack: '#9bb04f', other: '#a8998a',
      }[cls] || '#a8998a';
    },
    // 打开 Kernel tab 时调:先拉缓存结果;若可用且还没有结果,自动跑一次取证 ——
    // 免得用户找不到/不点"采集 stall 证据"按钮就以为 tab 空的(§A)。
    async onKernelTabOpen() {
      this._ensureKernelRoofline();
      await this.loadDeepEvidence();
      if (this.deep.available_now && !this.deep.result && !this.deep.running) {
        this.runDeepEvidence(5);
      }
    },
    // 懒建 Kernel tab 里的第二个 roofline 图(canvas 在 x-show 容器内,tab 显示后才有尺寸)
    _ensureKernelRoofline() {
      setTimeout(() => {
        const el = document.getElementById('kernel-roofline-chart');
        if (!el) return;
        if (_kRoofChart) { _kRoofChart.resize(); return; }
        _kRoofChart = _makeRooflineChart(el.getContext('2d'));
        if (_lastRoofline) _applyRooflineData(_kRoofChart, _lastRoofline);
      }, 60);
    },
    // 读最近一次取证结果(开 Kernel tab 时调,不触发新采集)
    async loadDeepEvidence() {
      try {
        const r = await fetch('/api/kernels/deep_evidence').then(x => x.json());
        this.deep.available_now = !!r.available_now;
        if (r.last) { this.deep.result = r.last; this.deep.findings = r.findings || []; }
      } catch (e) { /* fail-closed:静默 */ }
    },
    // 触发一个取证短窗(阻塞 ~window 秒)
    async runDeepEvidence(window) {
      if (this.deep.running) return;
      this.deep.running = true; this.deep.error = null;
      try {
        const r = await fetch(`/api/kernels/deep_evidence?window=${window || 5}`,
          { method: 'POST' }).then(x => x.json());
        if (r.available) {
          this.deep.result = r; this.deep.findings = r.findings || []; this.deep.available_now = true;
        } else {
          this.deep.error = r.error || 'PC Sampling 不可用'; this.deep.available_now = false;
        }
      } catch (e) {
        this.deep.error = '请求失败:' + e;
      } finally {
        this.deep.running = false;
      }
    },
    // kernel 数据是否"实时"(采集时刻够近),用于新鲜度横幅
    // 延迟分位分布条:返回 [0..p50, p50..p95, p95..p99] 三段占 p99 的宽度%
    pctSeg(d) {
      if (!d || !d.p99 || d.p99 <= 0) return [0, 0, 0];
      const w1 = 100 * d.p50 / d.p99;
      const w2 = 100 * (d.p95 - d.p50) / d.p99;
      const w3 = 100 * (d.p99 - d.p95) / d.p99;
      return [Math.max(0, w1), Math.max(0, w2), Math.max(0, w3)];
    },
    kernelFresh() {
      const a = this.kernels.snapshot_age_s;
      if (a == null) return true;  // 无 collector 信息时不显示过期
      const w = this.kernels.rollup_window_s || 1;
      return a <= Math.max(3, w * 2.5);
    },
    kernelAgeText() {
      const a = this.kernels.snapshot_age_s;
      if (a == null) return '';
      if (a < 1.5) return '刚刚';
      if (a < 90) return Math.round(a) + ' 秒前';
      return Math.round(a / 60) + ' 分钟前';
    },
    // 执行时间线:px-based,横向滚动 + 缩放按钮。放大=每毫秒像素翻倍(内层变宽),
    // 平移=容器原生横向滚动。tlPxPerMs=每毫秒像素;null=适应容器宽度。
    _tlFitPx(spanMs) {
      const el = document.getElementById('tl-scroll');
      const w = (el && el.clientWidth) ? el.clientWidth : 900;
      return Math.max(2, (w - 3) / Math.max(0.001, spanMs));  // 留 3px 余量,免最小块 1px 溢出触发滚动条
    },
    timelineView() {
      const tl = this.timeline;
      if (!tl || !tl.events || !tl.events.length) return null;
      const spanMs = (tl.span_ns || 1) / 1e6;
      const pxPerMs = this.tlPxPerMs || this._tlFitPx(spanMs);
      const rowOf = {}; tl.streams.forEach((st, i) => { rowOf[st] = i; });
      const blocks = tl.events.map((e, idx) => ({
        idx, row: rowOf[e.stream], stream: e.stream,
        leftPx: e.start / 1e6 * pxPerMs,
        widthPx: Math.max(1, e.dur / 1e6 * pxPerMs),
        centerMs: (e.start + e.dur / 2) / 1e6, startMs: e.start / 1e6,
        cls: e.cls, name: e.name, durus: e.dur / 1000, ingraph: e.in_graph,
      }));
      return {
        blocks, streams: tl.streams, spanMs, total: tl.count,
        pxPerMs, innerPx: Math.round(spanMs * pxPerMs), zoomed: this.tlPxPerMs != null,
      };
    },
    // 缩放:以"选中块中心"为锚点(没选则以当前视口中心),缩放后调滚动位置让锚点居中。
    tlZoom(factor) {
      const v = this.timelineView(); if (!v) return;
      const old = v.pxPerMs;
      const next = Math.max(2, Math.min(40000, old * factor));
      const sc = document.getElementById('tl-scroll');
      let anchorMs = this.tlSelMs;
      if (anchorMs == null && sc) anchorMs = (sc.scrollLeft + sc.clientWidth / 2) / old;
      this.tlPxPerMs = next;
      this.$nextTick(() => {
        const s = document.getElementById('tl-scroll');
        if (s && anchorMs != null) s.scrollLeft = anchorMs * next - s.clientWidth / 2;
      });
    },
    tlFit() { this.tlPxPerMs = null; },
    tlToggleFreeze() { this.tlFrozen = !this.tlFrozen; },
    // 点块选中 = 设缩放锚点(并冻结,免得数据跳走);再点 ＋ 就围着它放大
    tlSelectBlock(b) {
      if (this.tlSelIdx === b.idx) { this.tlSelIdx = null; this.tlSelMs = null; this.tlSelName = ''; return; }
      this.tlSelIdx = b.idx; this.tlSelMs = b.centerMs; this.tlSelName = b.name;
      this.tlFrozen = true;
    },
    tlSelectedBlock() {
      if (this.tlSelIdx == null) return null;
      const v = this.timelineView(); if (!v) return null;
      return v.blocks.find(b => b.idx === this.tlSelIdx) || null;
    },
    // 导出 Chrome Trace JSON → 用 Perfetto / chrome://tracing 看(专业 trace 查看器)
    async downloadTrace() {
      try {
        const r = await fetch('/api/kernels/trace').then(x => x.json());
        if (!r.available) { alert('暂无 trace 数据(需 CUPTI 采集器在采集)'); return; }
        const blob = new Blob([JSON.stringify(r.trace)], {type: 'application/json'});
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = 'pping-kernels-trace.json'; a.click();
        URL.revokeObjectURL(url);
      } catch (e) { console.warn('[pping-lang] trace export failed:', e); }
    },

    // ===== Roofline verdict (plain-language interpretation) =====
    _computeRooflineVerdict(data) {
      const pts = data.points || [];
      const peak = data.peak;
      if (!pts.length || !peak || !peak.compute_tflops || !peak.mem_bw_tbs) {
        return null;
      }
      const median = arr => {
        const s = [...arr].sort((a, b) => a - b);
        return s[Math.floor(s.length / 2)];
      };
      const med_ai = median(pts.map(p => p.ai));
      const med_tput = median(pts.map(p => p.throughput_tflops));
      const knee = peak.compute_tflops / peak.mem_bw_tbs;       // op/byte
      const isMemBound = med_ai < knee;
      // Achievable throughput at this AI (the Roofline envelope value)
      const achievable = Math.min(peak.compute_tflops, med_ai * peak.mem_bw_tbs);
      const utilization = med_tput / achievable;                // 0..1
      // Compute & bandwidth utilizations separately (each vs its own roof)
      const computeUtil = med_tput / peak.compute_tflops;
      const usedBwTbs = med_tput / med_ai;                      // TFLOPS / (op/byte) = TB/s
      const bwUtil = usedBwTbs / peak.mem_bw_tbs;
      let bound, headline, suggestions;
      if (isMemBound) {
        bound = 'memory';
        headline = 'Memory-bound（LLM decode 阶段的常态）';
        suggestions = [
          '增大 batch 直到 KV cache 接近 80% — 摊薄权重 re-read',
          '启用 speculative decoding — 减少 decode 步数',
          '权重量化 (AWQ / GPTQ) — 直接减小要读的字节数',
          `升级带宽更高的卡（你当前 ${(peak.mem_bw_tbs * 1000).toFixed(0)} GB/s；H100 3.4 TB/s，H200 4.8 TB/s）`,
        ];
      } else {
        bound = 'compute';
        headline = 'Compute-bound（prefill 或大 batch 状态）';
        suggestions = [
          '继续增大 batch 收益递减 — 算力已接近上限',
          '升级算力更高的 GPU 或上 tensor parallel',
          'Chunked prefill — 拆开长 prompt 让 decode 喘息',
        ];
      }
      return {
        bound, headline, suggestions,
        medAI: med_ai, medTput: med_tput,
        achievable, utilization,
        computeUtil, bwUtil,
        knee, peakC: peak.compute_tflops, peakBW: peak.mem_bw_tbs,
        n: pts.length,
      };
    },

    init() {
      const ctx = document.getElementById('gpu-chart').getContext('2d');

      _chart = _makeRooflineChart(ctx);
      // Mini latency-trend charts (TTFT / TPOT / E2E)
      _ttftChart = _createMiniLatencyChart('ttft-chart', '#dc4d3e');
      _tpotChart = _createMiniLatencyChart('tpot-chart', '#5147c8');
      _e2eChart  = _createMiniLatencyChart('e2e-chart',  '#0d8b80');
      // kernel 趋势图懒创建(canvas 在 x-if 里,见 _updateKernelTrends)

      this.fetchSystem();
      this.refresh();
      setInterval(() => this.refresh(), 2000);
      // 打开 Kernel tab 自动取证(§A):进去就有真数据,不用手点按钮
      this.$watch('tab', (v) => { if (v === 'kernel') this.onKernelTabOpen(); });
    },

    updateRoofline(data) {
      if (!_chart) return;
      // Surface which path produced the points, plus the formula tooltip
      this.rooflineSource = data.data_source || 'measured';
      this.rooflineFormula = data.formula || '';
      this.rooflineParamsB = data.params_billion != null
        ? Number(data.params_billion).toFixed(2)
        : '?';
      // verdict(roofline 本身不直观,用中位 AI/吞吐 判定,免单点 outlier 翻转结论)
      this.rooflineVerdict = this._computeRooflineVerdict(data);
      _lastRoofline = data;
      // 调优指引(decode 强度≈并发):当前簇 → 并发32 的带宽上界 → 拐点
      this.rooflineScale = null;
      if (data.peak && data.peak.compute_tflops && data.peak.mem_bw_tbs && (data.points || []).length) {
        const peakC = data.peak.compute_tflops, peakBW = data.peak.mem_bw_tbs, knee = peakC / peakBW;
        const agg = _aggRooflinePoints(data.points.map(p => ({ x: p.ai, y: p.throughput_tflops })));
        const dec = agg.length ? agg.reduce((a, p) => (p.n > a.n ? p : a)) : null;
        if (dec && dec.y > 0 && dec.x < knee) {
          const t32 = Math.min(peakBW * 32, peakC);
          this.rooflineScale = {
            ai: dec.x, cur: dec.y, t32, gain: t32 / dec.y, knee: Math.round(knee),
            // 该 AI 下的带宽上界利用率(实测吞吐 / envelope 值)
            bwUtil: Math.min(100, 100 * dec.y / (peakBW * dec.x)),
          };
        }
      }
      _applyRooflineData(_chart, data);        // Overview 的图
      _applyRooflineData(_kRoofChart, data);   // Kernel tab 的图(懒建后才非空)
    },

    async fetchSystem() {
      try {
        this.system = await fetch('/api/system').then(r => r.json());
      } catch (e) {
        console.warn('[pping-lang] system info fetch failed:', e);
      }
    },

    async copyCmdline() {
      const text = (this.system.cmdline || []).join(' ');
      if (!text) return;
      try {
        await navigator.clipboard.writeText(text);
        this.cmdlineCopyLabel = '已复制 ✓';
      } catch (e) {
        this.cmdlineCopyLabel = '复制失败';
      }
      setTimeout(() => { this.cmdlineCopyLabel = '复制'; }, 1800);
    },

    async refresh() {
      try {
        const [healthR, kpisR, rooflineR, trendsR, diagR, benchStatusR, kernelsR, tlR, kTrendsR] = await Promise.all([
          fetch('/api/health').then(r => r.json()),
          fetch('/api/kpis?window=60').then(r => r.json()),
          fetch('/api/roofline?seconds=60').then(r => r.json()),
          fetch('/api/latency_trends?seconds=300&buckets=30').then(r => r.json()),
          fetch('/api/diagnoses?seconds=300').then(r => r.json()),
          fetch('/api/bench/status').then(r => r.json()).catch(() => ({running: []})),
          fetch('/api/kernels?window=60').then(r => r.json()).catch(() => ({enabled: false, class_shares: []})),
          fetch('/api/kernels/timeline?max_events=800').then(r => r.json()).catch(() => ({timeline: null})),
          fetch('/api/kernels/trends?seconds=180').then(r => r.json()).catch(() => ({series: null})),
        ]);
        this.benchRunning = (benchStatusR.running || []).length;
        this.health = healthR;
        this.kpis = kpisR.kpis || this.kpis;
        this.gpu_util_pct = this.kpis.gpu_util ?? 0;
        this.kernels = kernelsR;
        if (!this.tlFrozen) this.timeline = tlR.timeline;   // 冻结时不覆盖,保持可缩放
        _updateKernelTrends(kTrendsR);

        const seen = new Set();
        this.diagnoses = (diagR.diagnoses || []).filter(d => {
          if (seen.has(d.rule_id)) return false;
          seen.add(d.rule_id);
          return true;
        });

        this.updateRoofline(rooflineR);
        this.updateLatencyTrends(trendsR);
      } catch (e) {
        console.warn('[pping-lang] refresh failed:', e);
      }
    },

    updateLatencyTrends(data) {
      const ttft = data.ttft_ms || [];
      const tpot = data.tpot_ms || [];
      const e2e  = data.e2e_ms || [];
      this.ttftHasData = ttft.length > 0;
      this.tpotHasData = tpot.length > 0;
      this.e2eHasData  = e2e.length > 0;
      this.tpotSource  = data.tpot_source || 'tpot';
      this.e2eP99 = this.e2eHasData ? e2e[e2e.length - 1].p99 : null;
      _updateMiniLatencyChart(_ttftChart, ttft);
      _updateMiniLatencyChart(_tpotChart, tpot);
      _updateMiniLatencyChart(_e2eChart, e2e);
    },
  };
}
