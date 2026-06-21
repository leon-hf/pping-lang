// ===== i18n(中/英;框架支持多语,加语言只需补字典 + 一个 lang-toggle 按钮)=====
// 全局 t(key):x-text="t('k')" / :title="t('k')" 处处可用(dashboard/rulesTab/benchTab 同享)。
// 语言存 Alpine.store('i18n').lang(响应式,切换即时重渲染)+ localStorage 持久化。
const I18N = {
  zh: {
    'nav.live': '实时', 'nav.kernel': 'Kernel', 'nav.rules': '规则', 'nav.bench': '压测',
    'brand.sub': 'vLLM 性能诊断',
    'btn.issue': '提 Issue', 'btn.star': 'Star',
    'hero.info': '查看 vLLM 启动命令、环境变量、解析配置',
    'common.save': '保存', 'common.saving': '保存中…', 'common.reset': '还原',
    'common.suggestion': '建议', 'common.inference': '推断', 'common.edit': '编辑', 'common.delete': '删除',
    'cfg.form': '业务形态', 'cfg.advanced': '高级阈值',
    'cfg.hintActive': '改一处,引用它的策展规则全跟着变 · 保存即热生效,无需重启',
    'cfg.hintInactive': '引擎未运行,保存仅校验,重启后生效',
    'cfg.sla_ttft': 'TTFT p99 SLA', 'cfg.sla_tpot': 'TPOT p99 SLA',
    'cfg.long_prompt': '长 prompt 阈值', 'cfg.waiting': '等待队列阈值',
    'cfg.mbu_high': 'MBU 贴顶阈值', 'cfg.mbu_low': 'MBU 偏低阈值',
    'cfg.batch_small': '并发偏小阈值', 'cfg.mfu_low': 'MFU 偏低阈值',
    'cfg.tail': '尾延迟比 p99/p50', 'cfg.kv': 'KV 压力阈值',
    'cfg.prefix': '前缀命中低阈值', 'cfg.weights': '权重占显存阈值',
    'kind.classifier': '分类器', 'kind.symptom': '入口症状', 'kind.fact': '判别', 'kind.custom': '自定义',
    'rules.diagCurrent': '当前触发的诊断', 'rules.diagRecent': '最近命中的诊断',
    'rules.freshHint': '最近 5 分钟去重后', 'rules.staleHint': '当前无触发 · 显示最近一次命中',
    'rules.allGood': '一切看起来都不错', 'rules.none': '当前窗口内没有规则被触发',
    'rules.title': '诊断规则',
    'rules.hint': '规则名=客观事实(测出来的);根因/处方是推断,供参考、非定论。策展规则只读,自定义规则可增删改。',
    'rules.newRule': '+ 新建规则', 'rules.newRuleDisabled': '引擎未运行,暂不可建',
    'rules.precondition': '前置:', 'rules.matchAny': '任一满足', 'rules.matchAll': '全部满足',
    'rules.onlyRegime': '仅 ',
    'rules.classifierNote': '引擎按 Roofline 算术强度 vs 脊点判定(无阈值)',
    'rules.customTitle': '自定义规则',
    'rules.customHintActive': '命中即在「触发的诊断」里冒,和 S1/D3a 同路径',
    'rules.customHintInactive': '引擎未运行,暂不可编辑',
    'rules.customEmpty': '还没有自定义规则。点右上「+ 新建规则」加一条 —— 它会和策展规则一起参与诊断。',
    'common.close': '关闭', 'common.copy': '复制',
    'live.tier1': '用户感知指标', 'live.tier1hint': '最近 60 秒 · 每 2s 刷新', 'live.tier2': '效率与诊断',
    'kpi.ttft': 'TTFT 平均', 'kpi.ttft.sub': '首 token 延迟', 'kpi.reqs': '请求',
    'kpi.tpot': 'TPOT 平均', 'kpi.tpot.sub': '每 token 间隔',
    'kpi.tput': 'Output 吞吐', 'kpi.tput.sub': '系统聚合', 'kpi.tput.perreq': '单请求',
    'kpi.kv': 'KV cache', 'kpi.running': '运行请求', 'kpi.waiting': '等待队列',
    'kpi.mfu': 'MFU', 'kpi.mfu.sub': '算力利用率',
    'kpi.gpuutil': 'GPU 利用率', 'kpi.gpuutil.sub': 'SM 忙碌',
    'kpi.vram': '显存占用', 'kpi.vram.sub': 'VRAM 容量', 'kpi.prefix': 'Prefix cache 命中',
    'startup.btn': '启动信息', 'startup.title': 'vLLM 启动信息',
    'startup.cmdline': '启动命令', 'startup.noCmdline': '未捕获到 cmdline(plugin 早于 sys.argv 设定?)',
    'startup.env': '环境变量', 'startup.noEnv': '无相关环境变量(仅含 VLLM_/PPING_LANG_/HF_/CUDA_/TORCH_ 等前缀)',
    'startup.resolved': 'vLLM 解析配置', 'startup.resolvedSub': 'CLI + 默认值合并后的最终生效值',
    'startup.noConfig': '无 vllm_config(plugin 实例化时未拿到,常见于本地 demo)',
    'startup.masked': '名称含 TOKEN/KEY/SECRET,值已脱敏',
    'lang.label': '语言 / Language',
  },
  en: {
    'nav.live': 'Live', 'nav.kernel': 'Kernel', 'nav.rules': 'Rules', 'nav.bench': 'Bench',
    'brand.sub': 'vLLM perf diagnostics',
    'btn.issue': 'Issue', 'btn.star': 'Star',
    'hero.info': 'View vLLM launch command, env vars, resolved config',
    'common.save': 'Save', 'common.saving': 'Saving…', 'common.reset': 'Reset',
    'common.suggestion': 'Suggestion', 'common.inference': 'Inference', 'common.edit': 'Edit', 'common.delete': 'Delete',
    'cfg.form': 'Workload', 'cfg.advanced': 'Advanced',
    'cfg.hintActive': 'Edit once — every curated rule referencing it follows; saved changes hot-reload, no restart.',
    'cfg.hintInactive': 'Engine not running; saving only validates, takes effect after restart.',
    'cfg.sla_ttft': 'TTFT p99 SLA', 'cfg.sla_tpot': 'TPOT p99 SLA',
    'cfg.long_prompt': 'Long-prompt threshold', 'cfg.waiting': 'Waiting-queue threshold',
    'cfg.mbu_high': 'MBU near-roof threshold', 'cfg.mbu_low': 'MBU low threshold',
    'cfg.batch_small': 'Small-batch threshold', 'cfg.mfu_low': 'MFU low threshold',
    'cfg.tail': 'Tail latency p99/p50', 'cfg.kv': 'KV pressure threshold',
    'cfg.prefix': 'Prefix-hit low threshold', 'cfg.weights': 'Weights/HBM threshold',
    'kind.classifier': 'Classifier', 'kind.symptom': 'Symptom', 'kind.fact': 'Fact rule', 'kind.custom': 'Custom',
    'rules.diagCurrent': 'Active diagnoses', 'rules.diagRecent': 'Recent diagnoses',
    'rules.freshHint': 'Last 5 min, deduped', 'rules.staleHint': 'None active — showing the latest hit',
    'rules.allGood': 'All looks good', 'rules.none': 'No rules fired in the current window',
    'rules.title': 'Diagnosis rules',
    'rules.hint': 'Rule name = objective fact (measured); root cause / fix is inference — for reference, not a verdict. Curated rules are read-only; custom rules are editable.',
    'rules.newRule': '+ New rule', 'rules.newRuleDisabled': 'Engine not running — can’t create',
    'rules.precondition': 'Precondition:', 'rules.matchAny': 'any matches', 'rules.matchAll': 'all match',
    'rules.onlyRegime': 'only ',
    'rules.classifierNote': 'Engine classifies by Roofline arithmetic intensity vs ridge (no threshold)',
    'rules.customTitle': 'Custom rules',
    'rules.customHintActive': 'Fires into “Active diagnoses” just like S1/D3a',
    'rules.customHintInactive': 'Engine not running — read-only',
    'rules.customEmpty': 'No custom rules yet. Click “+ New rule” (top-right) to add one — it joins the curated rules in diagnosis.',
    'common.close': 'Close', 'common.copy': 'Copy',
    'live.tier1': 'User-facing metrics', 'live.tier1hint': 'Last 60s · refreshed every 2s', 'live.tier2': 'Efficiency & diagnostics',
    'kpi.ttft': 'TTFT avg', 'kpi.ttft.sub': 'first-token latency', 'kpi.reqs': 'reqs',
    'kpi.tpot': 'TPOT avg', 'kpi.tpot.sub': 'per-token interval',
    'kpi.tput': 'Output throughput', 'kpi.tput.sub': 'system aggregate', 'kpi.tput.perreq': 'per-request',
    'kpi.kv': 'KV cache', 'kpi.running': 'Running reqs', 'kpi.waiting': 'Waiting queue',
    'kpi.mfu': 'MFU', 'kpi.mfu.sub': 'compute utilization',
    'kpi.gpuutil': 'GPU utilization', 'kpi.gpuutil.sub': 'SM busy',
    'kpi.vram': 'VRAM used', 'kpi.vram.sub': 'VRAM capacity', 'kpi.prefix': 'Prefix cache hit',
    'startup.btn': 'Startup info', 'startup.title': 'vLLM startup info',
    'startup.cmdline': 'Launch command', 'startup.noCmdline': 'cmdline not captured (plugin ran before sys.argv?)',
    'startup.env': 'Environment variables', 'startup.noEnv': 'No relevant env vars (only VLLM_/PPING_LANG_/HF_/CUDA_/TORCH_ prefixes)',
    'startup.resolved': 'vLLM resolved config', 'startup.resolvedSub': 'final values after merging CLI + defaults',
    'startup.noConfig': 'No vllm_config (not available at plugin init; common in local demo)',
    'startup.masked': 'name contains TOKEN/KEY/SECRET — value masked',
    'lang.label': 'Language / 语言',
  },
};
function _uiLang() {
  try { const s = window.Alpine && Alpine.store('i18n'); if (s && s.lang) return s.lang; } catch (e) { /* pre-init */ }
  return localStorage.getItem('pping_lang_ui')
    || ((navigator.language || '').toLowerCase().startsWith('zh') ? 'zh' : 'en');
}
window.t = function (key) {
  const lang = _uiLang();
  return (I18N[lang] && I18N[lang][key]) || I18N.en[key] || key;
};
document.addEventListener('alpine:init', () => {
  Alpine.store('i18n', {
    lang: localStorage.getItem('pping_lang_ui')
      || ((navigator.language || '').toLowerCase().startsWith('zh') ? 'zh' : 'en'),
  });
});

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
        ctx.fillStyle = p.labelColor || '#6e6e78';
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
          label: 'Compute roof', data: [], showLine: true, borderColor: '#d8483f', borderWidth: 2.5,
          pointRadius: 0, fill: 'origin', backgroundColor: 'rgba(220, 77, 62, 0.06)', tension: 0, order: 1,
        },
        {
          label: 'Memory roof', data: [], showLine: true, borderColor: '#5b5bd6', borderWidth: 2.5,
          pointRadius: 0, fill: 'origin', backgroundColor: 'rgba(81, 71, 200, 0.06)', tension: 0, order: 2,
        },
        {
          // 调优地图:decode 的算术强度≈batch → 扩 batch 沿带宽上界向右爬,ridge point 后 compute-bound
          label: 'batch scaling envelope', data: [], showLine: true, borderColor: '#9a9aa4',
          borderDash: [5, 4], borderWidth: 1.5, pointRadius: 3.5, pointStyle: 'rectRot',
          backgroundColor: '#9a9aa4', fill: false, order: 4,
        },
        {
          // P0-C:实测 scaling 曲线(压测扫并发档)—— 缺口从哪个 B 张开 = 真实瓶颈位置
          label: '实测 scaling', data: [], showLine: true, borderColor: '#0d8b80',
          borderWidth: 2, pointRadius: 5, pointHoverRadius: 8, pointStyle: 'circle',
          backgroundColor: '#0d8b80', fill: false, order: 5,
        },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      // ridge point 标注画在 compute roof 顶端(peakC = 绘图区最顶),标签还往上 9px + 文字高度,
      // 没顶部留白就会顶出画布被裁。留 26px 顶部 padding 给最顶那行标签。
      layout: { padding: { top: 26 } },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#1c1c22', titleColor: '#fff', bodyColor: '#f4f4f7', padding: 11,
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
              if (ds === '实测 scaling') {
                return [`实测 并发${ctx.raw.b}: ${ctx.parsed.y.toFixed(2)} TFLOPs/s`,
                        `理论 envelope: ${(ctx.raw.env || 0).toFixed(2)} TFLOPs/s`,
                        `缺口: ${(ctx.raw.gap || 0).toFixed(0)}%`];
              }
              return `${ds}: ${ctx.parsed.y.toFixed(1)} TFLOPs/s`;
            },
          },
        },
      },
      scales: {
        // log-log 轴的网格会按次刻度密画(1,2,3…10,20,30…),两轴都开直接变坐标纸 —— 全关,
        // roofline 的参照系是两条 roof 线本身,不需要网格
        x: {
          type: 'logarithmic',
          title: { display: true, text: 'Arithmetic Intensity (FLOPs / byte)', color: '#6e6e78', font: { size: 11.5, weight: '600' } },
          ticks: { color: '#9a9aa4', font: { size: 11 } }, grid: { display: false },
        },
        y: {
          type: 'logarithmic',
          title: { display: true, text: 'Achieved Throughput (TFLOPs/s)', color: '#6e6e78', font: { size: 11.5, weight: '600' } },
          ticks: { color: '#9a9aa4', font: { size: 11 } }, grid: { display: false },
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
      if (pf.x > dec.x * 2.5) { pf.label = 'prefill'; pf.labelColor = '#6e6e78'; }
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
        label: [1, 8, 32, 128].includes(b) ? `B=${b}` : '', labelDy: -9, labelColor: '#9a9aa4',
      });
    }
    traj.push({ x: knee, y: peakC, b: Math.round(knee), label: `ridge point (AI=${knee.toFixed(0)})`, labelDy: -9, labelColor: '#d8483f' });
    chart.data.datasets[3].data = traj;
  } else {
    chart.data.datasets[1].data = [];
    chart.data.datasets[2].data = [];
    chart.data.datasets[3].data = [];
  }
  // P0-C:实测 scaling 曲线(压测扫出来的真实扩展点,叠在理论 envelope 上)
  const rows = (data.scaling && data.scaling.verdict && data.scaling.verdict.rows) || [];
  chart.data.datasets[4].data = rows.map((r, i) => ({
    x: r.b, y: r.tflops, b: r.b, env: r.envelope_tflops, gap: r.gap_pct,
    label: i === rows.length - 1 ? '实测' : '', labelDy: 14, labelColor: '#0d8b80', labelBold: true,
  }));
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
          // 浅线 = p99(尾部参考,弱化);实线 = 平均(典型体验,平均为主,用户反馈)
          label: 'p99',
          data: [],
          borderColor: color + '55',
          backgroundColor: 'transparent',
          borderWidth: 1.5,
          tension: 0.35,
          fill: false,
          pointRadius: 0,
        },
        {
          label: '平均',
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
          backgroundColor: '#1c1c22',
          titleColor: '#fff',
          bodyColor: '#f4f4f7',
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
            color: '#9a9aa4',
            font: { size: 10 },
            maxTicksLimit: 4,
            callback: (v) => v + 'ms',
          },
          grid: { color: '#ececf1', drawBorder: false },
        },
        x: { display: false, grid: { display: false } },
      },
    },
  });
}

