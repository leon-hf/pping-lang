let _chart = null;
let _ttftChart = null;
let _tpotChart = null;
let _e2eChart = null;
let _kClassChart = null;   // kernel 类占比堆叠面积(实时)
let _kUtilChart = null;    // GPU busy + 同步等待(实时)

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
        memcpy: '#7a6e63', other: '#a8998a',
      }[cls] || '#a8998a';
    },
    kernelLabel(cls) {
      return {
        attention: 'Attention', gemm: 'GEMM', norm: 'Norm',
        rotary: 'Rotary', activation: 'Activation', comm: '通信 (NCCL)',
        other: '其它',
      }[cls] || cls;
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

      _chart = new Chart(ctx, {
        type: 'scatter',
        data: {
          datasets: [
            {
              label: '当前样本',
              data: [],
              backgroundColor: 'rgba(13, 139, 128, 0.55)',
              borderColor: '#0d8b80',
              borderWidth: 1,
              pointRadius: 4,
              pointHoverRadius: 7,
              pointHoverBackgroundColor: '#0d8b80',
              pointHoverBorderColor: '#fff',
              pointHoverBorderWidth: 2,
              showLine: false,
              order: 3,
            },
            {
              // Compute roof: horizontal line + fill the compute-bound zone
              // (right of knee). Soft coral wash hints "this is the compute
              // wall, points here are bounded by FLOPS not bandwidth."
              label: 'Compute roof',
              data: [],
              showLine: true,
              borderColor: '#dc4d3e',
              borderWidth: 2.5,
              pointRadius: 0,
              fill: 'origin',
              backgroundColor: 'rgba(220, 77, 62, 0.06)',
              tension: 0,
              order: 1,
            },
            {
              // Memory roof: diagonal + fill memory-bound zone (left of knee).
              // Soft indigo wash hints "you're stuck on bandwidth here."
              label: 'Memory roof',
              data: [],
              showLine: true,
              borderColor: '#5147c8',
              borderWidth: 2.5,
              pointRadius: 0,
              fill: 'origin',
              backgroundColor: 'rgba(81, 71, 200, 0.06)',
              tension: 0,
              order: 2,
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
              padding: 11,
              cornerRadius: 8,
              displayColors: false,
              borderWidth: 0,
              titleFont: { weight: '600' },
              callbacks: {
                title: () => '',
                label: (ctx) => {
                  const ds = ctx.dataset.label;
                  if (ds === '当前样本') {
                    return [
                      `AI:  ${ctx.parsed.x.toFixed(2)} FLOPs/byte`,
                      `TPut: ${ctx.parsed.y.toFixed(1)} TFLOPs/s`,
                    ];
                  }
                  return `${ds}: ${ctx.parsed.y.toFixed(1)} TFLOPs/s`;
                },
              },
            },
          },
          scales: {
            x: {
              type: 'logarithmic',
              title: {
                display: true,
                text: 'Arithmetic Intensity (FLOPs / byte)',
                color: '#7a6e63',
                font: { size: 11.5, weight: '600' },
              },
              ticks: { color: '#a8998a', font: { size: 11 } },
              grid: { color: '#f3ebdb', drawBorder: false },
            },
            y: {
              type: 'logarithmic',
              title: {
                display: true,
                text: 'Achieved Throughput (TFLOPs/s)',
                color: '#7a6e63',
                font: { size: 11.5, weight: '600' },
              },
              ticks: { color: '#a8998a', font: { size: 11 } },
              grid: { color: '#f3ebdb', drawBorder: false },
            },
          },
        },
      });
      // Mini latency-trend charts (TTFT / TPOT / E2E)
      _ttftChart = _createMiniLatencyChart('ttft-chart', '#dc4d3e');
      _tpotChart = _createMiniLatencyChart('tpot-chart', '#5147c8');
      _e2eChart  = _createMiniLatencyChart('e2e-chart',  '#0d8b80');
      // kernel 趋势图懒创建(canvas 在 x-if 里,见 _updateKernelTrends)

      this.fetchSystem();
      this.refresh();
      setInterval(() => this.refresh(), 2000);
    },

    updateRoofline(data) {
      if (!_chart) return;
      // Surface which path produced the points, plus the formula tooltip
      this.rooflineSource = data.data_source || 'measured';
      this.rooflineFormula = data.formula || '';
      this.rooflineParamsB = data.params_billion != null
        ? Number(data.params_billion).toFixed(2)
        : '?';
      // Sample points
      const points = (data.points || []).map(p => ({
        x: p.ai,
        y: p.throughput_tflops,
      }));
      _chart.data.datasets[0].data = points;
      // Plain-language verdict — Roofline is unintuitive on its own. We
      // pick the median AI / throughput of recent points so a single
      // outlier doesn't flip the bound classification.
      this.rooflineVerdict = this._computeRooflineVerdict(data);

      // Roof lines from peak info
      if (data.peak && data.peak.compute_tflops && data.peak.mem_bw_tbs) {
        const peakC = data.peak.compute_tflops;
        const peakBW = data.peak.mem_bw_tbs;
        const knee = peakC / peakBW;
        const xMin = 0.1;
        const xMax = Math.max(1000, knee * 3);
        // Compute roof: horizontal from knee to xMax
        _chart.data.datasets[1].data = [
          { x: knee, y: peakC },
          { x: xMax, y: peakC },
        ];
        // Memory roof: diagonal from (xMin, bw*xMin) to knee
        _chart.data.datasets[2].data = [
          { x: xMin, y: peakBW * xMin },
          { x: knee, y: peakC },
        ];
      } else {
        _chart.data.datasets[1].data = [];
        _chart.data.datasets[2].data = [];
      }
      _chart.update('none');
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