function _updateMiniLatencyChart(chart, buckets) {
  if (!chart) return;
  chart.data.labels = buckets.map(b => Math.round(b.t) + 's');
  chart.data.datasets[0].data = buckets.map(b => b.p99);
  chart.data.datasets[1].data = buckets.map(b => b.avg != null ? b.avg : b.p50);
  chart.update('none');
}

// kernel 类(堆叠面积)— 顺序 = 画的层序
const _KCLASSES = [
  ['gemm', '#5b5bd6', 'GEMM'], ['attention', '#0d8b80', 'Attention'],
  ['comm', '#d8483f', '通信'], ['norm', '#b7791f', 'Norm'],
  ['activation', '#3f9a63', 'Activation'], ['rotary', '#c2334f', 'Rotary'],
  ['other', '#9a9aa4', '其它'],
];
const _kTip = {backgroundColor:'#1c1c22',titleColor:'#fff',bodyColor:'#f4f4f7',padding:9,cornerRadius:6,borderWidth:0,titleFont:{weight:'600',size:11},bodyFont:{size:11}};
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
        y: {stacked: true, min: 0, max: 100, ticks: {color: '#9a9aa4', font: {size: 10}, maxTicksLimit: 5, callback: v => v + '%'}, grid: {color: '#ececf1', drawBorder: false}},
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
      {label: '同步等待 (launch-bound)', data: [], borderColor: '#b7791f', backgroundColor: 'transparent', borderWidth: 2, fill: false, pointRadius: 0, tension: 0.3},
    ]},
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: {mode: 'index', intersect: false},
      plugins: {legend: {display: true, position: 'top', align: 'end', labels: {font: {size: 10}, boxWidth: 10, color: '#6e6e78'}},
        tooltip: {..._kTip, callbacks: {label: c => `${c.dataset.label}: ${c.parsed.y == null ? '—' : c.parsed.y.toFixed(0) + '%'}`}}},
      scales: {
        y: {min: 0, max: 100, ticks: {color: '#9a9aa4', font: {size: 10}, maxTicksLimit: 5, callback: v => v + '%'}, grid: {color: '#ececf1', drawBorder: false}},
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
        // Endpoint:优先用后端解析出的真实 vLLM 端点(从启动 cmdline 的 --host/--port,
        // 已把 0.0.0.0 归一成 127.0.0.1)。压测在服务端跑,这个端点服务端本机可达、且端口正确
        // (vLLM 不在默认 :8000 时也不会猜错)。后端没给(老版本)才退回 :8000 的浏览器 host 猜测。
        if (this.form.endpoint === 'http://localhost:8000') {
          if (sys.vllm_endpoint) {
            this.form.endpoint = sys.vllm_endpoint;
          } else {
            this.form.endpoint = `http://${window.location.hostname}:8000`;
          }
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

    // ===== 压测结果对比:任选两个 run,A=先选(基准),B=后选,Δ=B 相对 A =====
    cmpSel: [],
    toggleCmp(id) {
      const i = this.cmpSel.indexOf(id);
      if (i >= 0) this.cmpSel.splice(i, 1);
      else { this.cmpSel.push(id); if (this.cmpSel.length > 2) this.cmpSel.shift(); }
    },
    cmpRuns() {
      if (this.cmpSel.length !== 2) return null;
      const a = this.runs.find(r => r.run_id === this.cmpSel[0]);
      const b = this.runs.find(r => r.run_id === this.cmpSel[1]);
      return (a && b) ? [a, b] : null;
    },
    cmpScenario(r) {
      const s = (r && r.scenario) || {};
      const len = s.duration_s ? `${s.duration_s}s` : `${s.num_requests} req`;
      return `并发 ${s.concurrency} · ${s.prompt_tokens}/${s.output_tokens} tok · ${len}`;
    },
    // 对比卡数据:逐指标 A/B 双横条(按本指标 max 归一,免得 ms 与 tok/s 挤同轴)+ Δ%。
    // 延迟类越低越好,吞吐越高越好;|Δ|<2% 视为持平(压测运行间噪声)
    cmpTable() {
      const pair = this.cmpRuns();
      if (!pair) return [];
      const [A, B] = pair;
      const g = (r, p) => p.split('.').reduce((o, k) => (o == null ? null : o[k]), r);
      const defs = [
        { label: 'TTFT 平均', path: 'client_metrics.ttft_ms.mean', lower: true, unit: 'ms' },
        { label: 'TTFT p99',  path: 'client_metrics.ttft_ms.p99',  lower: true, unit: 'ms' },
        { label: 'TPOT 平均', path: 'client_metrics.tpot_ms.mean', lower: true, unit: 'ms' },
        { label: 'TPOT p99',  path: 'client_metrics.tpot_ms.p99',  lower: true, unit: 'ms' },
        { label: 'E2E 平均',  path: 'client_metrics.e2e_ms.mean',  lower: true, unit: 'ms' },
        { label: 'E2E p99',   path: 'client_metrics.e2e_ms.p99',   lower: true, unit: 'ms' },
        { label: 'Output 吞吐', path: 'client_metrics.output_throughput_tps', lower: false, unit: 'tok/s' },
        { label: '完成 / 错误', path: 'client_metrics.ok', path2: 'client_metrics.errors', lower: false, unit: '' },
      ];
      return defs.map(d => {
        const a = g(A, d.path), b = g(B, d.path);
        let pct = null, good = null;
        if (a != null && b != null && Number(a) !== 0) {
          pct = 100 * (b - a) / a;
          if (Math.abs(pct) >= 2) good = d.lower ? pct < 0 : pct > 0;
        }
        const mx = Math.max(Number(a) || 0, Number(b) || 0);
        const bar = (v) => (v == null || mx <= 0) ? 0 : Math.max(2, 100 * Number(v) / mx);
        const f = (v) => v == null ? '—'
          : (d.unit === 'ms' ? Number(v).toFixed(1) : Number(v).toFixed(0)) + (d.unit ? ' ' + d.unit : '');
        // "完成 / 错误"特例:数值文案带上错误数
        const fa = d.path2 ? `${f(a)} / ${g(A, d.path2) ?? '—'}` : f(a);
        const fb = d.path2 ? `${f(b)} / ${g(B, d.path2) ?? '—'}` : f(b);
        return { label: d.label, aText: fa, bText: fb, barA: bar(a), barB: bar(b), pct, good };
      });
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
  // 业务形态 → (TTFT_p99, TPOT_p99) SLA 默认,与后端 _WORKLOAD_SLA 对齐
  const WORKLOAD_SLA = {
    chat: [1000, 50], rag: [3000, 50], agent: [1000, 50],
    reasoning: [1000, 30], code: [100, 20], custom: [2000, 50],
  };
  // 配置项 → [中文标签, 单位]。决定哪些字段在表单里出现 + 顺序
  // [i18n key, 单位] —— 标签走 t(),决定哪些字段在高级网格里出现 + 顺序
  const CFG_LABELS = {
    sla_ttft_p99_ms: ['cfg.sla_ttft', 'ms'],
    sla_tpot_p99_ms: ['cfg.sla_tpot', 'ms'],
    long_prompt_tokens: ['cfg.long_prompt', 'tokens'],
    waiting_reqs: ['cfg.waiting', 'reqs'],
    mbu_high_pct: ['cfg.mbu_high', '%'],
    mbu_low_pct: ['cfg.mbu_low', '%'],
    batch_small_reqs: ['cfg.batch_small', 'reqs'],
    mfu_low_ratio: ['cfg.mfu_low', '0–1'],
    tail_ratio: ['cfg.tail', '×'],
    kv_pressure_ratio: ['cfg.kv', '0–1'],
    prefix_hit_low: ['cfg.prefix', '0–1'],
    weights_hbm_ratio: ['cfg.weights', '0–1'],
  };
  const KIND_LABEL = { classifier: '分类器', symptom: '入口症状', fact: '判别' };
  return {
    rules: [], customRules: [], customEditable: false,
    config: {}, cfgDraft: {}, workloadForms: [], active: false,
    availableMetrics: [],
    editing: null, editingExisting: false,   // 自定义规则编辑态
    advancedOpen: false,                      // 高级阈值折叠面板开关
    saving: false, toast: '', toastError: false,
    cfgLabels: CFG_LABELS, kindLabel: KIND_LABEL,

    async init() { await Promise.all([this.load(), this.loadMetrics()]); },
    // 触发的诊断卡片用这俩 helper 把 rule_id → 人话(策展 + 自定义都查)
    allRules() { return [...this.rules, ...this.customRules]; },
    ruleName(rule_id) { const r = this.allRules().find(x => x.id === rule_id); return r ? r.name : rule_id; },
    ruleCategory(rule_id) {
      const r = this.allRules().find(x => x.id === rule_id);
      if (!r) return '';
      return r.custom ? t('kind.custom') : (t('kind.' + r.kind) || r.kind || '');
    },

    async load() {
      const d = await fetch('/api/diagnosis_rules').then(r => r.json());
      this.rules = d.rules || [];
      this.customRules = d.custom_rules || [];
      this.customEditable = !!d.custom_editable;
      this.config = d.config || {};
      this.workloadForms = d.workload_forms || [];
      this.active = !!d.active;
      this.cfgDraft = JSON.parse(JSON.stringify(this.config));
    },
    async loadMetrics() {
      try {
        const r = await fetch('/api/metrics/available').then(r => r.json());
        this.availableMetrics = r.metrics || [];
      } catch (e) { this.availableMetrics = []; }
    },

    // === 自定义规则 CRUD(走 /api/diagnosis_rules/custom,和策展规则同一引擎评)===
    blankRule() {
      return {
        name: '', metric: this.availableMetrics[0] || 'gpu.utilization_pct',
        op: '<', threshold: 50, window_seconds: 30, aggregation: 'avg',
        severity: 'warning', hypothesis: '', suggestion: '',
      };
    },
    newRule() { this.editing = this.blankRule(); this.editingExisting = false; },
    editRule(r) { this.editing = JSON.parse(JSON.stringify(r)); this.editingExisting = true; },
    cancelEdit() { this.editing = null; },
    async saveRule() {
      try {
        const url = this.editingExisting
          ? `/api/diagnosis_rules/custom/${this.editing.id}` : '/api/diagnosis_rules/custom';
        const r = await fetch(url, {
          method: this.editingExisting ? 'PUT' : 'POST',
          headers: {'Content-Type': 'application/json'}, body: JSON.stringify(this.editing),
        });
        const body = await r.json().catch(() => ({}));
        if (!r.ok) { this.showToast(`保存失败: ${body.detail || r.status}`, true); return; }
        this.showToast(this.editingExisting ? '已更新' : '已创建(立即参与诊断)');
        this.editing = null;
        await this.load();
      } catch (e) { this.showToast(`错误: ${e}`, true); }
    },
    async deleteRule(r) {
      if (!confirm(`删除自定义规则「${r.name}」？`)) return;
      const resp = await fetch(`/api/diagnosis_rules/custom/${r.id}`, {method: 'DELETE'});
      if (!resp.ok) { this.showToast(`删除失败: ${resp.status}`, true); return; }
      this.showToast('已删除');
      await this.load();
    },
    cfgKeys() { return Object.keys(CFG_LABELS).filter(k => k in this.cfgDraft); },
    // 高级面板只放阈值;TTFT/TPOT SLA 已常驻顶部条,不重复
    advKeys() { return this.cfgKeys().filter(k => k !== 'sla_ttft_p99_ms' && k !== 'sla_tpot_p99_ms'); },
    onFormChange(form) {
      // 读下拉框新值,不依赖 x-model 是否已写回 cfgDraft(否则读到切换前的旧形态)
      form = form || this.cfgDraft.workload_form;
      const sla = WORKLOAD_SLA[form];
      if (sla) { this.cfgDraft.sla_ttft_p99_ms = sla[0]; this.cfgDraft.sla_tpot_p99_ms = sla[1]; }
    },
    dirty() { return JSON.stringify(this.cfgDraft) !== JSON.stringify(this.config); },
    resetConfig() { this.cfgDraft = JSON.parse(JSON.stringify(this.config)); },
    async saveConfig() {
      this.saving = true;
      try {
        const r = await fetch('/api/diagnosis_config', {
          method: 'PUT', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(this.cfgDraft),
        });
        const body = await r.json().catch(() => ({}));
        if (!r.ok) { this.showToast(`保存失败: ${body.detail || r.status}`, true); return; }
        this.rules = body.rules || this.rules;
        this.config = body.config || this.config;
        this.cfgDraft = JSON.parse(JSON.stringify(this.config));
        this.showToast(body.applied ? '已保存,热生效' : '已保存(引擎未运行,重启后生效)');
      } catch (e) { this.showToast(`错误: ${e}`, true); }
      finally { this.saving = false; }
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
    scalingSweep: { running: false, progress: null, error: null, verdict: null },  // P0-C 实测 scaling
    ttftHasData: false,
    tpotHasData: false,
    e2eHasData: false,
    e2eAvg: null,
    diagnoses: [],
    diagnosesStale: false,   // true = 当前无触发,面板显示的是最近一次命中(history 回退)
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
        attention: '#0d8b80', gemm: '#5b5bd6', norm: '#b7791f',
        rotary: '#c2334f', activation: '#3f9a63', comm: '#d8483f',
        elementwise: '#3f7fa8', sampling: '#9b59b6', index: '#b7791f',
        memcpy: '#6e6e78', other: '#9a9aa4',
      }[cls] || '#9a9aa4';
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
    // P3 launch 栈:把 native 栈清洗成可读帧链(caller→callee:host 代码在前,启动原语在后)
    launchFrames(h) {
      if (!h || !h.launch || !h.launch.stack) return [];
      let frames = h.launch.stack.split(' <- ').map(s => s.trim()).filter(Boolean);
      frames = frames.map(f => {
        let s = f.replace(/^void\s+/, '');
        const lt = s.indexOf('<'), pr = s.indexOf('(');
        let cut = s.length;
        if (lt > 0) cut = Math.min(cut, lt);
        if (pr > 0) cut = Math.min(cut, pr);
        s = s.slice(0, cut).trim();
        // 去 PyTorch 派发器噪音后缀(::impl / ::call / ::redispatch / ::out),露出真正的算子名
        let parts = s.split('::').filter(Boolean);
        while (parts.length > 1 && /^(impl|call|redispatch|out|cuda|reimpl)$/.test(parts[parts.length - 1]))
          parts.pop();
        // 去 at/at::_ops/c10/torch 这类命名空间前缀,留末段算子名
        return parts[parts.length - 1] || s;
      }).filter(f => f && !/^_PyEval|^_PyObject|^PyObject|^PyNumber|make_boxed|wrap_kernel|_get_operation|^Wrap/.test(f));
      // 相邻重复(addmm::call 与 addmm::impl 清洗后同名)去重
      frames = frames.filter((f, i) => i === 0 || f !== frames[i - 1]);
      return frames.reverse();   // host 高层算子在前 → 启动原语在后
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
        memory_dependency: '#5b5bd6', shared_dependency: '#0d8b80',
        memory_throttle: '#7a5cc8', math_pipe: '#b7791f', exec_dependency: '#c2334f',
        sync: '#d8483f', fetch_control: '#3f9a63', dispatch: '#9a8f1f',
        scheduler_slack: '#9bb04f', other: '#9a9aa4',
      }[cls] || '#9a9aa4';
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
    // P0-C:启动实测 scaling 压测(串扫并发 1/4/16/64,约 2 分钟),轮询直到出结果
    async startScalingSweep() {
      if (this.scalingSweep.running) return;
      this.scalingSweep.running = true;
      this.scalingSweep.error = null;
      this.scalingSweep.progress = '启动中…';
      try {
        const r = await fetch('/api/roofline/scaling_sweep', { method: 'POST' });
        if (!r.ok) {
          const e = await r.json().catch(() => ({}));
          throw new Error(e.detail || `HTTP ${r.status}`);
        }
        // 轮询状态;结束后强刷一次 roofline(图 + verdict 同步上屏)
        while (true) {
          await new Promise(res => setTimeout(res, 4000));
          const s = await fetch('/api/roofline/scaling').then(x => x.json());
          if (s.error) throw new Error(s.error);
          if (!s.running) break;
          this.scalingSweep.progress = s.progress || '压测中…';
        }
        const data = await fetch('/api/roofline?seconds=60').then(x => x.json());
        this.updateRoofline(data);
      } catch (e) {
        this.scalingSweep.error = String(e.message || e);
      } finally {
        this.scalingSweep.running = false;
        this.scalingSweep.progress = null;
      }
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
    // 延迟分位条(三行式):某分位占 p99 的宽度%(p99=满刻度;三段挤一条看不清,实测反馈)
    pctW(d, q) {
      if (!d || !d.p99 || d.p99 <= 0 || d[q] == null) return 0;
      return Math.max(2, Math.min(100, 100 * d[q] / d.p99));
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
      _ttftChart = _createMiniLatencyChart('ttft-chart', '#d8483f');
      _tpotChart = _createMiniLatencyChart('tpot-chart', '#5b5bd6');
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
      // P0-C:实测 scaling verdict(随 roofline 响应带回)
      this.scalingSweep.verdict = (data.scaling && data.scaling.verdict) || null;
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
        const [healthR, kpisR, rooflineR, trendsR, diagR, diagHistR, benchStatusR, kernelsR, tlR, kTrendsR] = await Promise.all([
          fetch('/api/health').then(r => r.json()),
          fetch('/api/kpis?window=60').then(r => r.json()),
          fetch('/api/roofline?seconds=60').then(r => r.json()),
          fetch('/api/latency_trends?seconds=300&buckets=30').then(r => r.json()),
          fetch('/api/diagnoses?seconds=300').then(r => r.json()),
          fetch('/api/diagnoses/history?limit=200').then(r => r.json()).catch(() => ({diagnoses: []})),
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

        // 当前触发(近 5 分钟)按 rule_id 去重;空窗时回退到内存环里**最近一次命中**(history,
        // 不论多久前),面板不归零。stale=true 时标签提示"显示最近一次命中、当前无触发"。
        const dedupeByRule = (arr) => {
          const seen = new Set();
          return (arr || []).filter(d => {
            if (seen.has(d.rule_id)) return false;
            seen.add(d.rule_id);
            return true;
          });
        };
        const current = dedupeByRule(diagR.diagnoses);
        if (current.length > 0) {
          this.diagnoses = current;
          this.diagnosesStale = false;
        } else {
          this.diagnoses = dedupeByRule(diagHistR.diagnoses);
          this.diagnosesStale = this.diagnoses.length > 0;
        }

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
      this.e2eAvg = this.e2eHasData ? (e2e[e2e.length - 1].avg ?? e2e[e2e.length - 1].p50) : null;
      _updateMiniLatencyChart(_ttftChart, ttft);
      _updateMiniLatencyChart(_tpotChart, tpot);
      _updateMiniLatencyChart(_e2eChart, e2e);
    },
  };
}
