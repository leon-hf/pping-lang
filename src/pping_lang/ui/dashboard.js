// ===== i18n(中/英;框架支持多语,加语言只需补字典 + 一个 lang-toggle 按钮)=====
// 全局 t(key):x-text="t('k')" / :title="t('k')" 处处可用(dashboard/rulesTab/benchTab 同享)。
// 语言存 Alpine.store('i18n').lang(响应式,切换即时重渲染)+ localStorage 持久化。
const I18N = {
  zh: {
    'nav.live': '实时', 'nav.kernel': 'Kernel', 'nav.rules': '瓶颈诊断', 'nav.bench': '压测', 'nav.autopilot': 'Autopilot', 'nav.preview': 'beta',
    'brand.sub': 'vLLM 性能诊断',
    'btn.issue': '提 Issue', 'btn.star': 'Star',
    'hero.info': '查看 vLLM 启动命令、环境变量、解析配置',
    'common.save': '保存', 'common.saving': '保存中…', 'common.reset': '还原',
    'common.suggestion': '建议', 'common.inference': '推断', 
    'cfg.hintActive': '改一处,引用它的策展规则全跟着变 · 保存即热生效,无需重启',
    'cfg.hintInactive': '引擎未运行,保存仅校验,重启后生效',
    'cfg.mbu_high': 'HBM 占用上限阈值(NVML%)', 'cfg.mbu_low': 'HBM 占用偏低阈值(NVML%)',
    'cfg.mfu_low': 'MFU 偏低阈值', 'cfg.mfu_high': 'MFU 饱和阈值',
    'cfg.kv': 'KV 压力阈值', 'cfg.min_running': '空载守卫：在跑请求下限',
    'cfg.stall_mem': '内核访存 throttle 阈值', 'cfg.stall_memdep': '内核访存延迟阈值', 'cfg.stall_math': '内核算力管线饱和阈值',
    'slo.form': '业务形态', 'slo.thresholds': '瓶颈阈值', 'slo.pass': '达标', 'slo.fail': '超标',
    'shape.chat': '对话', 'shape.rag': 'RAG 问答', 'shape.agent': 'Agent', 'shape.reasoning': '长推理',
    'shape.code': '代码补全', 'shape.custom': '自定义', 'ap.customLoadNote': '全手动(沿用旧默认)',
    'slo.benchHint': '如需验证调参收益,可在压测台对比 A/B。', 'slo.benchBtn': '前往压测 →',
    'rules.staleHint': '当前无触发 · 显示最近一次命中',
    'rules.clickExpand': '点击展开详情 + 证据',
    'rules.builtinTitle': '内置诊断 · 4 瓶颈', 'rules.colBottleneck': '瓶颈', 'rules.colStatus': '状态', 'rules.colMethods': '检测手段(跨层)',
    'rules.miss': '未命中', 'rules.hitN': '命中 {n}/{m} 手段', 'rules.detectors': '检测手段（{n} 条独立、跨测量层）', 'rules.hitHint': '{n} 个瓶颈命中(最近 5 分钟去重)', 'rules.noneHint': '当前无瓶颈命中 —— 系统运行正常',
    'layer.L1': 'L1 实测 roofline（perf_stats:MFU/MBU）', 'layer.L2': 'L2 内核 stall（CUPTI PC sampling）', 'layer.L3': 'L3 调度态（vLLM 原生,永远在）', 'layer.L4': 'L4 请求时延', 'layer.L5': 'L5 GPU 硬件（NVML）',
    'rules.opTitle': '当前操作点 · 距各上限的余量', 'rules.opHint': '实测值;刻度为低/高阈值。逼近某一上限即命中对应瓶颈',
    'rules.computeRoof': '算力上限 · MFU', 'rules.bwRoof': '带宽上限 · HBM 占用(NVML)', 'rules.capacity': '容量 · KV 占用',
    'rules.roofC': '逼近上限 → 算力瓶颈', 'rules.roofB': '逼近上限 → 带宽瓶颈', 'rules.roofD': '接近耗尽 → 容量瓶颈',
    'rules.zone.far': '有余量', 'rules.zone.mid': '上升中', 'rules.zone.wall': '逼近上限', 'rules.zone.na': '无数据',
    'rules.runningNow': '在途请求', 'rules.guardArmed': '有在途请求(可判定双低)', 'rules.guardIdle': '空载(守卫生效,不判定双低)',
    'rules.layersTitle': '检测层 · L1–L5 含义', 'rules.layersHint': '每个瓶颈由跨测量层的多条独立检测手段交叉印证 —— 层间越独立,越不易同时失效(优雅降级)',
    'rules.toAutopilotHint': '想据此自动调优?交给 Autopilot 在沙盒里迭代调参、压测验证(预览)。', 'rules.toAutopilotBtn': '交给 Autopilot →',
    'regime.A': '双低', 'regime.B': '带宽瓶颈', 'regime.C': '算力瓶颈', 'regime.D': '容量瓶颈', 'regime.N': '症状/其它',
    'ap.realMode': '真实模式：Autopilot 会在 runw host 侧停主 serve 腾卡 → 起候选 vLLM 沙盒 → 跑 bench → kept/reverted → 恢复主 serve。若 host bridge 未连接,开始按钮会报错而不是偷偷跑模拟。',
    'ap.localMode': '本地/dev 模式：当前没有 host bridge,开始后走 SimSandbox 验证闭环;在 runw dashboard 地址访问时才会启动真实 DockerSandbox 调优。',
    'ap.runningNote1': '调优进行中：主 serve 已让位给候选沙盒,<b>本页(:8765)此刻打不开新标签,请勿刷新</b>。本页直播直连 host bridge,不受影响;备用直播页：',
    'ap.runningNote2': '。调优结束或点「停止」后主 serve 与本页自动恢复。',
    'ap.agentTitle': 'Agent（调优决策 LLM）', 'ap.notConfigured': '未配置',
    'ap.presetLabel': '预设', 'ap.presetLocal': '本地 (Ollama / vLLM)', 'ap.presetCustom': '自定义',
    'ap.testConnection': '测试连接', 'ap.advanced': '高级',
    'ap.agentNote': 'OpenRouter / OpenAI / DeepSeek / Moonshot 走 <b style="color:var(--coral-strong)">OpenAI 兼容</b>端点；Kimi Coding 走 Kimi Coding Messages API。真实调优必须配置 LLM agent；StubAgent 只用于本地 SimSandbox/dev tests,不能驱动真实 GPU 调优。Key 仅本会话用于调 agent，<b>不入诊断热路径、不随轨迹存盘</b>；生产建议走环境变量注入。注意：<b>别用被压测的那个 vLLM 模型当 agent</b>（它在被压、可能太小）。',
    'ap.guidanceLabel': '额外指引（追加到系统 prompt 之后，塞业务约束 / 偏好）',
    'ap.guidancePlaceholder': '如：本集群 gpu-util 不超 0.85；优先 chunked-prefill 而非提并发；延迟敏感业务，TTFT 优先…',
    'ap.timeoutS': '超时 (s)', 'ap.retries': '重试',
    'ap.actionScope': '动作范围 · vLLM 全量 − 身份(~7) − 硬件收窄 − 降精度类',
    'ap.denyModel': 'model / host / port（身份,~7）', 'ap.denyParallel': 'tensor / pipeline-parallel（按卡数收窄）',
    'ap.denyQuant': 'quantization（会降精度,不提供）', 'ap.denyKvDtype': 'kv-cache-dtype（会降精度,不提供）',
    'ap.actionScopeNote': '这 ~250 个里大半 vLLM 启动时已自调到最优（KV block 数 / attention backend / 兼容门）；agent 实际只动 <b style="color:var(--coral-strong)">max-num-seqs / max-num-batched-tokens / gpu-util / performance_mode</b> 这一小撮 + 诊断点名的对症参数。安全不靠黑名单,靠 launch-catch + 一次性容器兜底 + 降精度参数永不提供。',
    'ap.showPromptBtn': '查看核心 prompt 合约（只读 · 不可改）', 'ap.hidePromptBtn': '收起核心 prompt 合约',
    'ap.lockedPrompt': `你是一个 LLM-serving 性能工程师(核心合约,不可改):
① 每轮只改 1 个参数,且只能从「动作范围」里选;
② 必须接受压测判决——不得声称压测没证实的收益;
③ 必须把改动挂在本轮诊断证据上(evidence_refs);
④ 必须按 StructuredOutput schema 输出 {action|done, rationale, evidence_refs};
⑤ 目标：不破 SLA 前提下最大化主指标;已近最优则 done:true。
———
每轮的 user message 由 runner 自动拼(用户不填):
目标 / 预算 / 当前配置 / 蒸馏诊断 / 历史 / 动作范围。
你的「额外指引」会追加在以上合约之后。`,
    'ap.shapeLabel': '形态', 'ap.maxPrefix': '最多', 'ap.roundsUnit': '轮',
    'ap.roundsCapTitle': '轮数和分钟都是上限,不是目标',
    'ap.roundsCapText': '轮数是上限：多数 session 3-5 轮诚实收敛,提前停属正常',
    'ap.stopBtn': '停止调优', 'ap.stopping': '停止中…',
    'ap.step.observe': '读诊断', 'ap.step.hypothesize': '挂诊断选参数', 'ap.step.act': '沙盒重启',
    'ap.step.measure': '压测打分', 'ap.step.decide': '留下/回滚',
    'ap.idleDesc': '点开始后，Autopilot 会启动一个调优 session。Agent 在候选集中选一个 vLLM 参数,沙盒重启后跑同一套 bench,只有实测更好才保留,破 SLA 或收益不足就回滚。真实 runw 模式需要先配置 LLM agent;生产上线仍走人工确认上线包。',
    'ap.moat': '<b>👁 眼睛</b>(深度诊断) + <b>✋ 手</b>(压测+serve控制) + <b>📊 记分牌</b>(基线/Δ) 同进程',
    'ap.evaluatedOf': '已评估 {n} / 最多 {m} 轮', 'ap.currentBest': '当前最优',
    'ap.fallbackWarnPre': '⚠ 本次有',
    'ap.fallbackWarnMid': '轮 LLM 调用失败,由确定性启发式兜底选择参数 —— 检查 agent 配置 / 额度 / 网络(点开对应轮的「Agent 推理」看具体错误;带',
    'ap.fallbackWarnEnd': '标记的轮不是 LLM 决策)。', 'ap.fallbackBadge': '⚠ 兜底',
    'ap.fallbackTitle': 'LLM 调用失败({err}),本轮由确定性启发式兜底 —— 检查 agent 配置/额度/网络',
    'ap.expandTitle': '单击展开诊断快照/压测打分/判定详情',
    'ap.diagLabel': '诊断', 'ap.hypLabel': '假设', 'ap.thinkingRaw': '思考原文',
    'ap.snapLabel': '诊断快照（Agent 看到的事实）', 'ap.expectedLabel': '预期效果',
    'ap.benchScoreLabel': '压测打分', 'ap.metricLabel': '指标', 'ap.decideLabel': '判定',
    'ap.runningLabel': '运行中', 'ap.heartbeatDefault': '状态心跳中，未改线上 serve',
    'ap.roundReasoning': '本轮 Agent 推理',
    'ap.lastResultPrefix': '📋 上次调优结果', 'ap.lastResultSuffix': '（非本次刚跑完，页面重新打开后自动带出）',
    'ap.throughputLabel': '吞吐', 'ap.throughputValue': '{a} → {b} tok/s（×{x}）',
    'ap.resultLabel': '结果', 'ap.whyLabel': '为什么', 'ap.ledgerLabel': '逐轮账本',
    'ap.candidatePoolLabel': '候选池',
    'ap.honestNote': '「×3」只在「现实但没调过」的默认基线上成立 —— 那正是大量真实部署的现状。已调过的基线则是诚实的边际收益（+5~15%），Agent 会如实报「已近最优」。',
    'ap.viewFullTrace': '查看完整推理轨迹', 'ap.viewPromotePkg': '查看上线包（人工确认）',
    'ap.promoteTitle': '上线包', 'ap.promoteNote': 'Autopilot 已生成可审阅命令，但没有修改生产。上线仍需要人工确认、流量切换和健康检查。',
    'ap.prodCmd': '生产命令', 'ap.rollbackCmd': '回滚命令', 'ap.configDiff': '配置 diff',
    'ap.riskNote': '风险提示', 'ap.manualChecklist': '人工确认清单', 'ap.scopeOfApply': '适用边界',
    'ap.fullTraceTitle': '完整推理轨迹', 'ap.reasoningLabel': 'Agent 推理', 'ap.evidenceLabel': '证据引用',
    'ap.thisRoundCmd': '本轮命令', 'ap.thinkingProcessLabel': 'Agent 思考过程(原始)',
    'ap.copyMarkdown': '复制 Markdown', 'ap.downloadJson': '下载 JSON',
    'ap.needFields': '需填 Base URL / API Key / Model', 'ap.connecting': '连接中…',
    'ap.connectionOk': '✓ 连接可用', 'ap.connectionFailed': '连接失败： ',
    'ap.needAgentConfig': '真实调优需要先配置 LLM agent', 'ap.sessionAlreadyRunning': '已有 session 在跑',
    'ap.startingRealTuning': '启动真实调优', 'ap.creatingSession': '正在创建 session 并准备基线压测',
    'ap.bridgeNotConnected': 'host bridge 未连接', 'ap.startFailed': '启动失败',
    'ap.stoppingAndRestoring': '正在停止调优并恢复 serve…', 'ap.terminatingSandbox': '正在终止候选沙盒并恢复主 serve',
    'ap.stopRequestedNoKeep': '请求停止中，未保留当前候选',
    'ap.stoppedRestoring': '已停止，正在恢复状态…', 'ap.noRunningSession': '没有正在运行的调优',
    'ap.stopFailed': '停止失败： ', 'ap.statusRefreshRetry': '状态刷新重试中，保留上一帧',
    'ap.bridgeUnreachable': 'host bridge 状态不可达', 'ap.statusUnreachable': 'status 不可达',
    'ap.copied': '✓ 已复制',
    'common.close': '关闭', 'common.copy': '复制',
    'live.tier1': '用户感知指标', 'live.tier1hint': '最近 60 秒 · 每 2s 刷新', 'live.tier2': '效率与诊断',
    'kpi.ttft': 'TTFT 平均', 'kpi.ttft.sub': '首 token 延迟', 'kpi.reqs': '请求',
    'kpi.tpot': 'TPOT 平均', 'kpi.tpot.sub': '每 token 间隔',
    'kpi.tput': 'Output 吞吐', 'kpi.tput.sub': '系统聚合', 'kpi.tput.perreq': '单请求',
    'kpi.running': '运行请求', 'kpi.waiting': '等待队列',
    'kpi.mfu': 'MFU', 'kpi.mfu.sub': '算力利用率',
    'kpi.gpuutil': 'GPU 利用率', 'kpi.gpuutil.sub': 'SM 忙碌',
    'kpi.vram': '显存占用', 'kpi.vram.sub': 'VRAM 容量', 'kpi.prefix': 'Prefix cache 命中',
    'kpi.preempt': '抢占 / 分钟',
    'tip.ttft': 'TTFT = Time To First Token\n从用户发送请求到收到第一个生成 token 之间的延迟。\n大数字 = 窗口内平均(典型水平);下方分布条 = p50→p95→p99(看尾部恶化,告警颜色按 p99)。\n解读：\n  · 主要由 prefill 阶段决定(要处理整个 prompt)\n  · 长 prompt / 高并发 / 排队都会拉高 TTFT\n  · 用户感觉的「卡了多久才有反应」就是这个\nSLA 常见档：<200ms 即时;<500ms 流畅;<1s 可接受;>2s 用户开始流失。',
    'tip.dist': '分位分布：每行一条,p99 = 满刻度。p95/p99 行越接近 p50 行 = 尾部越稳;p99 远长于 p50 = 尾延迟恶化。',
    'tip.tpot': 'TPOT = Time Per Output Token\n生成每个 token 的平均时间(每次 forward pass 的时长)。\n大数字 = 窗口内平均;下方分布条 = p50→p95→p99(尾延迟,告警颜色按 p99)。\n解读：\n  · 主要由 decode 阶段的带宽决定(要读完一遍权重)\n  · 用户感觉的「文字一个个吐出来的快慢」就是这个\n  · 1/TPOT = 单请求 token 速度(50ms TPOT = 20 tok/s)\nSLA 常见档：<30ms 流畅;<50ms 可接受;>100ms 明显卡顿。\nITL fallback:vllm <0.20 不发 TPOT 时,用 iter 间隔近似(语义略不同)。',
    'tip.tpotItl': '注意：当前 vllm 未发 TPOT,回退到 ITL 近似。',
    'tip.tput': 'Output 吞吐 = 系统每秒产出的 token 总数\n公式：窗口内 sum(gen_tokens) / 窗口秒数\n解读两个值各看不同的事：\n  · 系统聚合(大字)：所有并发请求合起来每秒吐多少 token —— 衡量容量、$/token\n  · 单请求(小字 = 1000/TPOT_p50)：单个用户感觉每秒吐多少 token —— 衡量打字速度\n两者关系：系统聚合 ≈ 单请求速度 × 并发数(理想情况下)。\n扩 batch 时系统聚合涨、单请求速度可能微跌 — 用户略卡但总产能高。',
    'tip.kv': 'KV cache 使用率 = 已分配 KV 块 / 总 KV 块\nvllm 把每个请求的注意力 K/V 缓存切成固定大小的 block 管理。\n解读：\n  · 这是显存里『装请求』的容量水位,跟前面『显存占用』不是一回事\n  · <50%：还能塞更多并发,扩 max_num_seqs 没风险\n  · >80%：接近极限,新请求要么排队要么抢占老请求 (preemption)\n  · >90%:preemption 频率会陡升,吞吐反而下降\n和 --gpu-memory-utilization 互动：那个参数决定 KV cache 总池子多大。',
    'tip.running': '运行中请求数 = 当前正在 forward pass 的请求数 (batch size)\nvllm 的『continuous batching』每个 iter 都可能改变这个数。\n解读：\n  · 衡量当前并发度,跟 TPS 一起看\n  · 远低于 max_num_seqs：吃不满,扩客户端并发\n  · 等于 max_num_seqs：满载,看是被吞吐限制还是被显存限制\n  · 跟 waiting_reqs 比较：running 上不去 + waiting 排队 = 显存瓶颈',
    'tip.waiting': '等待队列长度 = 已收到但没排上 running 的请求数\n显存不够或者 max_num_seqs 满了,新请求就堆在这里。\n解读：\n  · 长期 >0：消化速度跟不上进来速度 —— 加 GPU 或限流\n  · 突发 >0 后快速归零：偶发流量尖峰,正常\n  · 持续 >20：用户感觉 TTFT 飙升,是规则告警阈值',
    'tip.mfu': 'MFU = Model FLOPs Utilization\n实际算力 / GPU 峰值算力,衡量「跑模型时 GPU 算力真正被用了多少」。\n公式：MFU = (6 · params · tokens/sec) / peak_TFLOPS\n解读：\n  · 训练通常 30-55% 算良好(A100/H100 上)\n  · 推理 decode 阶段天然低(~1-5%)—— 受带宽限制,不是 MFU 越高越好\n  · prefill 阶段高(>30%)才是 MFU 真正有意义的时候\n依赖 vllm ≥0.20 的 perf_stats,当前 0.13 不发,这里会显示 —',
    'tip.gpuutil': 'GPU 利用率 = SM (Streaming Multiprocessor) 忙碌时间占比\nNVML 报的值,反映「过去采样间隔内,至少一个 kernel 在跑」的时间比例。\n注意：不代表算力用满了 —— decode 阶段常见 70-90% 但 MFU 只有 1-5%,\n因为 SM 在「跑 kernel 等数据」也算忙。\n真正衡量算力效率要看 MFU。',
    'tip.vram': '显存占用率 = 已用 VRAM / 总 VRAM\n权重 + KV cache + 激活值 + CUDA workspace 之和。\nvllm 启动时 --gpu-memory-utilization 决定权重+KV的目标占比(默认 0.9)。\n持续 >95% 容易触发 preemption / OOM。',
    'tip.prefix': 'Prefix cache 命中率 = 复用前缀的 KV 块 / 总查询块\nvllm 会缓存已经算过的 prompt 前缀的 KV,下次请求命中前缀可以跳过 prefill。\n多轮对话 / system prompt 固定的场景应该 >50%;一次性请求自然为 0。',
    'tip.padding': 'CUDA padding = (cudagraph 实际批量 - 真实 token) / cudagraph 批量\nvllm 用 CUDA graph 加速时按固定 batch size 跑,少的 token 用 padding 填。\npadding 越高浪费越大。>30% 说明 batch 大小档位选得不合适。',
    'tip.preempt': '抢占 / 分钟 = 每分钟有多少请求被 swap out\nKV cache 不够时 vllm 会把活跃请求 swap 到内存换其他请求进来。\n>0 说明显存紧张,>5/min 是明显信号 — 应当减小 max_num_seqs 或扩 VRAM。',
    'common.avg': '平均',
    'live.latTrends': '用户延迟趋势', 'live.latHint': '最近 5 分钟 · 实线 平均 / 浅线 p99',
    'lat.ttft': 'TTFT · 首 token 延迟', 'lat.tpot': 'TPOT · 单 token 生成时间', 'lat.e2e': 'E2E · 端到端延迟',
    'lat.noTtft': '暂无 TTFT 数据', 'lat.noTpot': '暂无 TPOT / ITL 数据',
    'lat.noE2e': '暂无 E2E 数据', 'lat.noE2eHint': '(跑一次 bench 触发请求完成事件)',
    'lat.itlSource': '数据源：ITL fallback(当前 vllm 没发 TPOT 字段,用 iter 间隔近似)',
    'roof.title': 'Roofline 实时诊断',
    'roof.desc': '点 = 相近 step 聚合(越大 = 步数越多)· 看离上界还有多远,判断 compute-bound 还是 memory-bound',
    'roof.estimate': '⚠ 估算数据 · 当前 vllm 不发 perf_stats,按 token 计数 + 模型参数推算({b}B 参数)。',
    'roof.estimateNote': '点形状对,绝对值有误差;升 vllm ≥0.20 自动切回实测。',
    'roof.sample': '当前样本', 'roof.verdict': '当前结论',
    'roof.computeUtil': '算力利用', 'roof.bwUtil': '带宽利用',
    'roof.speedup': '提速方向', 'roof.noSamples': '没有样本可解读 — 跑一次 bench 触发数据。',
    'roof.foot': '中位 AI={ai} · 拐点={knee} · 样本={n}',
    'roof.footTip': '样本数={n}  AI 中位={ai}  knee={knee}',
    'roof.inMemBound': '位于 memory-bound 区', 'roof.inCmpBound': '位于 compute-bound 区',
    'startup.btn': '启动信息', 'startup.title': 'vLLM 启动信息',
    'startup.cmdline': '启动命令', 'startup.noCmdline': '未捕获到 cmdline(plugin 早于 sys.argv 设定?)',
    'startup.env': '环境变量', 'startup.noEnv': '无相关环境变量(仅含 VLLM_/PPING_LANG_/HF_/CUDA_/TORCH_ 等前缀)',
    'startup.resolved': 'vLLM 解析配置', 'startup.resolvedSub': 'CLI + 默认值合并后的最终生效值',
    'startup.noConfig': '无 vllm_config(plugin 实例化时未拿到,常见于本地 demo)',
    'startup.masked': '名称含 TOKEN/KEY/SECRET,值已脱敏',
    'kern.profileTitle': 'Kernel 级 Profiling',
    'kern.profileHint': 'PC Sampling 采样实测 · 每个 kernel 占多少 GPU 时间 + 为什么慢（无需 Nsight、按需短窗）',
    'kern.rooflineTitle': 'Roofline · 宏观定位',
    'kern.rooflineSubtitle': '整模型在屋脊线的位置（与 Overview 同数据）',
    'kern.rooflineHint': '点 = 相近 step 聚合（越大 = 步数越多）· 离上界多远 = 还有多少优化空间 · 配合下面 PC Sampling 定位具体 kernel 及原因',
    'kern.howComputed': '屋脊线怎么算的',
    'kern.rooflineFrom': '全部从 GPU 现读 CUDA 设备属性',
    'kern.kneeDesc': '拐点左 = 访存受限,拐点右 = 算力受限;点 y = 实测吞吐 TFLOPs/s,x = FLOPs/字节',
    'kern.scalingAnalysis': 'Scaling 分析',
    'kern.scalingSweepBtn': '▶ 实测 scaling 曲线(压测扫并发 1/4/16/64,约 2 分钟)',
    'kern.scalingSweepInProgress': '压测中…',
    'kern.scalingSweepNote': '压测流量打到本机 vLLM,期间面板数据会受压测影响',
    'kern.scalingSweepError': '压测失败：{err}',
    'kern.scalingVerdict': '📏 实测 scaling 结论',
    'kern.verdictChart': '图中实心绿线 = 实测;虚线 = 理论 envelope',
    'kern.kernelTimePct': '每个 Kernel 的 GPU 时间占比',
    'kern.kernelSampling': 'PC Sampling 采样 · 按占比降序',
    'kern.kernelHint': '采样命中数 ∝ GPU 活跃时间 → 每个 kernel 占用多少 GPU 时间（采样估计,非精确 μs）· 并给出其主导的 stall 原因 ·\n基于最近一次取证（共 {n} 样本）',
    'kern.recollect': '重新采集',
    'kern.collecting': '采集中…',
    'kern.byKernelClass': '按算子类型 · 占 GPU 时间',
    'kern.gpuUtilDiag': 'GPU 利用诊断',
    'kern.stallDesc': '{stall}% 的采样周期在等待 (stall),仅 {issued}% 在真正发射指令',
    'kern.sourceHotspots': '🔬 源码级热点',
    'kern.sourceDesc': 'PC 样本精确落到 Python 源码行(Triton/自编译 kernel,带 lineinfo)',
    'kern.sourceTimePct': '这些 kernel 合计占 GPU 时间 {pct}% ——\n本负载主导热点在闭源 cutlass/cuBLAS GEMM(下表,只能到 SASS 偏移);能定位到源码的是下面这些 Triton kernel。',
    'kern.sourceTimePctHigh': '占比可观,源码级定位直接可行动。',
    'kern.expandKernelDetail': '点任意行展开 → 看该 kernel 的完整 stall 构成 + 优化建议',
    'kern.kernelClass': '类',
    'kern.kernelGpuTime': 'GPU 时间占比',
    'kern.kernelStallRecoverable': 'stall 时间 可回收',
    'kern.kernelStallTitle': '这个 kernel 的 stall 构成（占它自己的样本）',
    'kern.kernelDeepHotspot': '最深热点',
    'kern.kernelHotspotDesc': 'PC sampling 落到指令地址(stall 样本集中在哪)',
    'kern.sourceFile': '源文件：{path}',
    'kern.closedLibKernel': '闭源库 kernel(无 lineinfo)→ 给到 SASS 指令偏移级热点：',
    'kern.launchOrigin': '↗ 启动来源',
    'kern.launchStack': 'launch 栈,向外归因到调用它的 host 代码',
    'kern.preciseMicros': '如需逐 kernel 的精确 μs 耗时,需 CUPTI Activity 模式 —— 与 PC Sampling 共用同一套性能计数硬件、二者互斥,需单独部署。下方 Deep Evidence 是同一次采样的全局 stall 分解。',
    'kern.collectingPcSampling': '正在采集 PC Sampling 证据…(约 5s,稍候自动出表)',
    'kern.noPcSamplingData': '打开本页会自动采集一次;或点下方 Deep Evidence 的「采集 stall 证据」。',
    'kern.dataFreshness': '实时',
    'kern.aggregated': '每 {w}s 聚合一次,当前数据采集于 {when}',
    'kern.noActivity': '⏸ 当前无 GPU 活动 —— 下面是 {when} 最后一次有 kernel 运行时(最近 {w}s 窗口)的数据,不是当前值',
    'kern.findings': '诊断结论',
    'kern.gpuBusy': 'GPU busy',
    'kern.wallClockShare': '占墙钟',
    'kern.launchFreq': 'Kernel 启动频率',
    'kern.meanDuration': '平均 kernel 时长',
    'kern.inCudaGraph': 'CUDA Graph 内',
    'kern.memcpy': 'memcpy',
    'kern.syncWait': '同步等待',
    'kern.classTrend': 'kernel 类占比 · 实时趋势',
    'kern.last3Min': '最近 ~3 分钟',
    'kern.stackedArea': '堆叠面积 = 各类 kernel 占 GPU 计算时间随时间变化',
    'kern.utilTrend': 'GPU 利用 vs 等待 · 实时趋势',
    'kern.utilNote': 'GPU busy 高 + 同步等待低 = 健康;等待显著升高 = launch-bound',
    'kern.timeline': '执行时间线',
    'kern.timelineSpan': '最近 {n} 个 kernel · 跨度 {span} ms',
    'kern.timelineHint': 'x=时间 · 行=GPU stream · 块=kernel(宽∝耗时) · 空白=GPU 空闲 · 点 ＋ 放大、拖滚动条平移',
    'kern.exportTrace': '⬇ 导出 trace',
    'kern.perfettoInst': '拖进 ui.perfetto.dev 看(Nsight / PyTorch 同款格式)· 下方为页内预览',
    'kern.zoomOut': '缩小',
    'kern.fitWidth': '适应',
    'kern.zoomIn': '放大(围绕选中块)',
    'kern.selected': '▸ 选中 {name}(点 ＋ 放大它)',
    'kern.selectedDetail': '选中 kernel 详情',
    'kern.deselect': '取消选中',
    'kern.duration': '耗时',
    'kern.startTime': '起始',
    'kern.stream': 'stream',
    'kern.inGraph': 'CUDA Graph 内',
    'kern.yes': '是',
    'kern.no': '否',
    'kern.rawKernelDetail': '原始 Kernel 明细',
    'kern.unique': '去重 {n} 种 kernel',
    'kern.kernelNameNote': '本窗口出现的不同 kernel 名数量(去重)。模型每步跑同一套 kernel,所以通常稳定;换模型/配置会变。上限 100。',
    'kern.allValuesWindow': '所有数值均为最近一个聚合窗口（约 {w}s）内的量,每窗刷新',
    'kern.kernelNameRaw': 'Kernel 名（原始）',
    'kern.calls': '调用',
    'kern.totalTime': '总耗时',
    'kern.average': '平均',
    'kern.share': '占比',
    'kern.graphShare': 'Graph',
    'kern.noKernelDetail': '暂无 per-kernel 明细（需注入式采集器在采,或本窗无 kernel）',
    'kern.showAll': '展示全部 {n} 个 ▾',
    'kern.collapse': '收起 ▴',
    'kern.overhead': '采集开销 {overhead} ms/窗（守 5% 预算）',
    'kern.dropped': '丢弃 {n} 条（已自动降级）',
    'kern.deepEvidence': '🔬 Deep Evidence — 为什么慢',
    'kern.deepEvidenceSub': 'PC Sampling 取证 · 按需短窗',
    'kern.deepHint': '上方定位"哪个 kernel";这里看整体：warp 周期的去向、全局主要 stall 原因、以及这些数据的测量方式',
    'kern.collectEvidence': '采集 stall 证据(5s)',
    'kern.unavailable': 'PC Sampling 取证不可用 — {err}\n需 Linux + libppingcupti.so + 放开 GPU 性能计数器权限;与 torch 同进程需 1b 注入式(见设计文档 §12)。',
    'kern.warpCycleDirection': 'Warp 周期去向',
    'kern.allSamples': '占全部 {n} 样本',
    'kern.issued': '发射指令(有效执行)',
    'kern.slack': '就绪未选中(占用率有余量)',
    'kern.stallWait': '真 stall(在等)',
    'kern.stallAnalysis': '→ 大量周期处于真 stall：延迟瓶颈,详见下方 stall 构成',
    'kern.slackAnalysis': '→ 有就绪 warp 没被选中：占用率充足,瓶颈不在并行度',
    'kern.issuedAnalysis': '→ 发射占比较高：GPU 比较忙碌',
    'kern.stallBreakdown': 'stall 分解',
    'kern.stallBreakdownSub': '占 stall 样本(= 全部 − issued)· 点行看原始硬件指标名',
    'kern.howMeasured': '测量方式',
    'kern.samplingPeriod': '采样周期 每 {period} cycle 一次(2^{log}) ·\n本窗 {w}s 采到 {samples} 样本 ·\n GetData 累计开销 {getdata}ms ·\n丢样 {dropped} · HW 缓冲满 {hwfull} 次 ·\nGPU 硬件采样,无需 Nsight、不停服务',
    'kern.noInitialData': '点击上方按钮启动一次短窗 PC Sampling,查看这些 kernel 内部的 stall 类型(访存依赖 / 计算管线 / 同步 …)。',
    // #5 comm 桶细分
    'csub.title': '通信细分',
    'csub.hint': '占通信总时间 · allreduce 常是延迟型,all_gather / reduce_scatter 常是带宽型,优化手段不同',
    'csub.ofTotal': '全局 {pct}%',
    // #6 Kernel 快照 A/B 对比(改 kernel 前后验证)
    'ksnap.saveA': '存为快照 A',
    'ksnap.saveB': '存为快照 B',
    'ksnap.slotA': '快照 A(改动前)',
    'ksnap.slotB': '快照 B(改动后)',
    'ksnap.clear': '清除',
    'ksnap.savedAt': '{time} 采集',
    'ksnap.title': '📐 改动前后对比',
    'ksnap.hint': 'Δ = B 相对 A;绿 = 更好,红 = 更差',
    'ksnap.needBoth': '存下 A(改动前)和 B(改动后)两个快照后,这里显示逐 kernel 的差异。',
    'ksnap.loadContext': '负载对照',
    'ksnap.loadSame': '典型 kernel 变化 {median}(中位数),说明变化集中在少数 kernel 上而非全体平移 —— 两次负载可比,下面的绝对 Δ 可信。整体 GPU 活跃度 {total}。',
    'ksnap.loadDrift': '⚠ 典型 kernel 变化 {median}(中位数)—— 几乎所有 kernel 都同向平移了,这更像是两次取样时的流量/负载不同,而不是某个改动生效。下面的"绝对速率 Δ"会被这个差异污染,建议在相同负载下重采。整体 GPU 活跃度 {total}。',
    'ksnap.periodMismatch': '⚠ 两次快照的采样周期不同(2^{a} vs 2^{b}),已按周期归一化后再比,但仍建议用同一周期重采。',
    'ksnap.stallDiff': '全局 stall 构成变化',
    'ksnap.stallDiffSub': '百分点差(B − A)· 下降 = 该类 stall 减少',
    'ksnap.warpDiff': 'Warp 周期去向变化',
    'ksnap.kernelDiff': '逐 kernel 差异',
    'ksnap.kernelDiffSub': '按 GPU 时间占比排序 · 只列前 25',
    'ksnap.colRate': '绝对速率 Δ',
    'ksnap.colRateTip': '该 kernel 每墙钟秒消耗的 GPU 周期(已按采样周期归一化)。这是"这个 kernel 真的变快了吗"的答案 —— 前提是两次负载相同。',
    'ksnap.colShare': 'GPU 时间占比',
    'ksnap.colShareTip': '占比是相对值：单个 kernel 变快会自动抬高其它所有 kernel 的占比。判断"变没变快"请看绝对速率 Δ。',
    'ksnap.colStall': '主导 stall',
    'ksnap.new': '新增',
    'ksnap.gone': '消失',
    'ksnap.shareCaveat': '注意：占比是相对值 —— 优化掉一个 kernel 会让其余所有 kernel 的占比"看起来变差"。要判断某个 kernel 自身有没有变快,看绝对速率 Δ。',
    'bench.createTitle': '新建压测',
    'bench.createHint': '提交后异步执行，结果落库后出现在下方历史',
    'bench.name': '名称（可选）',
    'bench.namePlaceholder': 'adhoc-时间戳',
    'bench.api': 'API',
    'bench.endpoint': 'Endpoint',
    'bench.endpointHint': 'vLLM 服务的 base URL，自动追加 /v1/...',
    'bench.modelName': '调用名',
    'bench.modelNameSub': '(served-model-name)',
    'bench.modelNameHint': '写进 OpenAI 请求 {"model": "..."} 的字符串，必须匹配 vLLM 启动时的 --served-model-name。与磁盘上权重路径无关。',
    'bench.promptSource': 'Prompt 数据源',
    'bench.promptTokens': 'Prompt tokens',
    'bench.outputTokens': 'Output tokens',
    'bench.concurrency': 'Concurrency',
    'bench.measureMode': '测量模式',
    'bench.measureByDuration': '按时长',
    'bench.measureByRequests': '按请求数',
    'bench.duration': 'Duration（秒）',
    'bench.numRequests': 'Num requests',
    'bench.warmup': 'Warmup（秒）',
    'bench.timeout': 'Timeout（秒）',
    'bench.sloConstraint': 'SLO 约束（可选）',
    'bench.sloShapeHint': '选形态带出三项 p99 默认值,可单独清空跳过该项',
    'bench.sloOptional': '可选',
    'bench.sloPreviewLabel': '提交时生成：',
    'bench.sloPreviewEmpty': '(空,提交后 SLO 状态标记为 n/a)',
    'bench.submitBtn': '开始运行',
    'bench.submitting': '提交中…',
    'bench.running': '正在运行',
    'bench.historyTitle': '历史记录',
    'bench.historyHint': '最近 50 条',
    'bench.emptyTitle': '暂无压测记录',
    'bench.emptyHint': '填上方表单点「开始运行」即可',
    'bench.compareTitle': '结果对比',
    'bench.compareHint': 'Δ = B 相对 A(基准);绿 = 更好,红 = 更差,|Δ|<2% 视为持平',
    'bench.clearCompare': '清除对比',
    'bench.metricTtftAvg': 'TTFT 平均',
    'bench.metricTpotAvg': 'TPOT 平均',
    'bench.metricTokPerSec': 'tok/s',
    'bench.metricOkErr': 'ok / err',
    'bench.resultError': '错误：',
    'bench.clientMetrics': '客户端指标',
    'bench.scenario': '场景',
    'chart.currentSamples': '当前样本',
    'chart.measuredScaling': '实测 scaling',
    'chart.samplesAgg': '合并 {n} 个 step',
    'chart.measuredConcurrency': '实测 并发{b}: {y} TFLOPs/s',
    'chart.theoreticalEnvelope': '理论 envelope: {e} TFLOPs/s',
    'chart.gap': '缺口： {g}%',
    'chart.kclassComm': '通信',
    'chart.kclassOther': '其它',
    'chart.syncWait': '同步等待 (launch-bound)',
    'bench.agoSeconds': '{s} 秒前',
    'bench.agoMinutes': '{m} 分钟前',
    'bench.agoHours': '{h} 小时前',
    'bench.agoDays': '{d} 天前',
    'bench.ttftAvg': 'TTFT 平均',
    'bench.tpotAvg': 'TPOT 平均',
    'bench.e2eAvg': 'E2E 平均',
    'bench.outputThroughput': 'Output 吞吐',
    'bench.completionErrors': '完成 / 错误',
    'bench.submitError': '提交失败： {e}',
    'bench.submitException': '错误： {e}',
    'bench.concurrencyLabel': '并发 {c} · {p}/{o} tok · {l}',
    'bench.promptSourceSynthetic': '合成填充 (synthetic)',
    'bench.promptSourceDesc': '按 prompt_tokens 长度循环 the quick brown fox 句模板',
    'toast.saveFailed': '保存失败： {e}',
    'toast.error': '错误： {e}',
    'toast.saveApplied': '已保存,热生效',
    'toast.savePending': '已保存(引擎未运行,重启后生效)',
    'kernel.fresh': '刚刚',
    'kernel.agoSeconds': '{s} 秒前',
    'kernel.agoMinutes': '{m} 分钟前',
    'kernel.scaling.progress': '启动中…',
    'kernel.scaling.testing': '压测中…',
    'kernel.pcSamplingUnavailable': 'PC Sampling 不可用',
    'kernel.requestFailed': '请求失败： {e}',
    'kernel.traceUnavailable': '暂无 trace 数据(需 CUPTI 采集器在采集)',
    'roofline.memoryBound': 'Memory-bound（LLM decode 阶段的常态）',
    'roofline.memSugg1': '增大 batch 直到 KV cache 接近 80% — 摊薄权重 re-read',
    'roofline.memSugg2': '启用 speculative decoding — 减少 decode 步数',
    'roofline.memSugg3': '权重量化 (AWQ / GPTQ) — 直接减小要读的字节数',
    'roofline.memSugg4': '升级带宽更高的卡（你当前 {bw} GB/s；H100 3.4 TB/s，H200 4.8 TB/s）',
    'roofline.computeBound': 'Compute-bound（prefill 或大 batch 状态）',
    'roofline.compSugg1': '继续增大 batch 收益递减 — 算力已接近上限',
    'roofline.compSugg2': '升级算力更高的 GPU 或上 tensor parallel',
    'roofline.compSugg3': 'Chunked prefill — 拆开长 prompt 让 decode 喘息',
    'kernel.bottleneck.memDep': '访存瓶颈',
    'kernel.bottleneck.memDepAction': '数据正在等待内存加载。可尝试 fp8/int8 量化以减少访存、算子融合以减少往返开销、确认 KV cache 复用。',
    'kernel.bottleneck.memThrottle': '访存带宽瓶颈',
    'kernel.bottleneck.memThrottleAction': '内存子系统已饱和。降低精度 / 融合算子以减少访存流量。',
    'kernel.bottleneck.mathPipe': '算力瓶颈',
    'kernel.bottleneck.mathPipeAction': '计算单元接近饱和(正常,已高效运行)。进一步优化需更低精度或更优 kernel。',
    'kernel.bottleneck.execDep': '指令延迟瓶颈',
    'kernel.bottleneck.execDepAction': '指令间数据依赖等待,多由 kernel 内部结构决定,优化空间有限。',
    'kernel.bottleneck.sharedDep': '共享内存瓶颈',
    'kernel.bottleneck.sharedDepAction': '等共享内存 / L1。检查 tile 大小与 bank conflict。',
    'kernel.bottleneck.sync': '同步瓶颈',
    'kernel.bottleneck.syncAction': '线程在 barrier 等待。检查同步频率与负载均衡。',
    'kernel.bottleneck.fetchCtrl': '前端取指瓶颈',
    'kernel.bottleneck.fetchCtrlAction': '指令获取 / 分支,一般非主因。',
    'kernel.bottleneck.dispatch': '发射瓶颈',
    'kernel.bottleneck.dispatchAction': '发射端口受限。',
    'kernel.meaning.memDep': '等全局/本地内存的数据返回(long scoreboard)',
    'kernel.meaning.sharedDep': '等共享内存 / L1(short scoreboard)',
    'kernel.meaning.memThrottle': '访存指令排队、内存子系统饱和',
    'kernel.meaning.mathPipe': '计算管线忙(Tensor / ALU / FMA),接近算力上限',
    'kernel.meaning.execDep': '等前一条指令的结果(指令间依赖)',
    'kernel.meaning.sync': '在 barrier / membar 等其他线程',
    'kernel.meaning.fetchCtrl': '等取指 / 分支决议',
    'kernel.meaning.dispatch': '发射端口受限',
    'kernel.meaning.schedulerSlack': '有就绪 warp 但本周期没被选中(占用率有余量,非瓶颈)',
    'kernel.meaning.other': '其它 / 杂项',
    'kernel.label.memDep': '访存依赖',
    'kernel.label.sharedDep': 'shared/MIO 依赖',
    'kernel.label.memThrottle': '访存子系统压力',
    'kernel.label.mathPipe': '计算管线',
    'kernel.label.execDep': '执行依赖',
    'kernel.label.sync': '同步',
    'kernel.label.fetchCtrl': '取指/控制流',
    'kernel.label.dispatch': '调度分发',
    'kernel.label.schedulerSlack': '调度余量(非瓶颈)',
    'kernel.label.other': '其它',
    'kernel.suggestion.gemmMem': '访存瓶颈的矩阵乘：fp8/int8 量化、增大 batch 提升计算密度、检查权重是否反复从显存读取。',
    'kernel.suggestion.gemmMath': '矩阵乘已算力饱和(接近峰值),难再压;考虑更低精度。',
    'kernel.suggestion.attnMem': '注意力访存瓶颈：确认 FlashAttention / PagedAttention 生效、KV cache 命中率。',
    'kernel.suggestion.elementwise': '逐元素 / 拷贝：看能否算子融合,减少 kernel 数与显存往返。',
    'kernel.suggestion.sampling': '采样 / 解码开销：批量解码、减少不必要的 host-device 往返。',
    'kernel.suggestion.index': '索引 / 查表：确认访问模式连续,避免随机 gather 打散访存。',
    'kernel.suggestion.execDep': '指令延迟为主,通常由 kernel 内部结构决定,优化空间有限。',
    'ui.copy': '复制',
    'ui.copied': '已复制 ✓',
    'ui.copyFailed': '复制失败',
    'cleanup.computeRoofFormula': 'TFLOPS（= SM数 × SM时钟 × 架构 bf16 Tensor 吞吐）',
    'cleanup.memoryRoofFormula': 'GB/s（= 显存时钟 × 位宽 × 2）',
    'cleanup.knee': '拐点',
    'cleanup.scalingEff': '（扩展效率 {pct}%）',
    'cleanup.issuedTitle': '发射指令 {pct}%',
    'cleanup.stallTitle': 'stall {pct}%',
    'cleanup.mainCause': '主因',
    'cleanup.ofStall': '（占 stall 的 {pct}%）',
    'cleanup.topRecoverablePre': '🎯 最大可回收点：',
    'cleanup.topRecoverableMid': '—— 全局约',
    'cleanup.topRecoverablePost': '的 GPU 时间是它在等待,优先优化它。',
    'cleanup.dominantStall': '主导 stall',
    'cleanup.lineinfoPre': '想到源码行需 kernel 带',
    'cleanup.lineinfoPost': '(Triton/自编译默认带);cutlass/cuBLAS 闭源到此为止 —— 偏移 + kernel 名(tile/dtype)已足够定位是哪段在卡。',
    'cleanup.collapse': '收起 ▴',
    'cleanup.showAllKernels': '展示全部 {n} 个 ▾',
    'cleanup.pcSamplingUnavailable': 'PC Sampling 取证不可用 —— {err}',
    'cleanup.frozen': '⏸ 已冻结',
    'cleanup.liveTl': '🔴 实时',
    'cleanup.fit': '适应',
    'cleanup.cuptiTimelineEmpty': 'CUPTI 执行时间线暂无数据(需采集器在采集 kernel)。',
    'cleanup.rawKernelPre': '真实 mangled 名 + GPU 硬件实测耗时',
    'cleanup.rawKernelPost': ',按占比降序',
    'cleanup.warpIssuedTitle': '发射指令 {pct}%',
    'cleanup.warpSlackTitle': '就绪未选中 {pct}%',
    'cleanup.warpStallTitle': '真 stall {pct}%',
    'cleanup.samplesCount': '{n} 样本',
    'cleanup.aBaseline': 'A(基准)',
    'cleanup.deltaBetter': ' ↑优',
    'cleanup.deltaWorse': ' ↓劣',
    'cleanup.deltaEven': ' ≈持平',
    'cleanup.statusRunning': '运行中',
    'cleanup.statusDone': '已完成',
    'cleanup.statusFailed': '失败',
    'cleanup.sloPass': 'SLO 通过',
    'cleanup.sloFail': 'SLO 失败',
    'cleanup.compareSlot': '对比 {slot}',
    'cleanup.compare': '对比',
    'cleanup.autoRefresh': '每 2s 自动刷新',
    'cleanup.envelopeLabel': 'B={b}: bandwidth-bound 上界 {y} TFLOPs/s',
    'cleanup.measured': '实测',
    'cleanup.dominatedBy': '{label} 为主',
    'rule.regime-classify.name': '算术强度 vs 脊点(Roofline 定位)',
    'rule.regime-classify.hypothesis': 'AI < 脊点 → 访存受限;否则计算受限(定义性派生)。memory-bound 且 SM util 高时：util 虚高是物理极限,非优化空间。',
    'rule.A.name': '双低(算力、带宽均有余量)', 'rule.A.hypothesis': '存在在途请求,但算力与带宽两个上限均未逼近 —— 二者皆有余量,瓶颈不在硬件(可能为批处理规模不足 / kernel launch 开销 / 小算子未融合)。空载守卫：无在途请求时不触发,以区分"双低"与"纯空载"。', 'rule.A.suggestion': '提高 max-num-seqs(并发未填满)/ 调整 chunked-prefill 的 partial-prefills。注：Continuous Batching、CUDA Graph、chunked-prefill 在 0.21 默认启用,请先确认未被 enforce-eager 等关闭,避免重复启用(无效操作)。',
    'rule.B.name': '带宽瓶颈(逼近显存带宽上限)', 'rule.B.hypothesis': '访存受限：decode 每步重新读取权重与 KV,带宽为上限。NVML HBM 控制器占用 / 内核 memory_throttle(访存管线饱和)/ memory_dependency(等待访存)交叉印证。(注：perf 实测 MBU 在小模型上因 L2 复用 >1,故不作为阈值。)', 'rule.B.suggestion': '投机解码(关注接受率,避免转为计算受限)/ KV 量化(FP8)/ 升级至更高带宽 GPU。',
    'rule.C.name': '算力瓶颈(算力饱和)', 'rule.C.hypothesis': '计算受限：FLOPs 饱和,算力为上限(长 prompt prefill 的固有特征)。实测 MFU 逼近上限 / 内核 math_pipe(FMA/ALU/Tensor 计算管线饱和)交叉印证。', 'rule.C.suggestion': '更换更快的 attention backend(0.21 按硬件自动选择,可用 --attention-backend 覆盖)/ 权重量化(FP8/FP4)/ 升级算力更强的 GPU。',
    'rule.D.name': '容量瓶颈(KV 耗尽并触发抢占)', 'rule.D.hypothesis': '显存无法容纳 KV → 并发受限 → 触发抢占。V1 抢占为纯重算(丢弃 KV、从头 re-prefill),一旦发生,decode 吞吐急剧下降。', 'rule.D.suggestion': 'KV 量化(FP8)/ 降低 max-model-len / KV offload / 降低 max-num-seqs。',
    'rule.A.detector.roofline': '算力/带宽双低(MFU 低 + HBM 控制器空闲)',
    'rule.A.detector.kernel_slack': '内核 scheduler_slack(warp 就绪未发射)',
    'rule.B.detector.hbm_busy': 'HBM 控制器占用(NVML mem-util)',
    'rule.B.detector.kernel_throttle': '内核访存管线 throttle(LSU 饱和)',
    'rule.B.detector.kernel_memdep': '内核访存延迟(long_scoreboard 等待访存)',
    'rule.C.detector.measured_mfu': '实测 MFU 逼近上限',
    'rule.C.detector.kernel_mathpipe': '内核算力管线饱和(FMA/ALU/Tensor)',
    'rule.D.detector.kv_pressure': 'KV 池接近耗尽',
    'rule.D.detector.preemption': '已发生抢占',
    'decode.vllmFusedRms': 'vLLM 自定义 · fused add + RMSNorm', 'decode.vllmRms': 'vLLM 自定义 · RMSNorm',
    'decode.vllmRope': 'vLLM 自定义 · RoPE', 'decode.vllmSilu': 'vLLM 自定义 · SiLU/激活',
    'decode.vllmCuda': 'vLLM 自定义 CUDA kernel', 'decode.flashinferSample': 'FlashInfer · 采样 kernel',
    'decode.cublasGemv': 'cuBLAS GEMV(矩阵×向量,小 batch 典型)',
    'decode.torchNative': 'PyTorch · 原生 elementwise/reduce kernel',
    'decode.cppCompiled': 'C++ 编译 kernel(闭源/无 lineinfo)',
    'lang.label': '语言 / Language',
  },
  en: {
    'nav.live': 'Live', 'nav.kernel': 'Kernel', 'nav.rules': 'Bottleneck diagnosis', 'nav.bench': 'Bench', 'nav.autopilot': 'Autopilot', 'nav.preview': 'beta',
    'brand.sub': 'vLLM perf diagnostics',
    'btn.issue': 'Issue', 'btn.star': 'Star',
    'hero.info': 'View vLLM launch command, env vars, resolved config',
    'common.save': 'Save', 'common.saving': 'Saving…', 'common.reset': 'Reset',
    'common.suggestion': 'Suggestion', 'common.inference': 'Inference', 
    'cfg.hintActive': 'Edit once — every curated rule referencing it follows; saved changes hot-reload, no restart.',
    'cfg.hintInactive': 'Engine not running; saving only validates, takes effect after restart.',
    'cfg.mbu_high': 'HBM-busy near-roof (NVML%)', 'cfg.mbu_low': 'HBM-busy low threshold (NVML%)',
    'cfg.mfu_low': 'MFU low threshold', 'cfg.mfu_high': 'MFU saturation threshold',
    'cfg.kv': 'KV pressure threshold', 'cfg.min_running': 'Idle guard: min running reqs',
    'cfg.stall_mem': 'Kernel memory-throttle threshold', 'cfg.stall_memdep': 'Kernel memory-dependency threshold', 'cfg.stall_math': 'Kernel math-pipe threshold',
    'slo.form': 'Workload form', 'slo.thresholds': 'Bottleneck thresholds', 'slo.pass': 'Met', 'slo.fail': 'Breached',
    'shape.chat': 'Chat', 'shape.rag': 'RAG Q&A', 'shape.agent': 'Agent', 'shape.reasoning': 'Long reasoning',
    'shape.code': 'Code completion', 'shape.custom': 'Custom', 'ap.customLoadNote': 'Fully manual (keeps old defaults)',
    'slo.benchHint': 'To verify a tuning gain, compare A/B on the bench.', 'slo.benchBtn': 'Go to bench →',
    'rules.staleHint': 'None active — showing the latest hit',
    'rules.clickExpand': 'Click to expand details + evidence',
    'rules.builtinTitle': 'Built-in diagnoses · 4 bottlenecks', 'rules.colBottleneck': 'Bottleneck', 'rules.colStatus': 'Status', 'rules.colMethods': 'Methods (cross-layer)',
    'rules.miss': 'Clear', 'rules.hitN': 'Hit {n}/{m} methods', 'rules.detectors': 'Detection methods ({n} independent, cross-layer)', 'rules.hitHint': '{n} bottleneck(s) hit (last 5 min, deduped)', 'rules.noneHint': 'No bottleneck active — all good',
    'layer.L1': 'L1 measured roofline (perf_stats: MFU/MBU)', 'layer.L2': 'L2 kernel stall (CUPTI PC sampling)', 'layer.L3': 'L3 scheduler state (vLLM native, always on)', 'layer.L4': 'L4 request latency', 'layer.L5': 'L5 GPU hardware (NVML)',
    'rules.opTitle': 'Operating point · distance to each wall', 'rules.opHint': 'Measured; ticks = low/high thresholds. Hugging a roof = that bottleneck fires',
    'rules.computeRoof': 'Compute roof · MFU', 'rules.bwRoof': 'Bandwidth roof · HBM busy (NVML)', 'rules.capacity': 'Capacity · KV usage',
    'rules.roofC': 'at roof → compute bottleneck', 'rules.roofB': 'at roof → bandwidth bottleneck', 'rules.roofD': 'near full → capacity bottleneck',
    'rules.zone.far': 'headroom', 'rules.zone.mid': 'climbing', 'rules.zone.wall': 'at the wall', 'rules.zone.na': 'no data',
    'rules.runningNow': 'Running reqs', 'rules.guardArmed': 'has work (under-utilized can fire)', 'rules.guardIdle': 'idle (guard active, under-utilized suppressed)',
    'rules.layersTitle': 'Measurement layers · what L1–L5 mean', 'rules.layersHint': 'Each bottleneck triangulated by independent methods across layers — more independent = less likely to fail together (graceful degradation)',
    'rules.toAutopilotHint': 'Auto-tune from these bottlenecks? Hand off to Autopilot for sandboxed iterative tuning, bench-verified (preview).', 'rules.toAutopilotBtn': 'Hand off to Autopilot →',
    'regime.A': 'Under-utilized', 'regime.B': 'Bandwidth bottleneck', 'regime.C': 'Compute bottleneck', 'regime.D': 'Capacity bottleneck', 'regime.N': 'Symptom/other',
    'ap.realMode': 'Real mode: Autopilot stops the primary serve on the runw host to free the GPU → boots a candidate vLLM sandbox → runs the bench → kept/reverted → restores the primary serve. If no host bridge is connected, the start button errors instead of silently falling back to a simulation.',
    'ap.localMode': "Local/dev mode: no host bridge is connected, so starting runs SimSandbox to exercise the loop; visiting from the runw dashboard address is what triggers a real DockerSandbox tuning run.",
    'ap.runningNote1': 'Tuning in progress: the primary serve has handed off to the candidate sandbox — <b>this page (:8765) can\'t open new tabs right now; please don\'t refresh</b>. This page streams directly from the host bridge and is unaffected; backup live page:',
    'ap.runningNote2': '. The primary serve and this page recover automatically once tuning ends or you click "Stop".',
    'ap.agentTitle': 'Agent (tuning-decision LLM)', 'ap.notConfigured': 'Not configured',
    'ap.presetLabel': 'Preset', 'ap.presetLocal': 'Local (Ollama / vLLM)', 'ap.presetCustom': 'Custom',
    'ap.testConnection': 'Test connection', 'ap.advanced': 'Advanced',
    'ap.agentNote': 'OpenRouter / OpenAI / DeepSeek / Moonshot use an <b style="color:var(--coral-strong)">OpenAI-compatible</b> endpoint; Kimi Coding uses the Kimi Coding Messages API. Real tuning requires an LLM agent to be configured; StubAgent is for local SimSandbox/dev tests only and can\'t drive real GPU tuning. The key is used only within this session to call the agent, <b>never enters the diagnosis hot path or gets persisted with the trace</b>; production deployments should inject it via environment variables. Note: <b>don\'t use the vLLM model under test as the agent</b> (it\'s under load and may be too small).',
    'ap.guidanceLabel': 'Extra guidance (appended after the system prompt — business constraints / preferences)',
    'ap.guidancePlaceholder': 'e.g. keep gpu-util under 0.85 on this cluster; prefer chunked-prefill over raising concurrency; latency-sensitive workload, prioritize TTFT…',
    'ap.timeoutS': 'Timeout (s)', 'ap.retries': 'Retries',
    'ap.actionScope': 'Action scope · full vLLM surface − identity (~7) − hardware-narrowed − precision-lowering',
    'ap.denyModel': 'model / host / port (identity, ~7)', 'ap.denyParallel': 'tensor / pipeline-parallel (narrowed by GPU count)',
    'ap.denyQuant': 'quantization (lowers precision, not offered)', 'ap.denyKvDtype': 'kv-cache-dtype (lowers precision, not offered)',
    'ap.actionScopeNote': 'Most of these ~250 flags are already self-tuned to optimal by vLLM at startup (KV block count / attention backend / compatibility gates); the agent effectively only moves <b style="color:var(--coral-strong)">max-num-seqs / max-num-batched-tokens / gpu-util / performance_mode</b> and whatever the diagnosis calls out. Safety doesn\'t rely on a blocklist — it relies on launch-catch + disposable containers + never offering precision-lowering params.',
    'ap.showPromptBtn': 'View core prompt contract (read-only · fixed)', 'ap.hidePromptBtn': 'Hide core prompt contract',
    'ap.lockedPrompt': `You are an LLM-serving performance engineer (core contract, cannot be changed):
① Change exactly 1 parameter per round, chosen only from the "action scope";
② You must accept the bench verdict — never claim a gain the bench hasn't confirmed;
③ You must anchor every change to this round's diagnosis evidence (evidence_refs);
④ You must output the StructuredOutput schema: {action|done, rationale, evidence_refs};
⑤ Goal: maximize the primary metric without breaking SLA; if already near-optimal, output done:true.
———
Each round's user message is assembled automatically by the runner (you don't fill it in):
objective / budget / current config / distilled diagnosis / history / action scope.
Your "extra guidance" is appended after this contract.`,
    'ap.shapeLabel': 'Shape', 'ap.maxPrefix': 'Up to', 'ap.roundsUnit': 'rounds',
    'ap.roundsCapTitle': 'Rounds and minutes are caps, not targets',
    'ap.roundsCapText': 'Round count is a cap: most sessions honestly converge in 3-5 rounds; stopping early is normal',
    'ap.stopBtn': 'Stop tuning', 'ap.stopping': 'Stopping…',
    'ap.step.observe': 'read diagnosis', 'ap.step.hypothesize': 'pick a param from the diagnosis', 'ap.step.act': 'restart sandbox',
    'ap.step.measure': 'bench + score', 'ap.step.decide': 'keep/revert',
    'ap.idleDesc': "Click start and Autopilot launches a tuning session. The agent picks one vLLM parameter from the candidate set, the sandbox restarts, and the same bench runs again — a change is kept only if it measurably improves things; breaking SLA or a gain within noise reverts it. Real runw mode requires an LLM agent to be configured first; production rollout still goes through manual confirmation of the promote package.",
    'ap.moat': '<b>👁 Eyes</b> (deep diagnosis) + <b>✋ Hands</b> (bench + serve control) + <b>📊 Scorecard</b> (baseline/Δ) — all in one process',
    'ap.evaluatedOf': 'Evaluated {n} / up to {m} rounds', 'ap.currentBest': 'Current best',
    'ap.fallbackWarnPre': '⚠ This session had',
    'ap.fallbackWarnMid': 'round(s) where the LLM call failed and a deterministic heuristic picked the parameter instead — check agent config / quota / network (open that round\'s "Agent reasoning" for the specific error; rounds marked',
    'ap.fallbackWarnEnd': 'were not LLM decisions).', 'ap.fallbackBadge': '⚠ fallback',
    'ap.fallbackTitle': 'LLM call failed ({err}); this round fell back to a deterministic heuristic — check agent config/quota/network',
    'ap.expandTitle': 'Click to expand diagnosis snapshot / bench score / decision detail',
    'ap.diagLabel': 'Diagnosis', 'ap.hypLabel': 'Hypothesis', 'ap.thinkingRaw': 'Raw thinking',
    'ap.snapLabel': 'Diagnosis snapshot (what the agent saw)', 'ap.expectedLabel': 'Expected effect',
    'ap.benchScoreLabel': 'Bench score', 'ap.metricLabel': 'Metric', 'ap.decideLabel': 'Decision',
    'ap.runningLabel': 'Running', 'ap.heartbeatDefault': 'Status heartbeat — production serve untouched',
    'ap.roundReasoning': "This round's agent reasoning",
    'ap.lastResultPrefix': '📋 Last tuning result', 'ap.lastResultSuffix': "(not just finished — restored automatically when the page reopens)",
    'ap.throughputLabel': 'Throughput', 'ap.throughputValue': '{a} → {b} tok/s (×{x})',
    'ap.resultLabel': 'Result', 'ap.whyLabel': 'Why', 'ap.ledgerLabel': 'Round-by-round ledger',
    'ap.candidatePoolLabel': 'Candidate pool',
    'ap.honestNote': '"×3" only holds against a "realistic but never-tuned" default baseline — which is exactly the state of most real deployments. Against an already-tuned baseline, the honest gain is marginal (+5-15%), and the agent will report "already near-optimal" when that\'s the case.',
    'ap.viewFullTrace': 'View full reasoning trace', 'ap.viewPromotePkg': 'View promote package (manual confirmation)',
    'ap.promoteTitle': 'Promote package', 'ap.promoteNote': 'Autopilot has generated a reviewable command but has not modified production. Rollout still requires manual confirmation, traffic cutover, and health checks.',
    'ap.prodCmd': 'Production command', 'ap.rollbackCmd': 'Rollback command', 'ap.configDiff': 'Config diff',
    'ap.riskNote': 'Risk notes', 'ap.manualChecklist': 'Manual confirmation checklist', 'ap.scopeOfApply': 'Scope of applicability',
    'ap.fullTraceTitle': 'Full reasoning trace', 'ap.reasoningLabel': 'Agent reasoning', 'ap.evidenceLabel': 'Evidence refs',
    'ap.thisRoundCmd': "This round's command", 'ap.thinkingProcessLabel': 'Agent thinking process (raw)',
    'ap.copyMarkdown': 'Copy Markdown', 'ap.downloadJson': 'Download JSON',
    'ap.needFields': 'Fill in Base URL / API Key / Model', 'ap.connecting': 'Connecting…',
    'ap.connectionOk': '✓ Connection OK', 'ap.connectionFailed': 'Connection failed: ',
    'ap.needAgentConfig': 'Real tuning requires an LLM agent to be configured first', 'ap.sessionAlreadyRunning': 'A session is already running',
    'ap.startingRealTuning': 'Starting real tuning', 'ap.creatingSession': 'Creating session and preparing baseline bench',
    'ap.bridgeNotConnected': 'host bridge not connected', 'ap.startFailed': 'Failed to start',
    'ap.stoppingAndRestoring': 'Stopping tuning and restoring serve…', 'ap.terminatingSandbox': 'Terminating candidate sandbox and restoring primary serve',
    'ap.stopRequestedNoKeep': 'Stop requested — current candidate will not be kept',
    'ap.stoppedRestoring': '✓ Stopped, restoring state…', 'ap.noRunningSession': 'No tuning session running',
    'ap.stopFailed': 'Failed to stop: ', 'ap.statusRefreshRetry': 'Retrying status refresh, keeping last frame',
    'ap.bridgeUnreachable': 'host bridge unreachable', 'ap.statusUnreachable': 'status unreachable',
    'ap.copied': '✓ Copied',
    'common.close': 'Close', 'common.copy': 'Copy',
    'live.tier1': 'User-facing metrics', 'live.tier1hint': 'Last 60s · refreshed every 2s', 'live.tier2': 'Efficiency & diagnostics',
    'kpi.ttft': 'TTFT avg', 'kpi.ttft.sub': 'first-token latency', 'kpi.reqs': 'reqs',
    'kpi.tpot': 'TPOT avg', 'kpi.tpot.sub': 'per-token interval',
    'kpi.tput': 'Output throughput', 'kpi.tput.sub': 'system aggregate', 'kpi.tput.perreq': 'per-request',
    'kpi.running': 'Running reqs', 'kpi.waiting': 'Waiting queue',
    'kpi.mfu': 'MFU', 'kpi.mfu.sub': 'compute utilization',
    'kpi.gpuutil': 'GPU utilization', 'kpi.gpuutil.sub': 'SM busy',
    'kpi.vram': 'VRAM used', 'kpi.vram.sub': 'VRAM capacity', 'kpi.prefix': 'Prefix cache hit',
    'kpi.preempt': 'Preempt / min',
    'tip.ttft': 'TTFT = Time To First Token\nLatency from sending the request to receiving the first generated token.\nBig number = window average (typical); the bars below = p50→p95→p99 (watch tail blow-up; alert color follows p99).\nReading it:\n  · Dominated by the prefill stage (the whole prompt must be processed)\n  · Long prompts / high concurrency / queueing all raise TTFT\n  · This is the "how long until anything happens" the user feels\nCommon SLA tiers: <200ms instant; <500ms smooth; <1s acceptable; >2s users start to churn.',
    'tip.dist': 'Percentile distribution: one bar each, p99 = full scale. p95/p99 close to p50 = stable tail; p99 far above p50 = tail-latency blow-up.',
    'tip.tpot': 'TPOT = Time Per Output Token\nAverage time to generate each token (one forward pass).\nBig number = window average; the bars below = p50→p95→p99 (tail latency; alert color follows p99).\nReading it:\n  · Dominated by decode-stage bandwidth (the weights are read once per token)\n  · This is the "how fast tokens stream out" the user feels\n  · 1/TPOT = per-request token speed (50ms TPOT = 20 tok/s)\nCommon SLA tiers: <30ms smooth; <50ms acceptable; >100ms clearly laggy.\nITL fallback: when vllm <0.20 does not emit TPOT, the iter interval is used as an approximation (slightly different semantics).',
    'tip.tpotItl': 'Note: this vllm build does not emit TPOT — falling back to the ITL approximation.',
    'tip.tput': 'Output throughput = total tokens the system produces per second\nFormula: sum(gen_tokens) in window / window seconds\nThe two numbers measure different things:\n  · System aggregate (big): tokens/s across all concurrent requests — measures capacity, $/token\n  · Per-request (small = 1000/TPOT_p50): tokens/s a single user feels — measures typing speed\nRelationship: system aggregate ≈ per-request speed × concurrency (ideally).\nGrowing the batch raises the aggregate but may slightly lower per-request speed — a bit laggier per user, higher total capacity.',
    'tip.kv': 'KV cache usage = allocated KV blocks / total KV blocks\nvllm manages each request\'s attention K/V cache in fixed-size blocks.\nReading it:\n  · This is the "how full of requests" water level in VRAM — not the same as "VRAM used" above\n  · <50%: room for more concurrency, raising max_num_seqs is safe\n  · >80%: near the limit, new requests queue or preempt older ones\n  · >90%: preemption frequency spikes, throughput actually drops\nInteracts with --gpu-memory-utilization: that flag sets the total KV-cache pool size.',
    'tip.running': 'Running requests = requests currently in a forward pass (batch size)\nvllm\'s continuous batching can change this every iter.\nReading it:\n  · Measures current concurrency — read alongside TPS\n  · Far below max_num_seqs: under-utilized, raise client concurrency\n  · Equal to max_num_seqs: saturated — check whether throughput- or memory-bound\n  · Compare with waiting_reqs: running stuck + waiting queueing = memory bottleneck',
    'tip.waiting': 'Waiting queue = requests received but not yet scheduled to running\nWhen VRAM is short or max_num_seqs is full, new requests pile up here.\nReading it:\n  · Persistently >0: intake outpaces drain — add GPUs or rate-limit\n  · Spikes >0 then quickly to zero: occasional traffic burst, normal\n  · Sustained >20: users feel TTFT spike — this is a rule alert threshold',
    'tip.mfu': 'MFU = Model FLOPs Utilization\nActual compute / GPU peak compute — "how much of the GPU\'s compute is really used while running the model".\nFormula: MFU = (6 · params · tokens/sec) / peak_TFLOPS\nReading it:\n  · Training is typically 30-55% (good, on A100/H100)\n  · Inference decode is naturally low (~1-5%) — bandwidth-bound, higher MFU isn\'t the goal\n  · The prefill stage being high (>30%) is when MFU truly matters\nNeeds vllm ≥0.20 perf_stats; vllm 0.13 doesn\'t emit it, so this shows —',
    'tip.gpuutil': 'GPU utilization = fraction of time the SMs (Streaming Multiprocessors) are busy\nThe NVML value: fraction of the last sampling interval where at least one kernel was running.\nNote: it does NOT mean compute is saturated — decode often sits at 70-90% while MFU is only 1-5%,\nbecause an SM "running a kernel waiting on data" still counts as busy.\nFor real compute efficiency, look at MFU.',
    'tip.vram': 'VRAM usage = used VRAM / total VRAM\nWeights + KV cache + activations + CUDA workspace.\nAt startup --gpu-memory-utilization sets the target share for weights+KV (default 0.9).\nSustained >95% easily triggers preemption / OOM.',
    'tip.prefix': 'Prefix cache hit rate = reused-prefix KV blocks / total queried blocks\nvllm caches KV for already-computed prompt prefixes; a later request hitting the prefix can skip prefill.\nMulti-turn chat / fixed system prompts should be >50%; one-off requests are naturally 0.',
    'tip.padding': 'CUDA padding = (cudagraph actual batch - real tokens) / cudagraph batch\nWith CUDA-graph acceleration vllm runs fixed batch sizes; short batches are filled with padding.\nMore padding = more waste. >30% means the batch-size buckets are poorly chosen.',
    'tip.preempt': 'Preempt / min = how many requests get swapped out per minute\nWhen KV cache runs short, vllm swaps active requests to host memory to admit others.\n>0 means VRAM is tight; >5/min is a clear signal — lower max_num_seqs or add VRAM.',
    'common.avg': 'avg',
    'live.latTrends': 'User-latency trends', 'live.latHint': 'Last 5 min · solid = avg / faint = p99',
    'lat.ttft': 'TTFT · first-token latency', 'lat.tpot': 'TPOT · per-token time', 'lat.e2e': 'E2E · end-to-end latency',
    'lat.noTtft': 'No TTFT data yet', 'lat.noTpot': 'No TPOT / ITL data yet',
    'lat.noE2e': 'No E2E data yet', 'lat.noE2eHint': '(run a bench to trigger request-completion events)',
    'lat.itlSource': 'Source: ITL fallback (this vllm doesn’t emit TPOT; iter interval used as approximation)',
    'roof.title': 'Roofline live diagnosis',
    'roof.desc': 'Dots = nearby steps aggregated (bigger = more steps) · distance below the roof tells compute- vs memory-bound',
    'roof.estimate': '⚠ Estimated · this vllm doesn’t emit perf_stats; derived from token counts + model params ({b}B params).',
    'roof.estimateNote': 'Shape is right, absolute values approximate; upgrade to vllm ≥0.20 to switch back to measured.',
    'roof.sample': 'Current samples', 'roof.verdict': 'Verdict',
    'roof.computeUtil': 'Compute used', 'roof.bwUtil': 'Bandwidth used',
    'roof.speedup': 'Speed-up directions', 'roof.noSamples': 'No samples to interpret — run a bench to generate data.',
    'roof.foot': 'median AI={ai} · ridge={knee} · samples={n}',
    'roof.footTip': 'samples={n}  median AI={ai}  knee={knee}',
    'roof.inMemBound': 'in the memory-bound region', 'roof.inCmpBound': 'in the compute-bound region',
    'startup.btn': 'Startup info', 'startup.title': 'vLLM startup info',
    'startup.cmdline': 'Launch command', 'startup.noCmdline': 'cmdline not captured (plugin ran before sys.argv?)',
    'startup.env': 'Environment variables', 'startup.noEnv': 'No relevant env vars (only VLLM_/PPING_LANG_/HF_/CUDA_/TORCH_ prefixes)',
    'startup.resolved': 'vLLM resolved config', 'startup.resolvedSub': 'final values after merging CLI + defaults',
    'startup.noConfig': 'No vllm_config (not available at plugin init; common in local demo)',
    'startup.masked': 'name contains TOKEN/KEY/SECRET — value masked',
    'kern.profileTitle': 'Kernel-level profiling',
    'kern.profileHint': 'PC sampling measurements · how much GPU time each kernel takes + why it\'s slow (no Nsight needed, on-demand short window)',
    'kern.rooflineTitle': 'Roofline · whole-model scope',
    'kern.rooflineSubtitle': 'Where the whole model sits on the roofline (same data as Overview)',
    'kern.rooflineHint': 'Dots = nearby steps aggregated (bigger = more steps) · distance from ceiling = headroom left · pair with PC Sampling below to see which kernel and why',
    'kern.howComputed': 'How the roofline is computed',
    'kern.rooflineFrom': 'All read live from GPU CUDA device properties',
    'kern.kneeDesc': 'Left of knee = memory-bound, right of knee = compute-bound; point y = measured throughput TFLOPs/s, x = FLOPs/byte',
    'kern.scalingAnalysis': 'Scaling analysis',
    'kern.scalingSweepBtn': '▶ Measured scaling curve (bench sweep concurrency 1/4/16/64, ~2 min)',
    'kern.scalingSweepInProgress': 'benching…',
    'kern.scalingSweepNote': 'Benchmark traffic targets this vLLM; panel data will be affected during the sweep',
    'kern.scalingSweepError': 'Bench failed: {err}',
    'kern.scalingVerdict': '📏 Measured scaling verdict',
    'kern.verdictChart': 'Solid green line in chart = measured; dashed = theoretical envelope',
    'kern.kernelTimePct': 'Per-kernel GPU time share',
    'kern.kernelSampling': 'PC sampling · sorted by share',
    'kern.kernelHint': 'Sample hits ∝ GPU active time → how much GPU time each kernel consumes (sampling estimate, not exact µs) · plus the stall reason it dominates ·\nbased on the latest collection ({n} samples total)',
    'kern.recollect': 'Re-collect',
    'kern.collecting': 'Collecting…',
    'kern.byKernelClass': 'By operator class · GPU time share',
    'kern.gpuUtilDiag': 'GPU utilization diagnosis',
    'kern.stallDesc': '{stall}% of sampled cycles are stalled (waiting), only {issued}% actually issuing instructions',
    'kern.sourceHotspots': '🔬 Source-level hotspots',
    'kern.sourceDesc': 'PC samples map precisely to Python source lines (Triton/custom kernels with lineinfo)',
    'kern.sourceTimePct': 'These kernels account for {pct}% of GPU time ——\nthis workload\'s dominant hotspot is in closed-source cutlass/cuBLAS GEMM (table below, only SASS offset available); the source-addressable ones are the Triton kernels below.',
    'kern.sourceTimePctHigh': 'Share is significant; source-level targeting is directly actionable.',
    'kern.expandKernelDetail': 'Click any row to expand → see the kernel\'s full stall breakdown + optimization hints',
    'kern.kernelClass': 'Class',
    'kern.kernelGpuTime': 'GPU time %',
    'kern.kernelStallRecoverable': 'Recoverable stall time',
    'kern.kernelStallTitle': 'This kernel\'s stall breakdown (% of its samples)',
    'kern.kernelDeepHotspot': 'Deepest hotspot',
    'kern.kernelHotspotDesc': 'PC sampling mapped to instruction address (where stall samples cluster)',
    'kern.sourceFile': 'Source file: {path}',
    'kern.closedLibKernel': 'Closed-source kernel (no lineinfo) → SASS instruction offset-level hotspots:',
    'kern.launchOrigin': '↗ Launch origin',
    'kern.launchStack': 'launch stack, tracing back to host code that called it',
    'kern.preciseMicros': 'Want exact µs per-kernel timing? That requires CUPTI Activity mode — it contends with PC Sampling for the same performance counter hardware (mutually exclusive, needs separate deployment). Deep Evidence below shows the same sample\'s global stall breakdown.',
    'kern.collectingPcSampling': 'Collecting PC Sampling evidence… (~5s, will auto-populate)',
    'kern.noPcSamplingData': 'Opening this tab auto-collects once; or click \'Collect stall evidence\' in Deep Evidence below.',
    'kern.dataFreshness': 'Live',
    'kern.aggregated': 'aggregated every {w}s, current data collected at {when}',
    'kern.noActivity': '⏸ No GPU activity currently — below is data from {when} when kernels last ran (latest {w}s window), not current',
    'kern.findings': 'Diagnosis findings',
    'kern.gpuBusy': 'GPU busy',
    'kern.wallClockShare': 'wall-clock share',
    'kern.launchFreq': 'Kernel launch frequency',
    'kern.meanDuration': 'Mean kernel duration',
    'kern.inCudaGraph': 'In CUDA Graph',
    'kern.memcpy': 'memcpy',
    'kern.syncWait': 'Sync wait',
    'kern.classTrend': 'Kernel class share · real-time trend',
    'kern.last3Min': 'Last ~3 min',
    'kern.stackedArea': 'Stacked area = share of GPU compute time by kernel class over time',
    'kern.utilTrend': 'GPU utilization vs wait · real-time trend',
    'kern.utilNote': 'High GPU busy + low sync wait = healthy; wait spike = launch-bound',
    'kern.timeline': 'Execution timeline',
    'kern.timelineSpan': 'Last {n} kernels · span {span} ms',
    'kern.timelineHint': 'x=time · row=GPU stream · block=kernel (width ∝ duration) · blank=idle · click+zoom, drag scrollbar to pan',
    'kern.exportTrace': '⬇ Export trace',
    'kern.perfettoInst': 'Drag into ui.perfetto.dev (Nsight / PyTorch format) · inline preview below',
    'kern.zoomOut': 'Zoom out',
    'kern.fitWidth': 'Fit',
    'kern.zoomIn': 'Zoom (around selection)',
    'kern.selected': '▸ Selected {name} (click+zoom it)',
    'kern.selectedDetail': 'Selected kernel details',
    'kern.deselect': 'Deselect',
    'kern.duration': 'Duration',
    'kern.startTime': 'Start',
    'kern.stream': 'stream',
    'kern.inGraph': 'In Graph',
    'kern.yes': 'Yes',
    'kern.no': 'No',
    'kern.rawKernelDetail': 'Raw kernel details',
    'kern.unique': '{n} unique kernels',
    'kern.kernelNameNote': 'Count of unique kernel names in this window (deduped). Models typically run the same kernel set per step (stable); changes with model/config. Max 100.',
    'kern.allValuesWindow': 'All values are from the latest aggregation window (~{w}s), refreshed each window',
    'kern.kernelNameRaw': 'Kernel name (raw)',
    'kern.calls': 'Calls',
    'kern.totalTime': 'Total time',
    'kern.average': 'Avg',
    'kern.share': 'Share',
    'kern.graphShare': 'Graph',
    'kern.noKernelDetail': 'No per-kernel details (requires instrumented collector, or no kernels in this window)',
    'kern.showAll': 'Show all {n} ▾',
    'kern.collapse': 'Collapse ▴',
    'kern.overhead': 'Collection overhead {overhead} ms/window (5% budget)',
    'kern.dropped': '{n} dropped (auto-downgraded)',
    'kern.deepEvidence': '🔬 Deep Evidence — why it\'s slow',
    'kern.deepEvidenceSub': 'PC Sampling evidence · on-demand short window',
    'kern.deepHint': 'Above shows "which kernel"; here we see the whole picture: where warp cycles go, what global stalls are, and how these are measured',
    'kern.collectEvidence': 'Collect stall evidence (5s)',
    'kern.unavailable': 'PC Sampling evidence unavailable — {err}\nRequires Linux + libppingcupti.so + GPU perf counter permissions; same-process with torch needs instrumentation (see design doc §12).',
    'kern.warpCycleDirection': 'Warp cycle destinations',
    'kern.allSamples': 'of {n} total samples',
    'kern.issued': 'Issued (working)',
    'kern.slack': 'Ready but not selected (occupancy headroom)',
    'kern.stallWait': 'True stall (waiting)',
    'kern.stallAnalysis': '→ Many cycles in true stall: latency bottleneck, see what it\'s stuck on below',
    'kern.slackAnalysis': '→ Ready warps not selected: occupancy sufficient, bottleneck not in parallelism',
    'kern.issuedAnalysis': '→ Issue rate high: GPU is busy',
    'kern.stallBreakdown': 'Stall breakdown',
    'kern.stallBreakdownSub': '% of stall samples (= total − issued) · click row for raw hardware metric names',
    'kern.howMeasured': 'How measured',
    'kern.samplingPeriod': 'Sample every {period} cycles (2^{log}) ·\nThis window {w}s collected {samples} samples ·\nGetData cumulative overhead {getdata}ms ·\nDropped {dropped} · HW buffer full {hwfull} times ·\nGPU hardware sampling, no Nsight, no service interruption',
    'kern.noInitialData': 'Click the button above to open a short-window PC Sampling — see what\'s stalling inside these kernels (memory deps / compute pipeline / sync …).',
    // #5 comm bucket subdivision
    'csub.title': 'Comm breakdown',
    'csub.hint': '% of total comm time · allreduce is usually latency-bound, all_gather / reduce_scatter usually bandwidth-bound — different fixes',
    'csub.ofTotal': '{pct}% overall',
    // #6 Kernel snapshot A/B compare (verify a kernel change)
    'ksnap.saveA': 'Save as snapshot A',
    'ksnap.saveB': 'Save as snapshot B',
    'ksnap.slotA': 'Snapshot A (before)',
    'ksnap.slotB': 'Snapshot B (after)',
    'ksnap.clear': 'Clear',
    'ksnap.savedAt': 'captured {time}',
    'ksnap.title': '📐 Before / after compare',
    'ksnap.hint': 'Δ = B relative to A; green = better, red = worse',
    'ksnap.needBoth': 'Save both A (before) and B (after) to see the per-kernel diff here.',
    'ksnap.loadContext': 'Load context',
    'ksnap.loadSame': 'Typical kernel moved {median} (median) — the change is concentrated in a few kernels rather than shifting everything, so the loads are comparable and the absolute Δ below is trustworthy. Overall GPU activity {total}.',
    'ksnap.loadDrift': '⚠ Typical kernel moved {median} (median) — nearly every kernel shifted in the same direction, which looks more like different traffic/load between the two captures than like a change taking effect. The "absolute rate Δ" below is confounded by that; re-capture under matching load. Overall GPU activity {total}.',
    'ksnap.periodMismatch': '⚠ The two snapshots used different sampling periods (2^{a} vs 2^{b}). Values are normalized by period before comparing, but re-capturing with one period is still recommended.',
    'ksnap.stallDiff': 'Global stall breakdown change',
    'ksnap.stallDiffSub': 'Percentage-point delta (B − A) · down = less of that stall',
    'ksnap.warpDiff': 'Warp cycle destination change',
    'ksnap.kernelDiff': 'Per-kernel diff',
    'ksnap.kernelDiffSub': 'Sorted by GPU time share · top 25 only',
    'ksnap.colRate': 'Absolute rate Δ',
    'ksnap.colRateTip': 'GPU cycles this kernel consumes per wall-clock second (normalized by sampling period). This is the answer to "did this kernel actually get faster" — provided both snapshots ran under the same load.',
    'ksnap.colShare': 'GPU time share',
    'ksnap.colShareTip': 'Share is relative: making one kernel faster automatically inflates every other kernel\'s share. To judge whether a kernel got faster, read the absolute rate Δ.',
    'ksnap.colStall': 'Dominant stall',
    'ksnap.new': 'new',
    'ksnap.gone': 'gone',
    'ksnap.shareCaveat': 'Note: share is a relative number — optimizing one kernel away makes every remaining kernel\'s share "look worse". To judge whether a given kernel itself got faster, read the absolute rate Δ.',
    'bench.createTitle': 'Create New Benchmark',
    'bench.createHint': 'Runs asynchronously after submission; results appear in history below',
    'bench.name': 'Name (optional)',
    'bench.namePlaceholder': 'adhoc-timestamp',
    'bench.api': 'API',
    'bench.endpoint': 'Endpoint',
    'bench.endpointHint': 'vLLM service base URL; automatically appends /v1/...',
    'bench.modelName': 'Model Name',
    'bench.modelNameSub': '(served-model-name)',
    'bench.modelNameHint': 'String in OpenAI request {"model": "..."}; must match vLLM startup --served-model-name. Not the disk weight path.',
    'bench.promptSource': 'Prompt Data Source',
    'bench.promptTokens': 'Prompt tokens',
    'bench.outputTokens': 'Output tokens',
    'bench.concurrency': 'Concurrency',
    'bench.measureMode': 'Measurement Mode',
    'bench.measureByDuration': 'By Duration',
    'bench.measureByRequests': 'By Request Count',
    'bench.duration': 'Duration (seconds)',
    'bench.numRequests': 'Num requests',
    'bench.warmup': 'Warmup (seconds)',
    'bench.timeout': 'Timeout (seconds)',
    'bench.sloConstraint': 'SLO Constraints (optional)',
    'bench.sloShapeHint': 'Picking a shape fills in p99 defaults for all three; clear any field to skip it',
    'bench.sloOptional': 'optional',
    'bench.sloPreviewLabel': 'Generated on submit:',
    'bench.sloPreviewEmpty': '(empty — SLO status will be marked n/a after submission)',
    'bench.submitBtn': 'Start Run',
    'bench.submitting': 'Submitting...',
    'bench.running': 'Currently Running',
    'bench.historyTitle': 'History',
    'bench.historyHint': 'Last 50 runs',
    'bench.emptyTitle': 'No benchmark runs yet',
    'bench.emptyHint': 'Fill the form above and click "Start Run"',
    'bench.compareTitle': 'Compare Results',
    'bench.compareHint': 'Δ = B relative to A (baseline); green = better, red = worse, |Δ|<2% is neutral',
    'bench.clearCompare': 'Clear Comparison',
    'bench.metricTtftAvg': 'TTFT Mean',
    'bench.metricTpotAvg': 'TPOT Mean',
    'bench.metricTokPerSec': 'tok/s',
    'bench.metricOkErr': 'ok / err',
    'bench.resultError': 'Error:',
    'bench.clientMetrics': 'Client Metrics',
    'bench.scenario': 'Scenario',
    'chart.currentSamples': 'Current samples',
    'chart.measuredScaling': 'Measured scaling',
    'chart.samplesAgg': 'Aggregated {n} steps',
    'chart.measuredConcurrency': 'Measured concurrency {b}: {y} TFLOPs/s',
    'chart.theoreticalEnvelope': 'Theoretical envelope: {e} TFLOPs/s',
    'chart.gap': 'Gap: {g}%',
    'chart.kclassComm': 'Comm (NCCL)',
    'chart.kclassOther': 'Other',
    'chart.syncWait': 'Sync wait (launch-bound)',
    'bench.agoSeconds': '{s} seconds ago',
    'bench.agoMinutes': '{m} minutes ago',
    'bench.agoHours': '{h} hours ago',
    'bench.agoDays': '{d} days ago',
    'bench.ttftAvg': 'TTFT avg',
    'bench.tpotAvg': 'TPOT avg',
    'bench.e2eAvg': 'E2E avg',
    'bench.outputThroughput': 'Output throughput',
    'bench.completionErrors': 'Completion / errors',
    'bench.submitError': 'Submission failed: {e}',
    'bench.submitException': 'Error: {e}',
    'bench.concurrencyLabel': 'Concurrency {c} · {p}/{o} tok · {l}',
    'bench.promptSourceSynthetic': 'Synthetic padding (synthetic)',
    'bench.promptSourceDesc': 'Cycle the quick brown fox template by prompt_tokens length',
    'toast.saveFailed': 'Save failed: {e}',
    'toast.error': 'Error: {e}',
    'toast.saveApplied': 'Saved (hot-loaded)',
    'toast.savePending': 'Saved (engine not running, takes effect on restart)',
    'kernel.fresh': 'just now',
    'kernel.agoSeconds': '{s}s ago',
    'kernel.agoMinutes': '{m}m ago',
    'kernel.scaling.progress': 'Starting…',
    'kernel.scaling.testing': 'Testing…',
    'kernel.pcSamplingUnavailable': 'PC Sampling unavailable',
    'kernel.requestFailed': 'Request failed: {e}',
    'kernel.traceUnavailable': 'No trace data available (requires CUPTI collector active)',
    'roofline.memoryBound': 'Memory-bound (typical for LLM decode phase)',
    'roofline.memSugg1': 'Increase batch until KV cache approaches 80% — amortize weight re-reads',
    'roofline.memSugg2': 'Enable speculative decoding — reduce decode steps',
    'roofline.memSugg3': 'Weight quantization (AWQ / GPTQ) — reduce bytes to read',
    'roofline.memSugg4': 'Upgrade to higher-bandwidth GPU (current {bw} GB/s; H100 3.4 TB/s, H200 4.8 TB/s)',
    'roofline.computeBound': 'Compute-bound (prefill or large-batch state)',
    'roofline.compSugg1': 'Increasing batch shows diminishing returns — compute near saturation',
    'roofline.compSugg2': 'Upgrade to higher-compute GPU or add tensor parallelism',
    'roofline.compSugg3': 'Chunked prefill — split long prompts to let decode breathe',
    'kernel.bottleneck.memDep': 'Memory dependency',
    'kernel.bottleneck.memDepAction': 'Data waiting for memory loads. Try fp8/int8 quantization to reduce memory traffic, fuse operators to reduce round-trips, verify KV-cache reuse.',
    'kernel.bottleneck.memThrottle': 'Memory bandwidth throttle',
    'kernel.bottleneck.memThrottleAction': 'Memory subsystem saturated. Lower precision or fuse operators to reduce memory traffic.',
    'kernel.bottleneck.mathPipe': 'Math pipeline',
    'kernel.bottleneck.mathPipeAction': 'Compute units near saturation (good, already efficient). Further gains require lower precision or better kernels.',
    'kernel.bottleneck.execDep': 'Instruction dependency',
    'kernel.bottleneck.execDepAction': 'Data dependency between instructions; limited by kernel structure, little room to optimize.',
    'kernel.bottleneck.sharedDep': 'Shared memory dependency',
    'kernel.bottleneck.sharedDepAction': 'Waiting for shared memory / L1. Check tile size and bank conflicts.',
    'kernel.bottleneck.sync': 'Synchronization',
    'kernel.bottleneck.syncAction': 'Threads waiting at barriers. Check sync frequency and load balance.',
    'kernel.bottleneck.fetchCtrl': 'Fetch control',
    'kernel.bottleneck.fetchCtrlAction': 'Instruction fetch / branch decisions; usually not a primary bottleneck.',
    'kernel.bottleneck.dispatch': 'Dispatch',
    'kernel.bottleneck.dispatchAction': 'Issue port limited.',
    'kernel.meaning.memDep': 'Waiting for data from global/local memory (long scoreboard)',
    'kernel.meaning.sharedDep': 'Waiting for shared memory / L1 (short scoreboard)',
    'kernel.meaning.memThrottle': 'Memory instruction queueing, memory subsystem saturated',
    'kernel.meaning.mathPipe': 'Compute pipeline busy (Tensor / ALU / FMA), approaching compute limit',
    'kernel.meaning.execDep': 'Waiting for previous instruction result (data dependency)',
    'kernel.meaning.sync': 'Waiting at barrier / membar for other threads',
    'kernel.meaning.fetchCtrl': 'Waiting for instruction fetch / branch decision',
    'kernel.meaning.dispatch': 'Issue port limited',
    'kernel.meaning.schedulerSlack': 'Ready warps not selected this cycle (occupancy margin, not a bottleneck)',
    'kernel.meaning.other': 'Other / miscellaneous',
    'kernel.label.memDep': 'Memory dependency',
    'kernel.label.sharedDep': 'Shared/MIO dependency',
    'kernel.label.memThrottle': 'Memory pressure',
    'kernel.label.mathPipe': 'Compute pipeline',
    'kernel.label.execDep': 'Execution dependency',
    'kernel.label.sync': 'Sync',
    'kernel.label.fetchCtrl': 'Fetch/control',
    'kernel.label.dispatch': 'Dispatch',
    'kernel.label.schedulerSlack': 'Scheduler slack (not a bottleneck)',
    'kernel.label.other': 'Other',
    'kernel.suggestion.gemmMem': 'Memory-bound GEMM: try fp8/int8 quantization, increase batch for better compute density, verify weights aren\'t repeatedly read from VRAM.',
    'kernel.suggestion.gemmMath': 'GEMM compute-saturated (near peak); further gains hard. Consider lower precision.',
    'kernel.suggestion.attnMem': 'Attention memory-bound: verify FlashAttention / PagedAttention active, check KV-cache hit rate.',
    'kernel.suggestion.elementwise': 'Elementwise / copy: explore operator fusion to reduce kernels and VRAM round-trips.',
    'kernel.suggestion.sampling': 'Sampling / decode overhead: batch decode, reduce unnecessary host-device trips.',
    'kernel.suggestion.index': 'Index / lookup: verify contiguous access patterns, avoid random gather breaking memory coalescing.',
    'kernel.suggestion.execDep': 'Instruction latency dominant; limited by kernel structure, little room to improve.',
    'ui.copy': 'Copy',
    'ui.copied': 'Copied ✓',
    'ui.copyFailed': 'Copy failed',
    'cleanup.computeRoofFormula': 'TFLOPS (= SM count × SM clock × architectural bf16 Tensor throughput)',
    'cleanup.memoryRoofFormula': 'GB/s (= memory clock × bus width × 2)',
    'cleanup.knee': 'Knee',
    'cleanup.scalingEff': ' (scaling efficiency {pct}%)',
    'cleanup.issuedTitle': 'Issued {pct}%',
    'cleanup.stallTitle': 'stall {pct}%',
    'cleanup.mainCause': 'Main cause',
    'cleanup.ofStall': ' ({pct}% of stall)',
    'cleanup.topRecoverablePre': '🎯 Top recoverable hotspot: ',
    'cleanup.topRecoverableMid': '—— roughly',
    'cleanup.topRecoverablePost': 'of global GPU time is spent waiting on it; optimize it first.',
    'cleanup.dominantStall': 'Dominant stall',
    'cleanup.lineinfoPre': 'Resolving to source lines needs the kernel built with',
    'cleanup.lineinfoPost': ' (Triton / self-compiled enable it by default); cutlass / cuBLAS are closed-source and stop here — offset + kernel name (tile/dtype) is already enough to pinpoint which part is stalling.',
    'cleanup.collapse': 'Collapse ▴',
    'cleanup.showAllKernels': 'Show all {n} ▾',
    'cleanup.pcSamplingUnavailable': 'PC Sampling evidence unavailable —— {err}',
    'cleanup.frozen': '⏸ Frozen',
    'cleanup.liveTl': '🔴 Live',
    'cleanup.fit': 'Fit',
    'cleanup.cuptiTimelineEmpty': 'No CUPTI execution timeline data yet (the collector must be capturing kernels).',
    'cleanup.rawKernelPre': 'Real mangled name + GPU hardware-measured duration',
    'cleanup.rawKernelPost': ', sorted by share descending',
    'cleanup.warpIssuedTitle': 'Issued {pct}%',
    'cleanup.warpSlackTitle': 'Ready, not selected {pct}%',
    'cleanup.warpStallTitle': 'True stall {pct}%',
    'cleanup.samplesCount': '{n} samples',
    'cleanup.aBaseline': 'A (baseline)',
    'cleanup.deltaBetter': ' ↑ better',
    'cleanup.deltaWorse': ' ↓ worse',
    'cleanup.deltaEven': ' ≈ even',
    'cleanup.statusRunning': 'Running',
    'cleanup.statusDone': 'Done',
    'cleanup.statusFailed': 'Failed',
    'cleanup.sloPass': 'SLO pass',
    'cleanup.sloFail': 'SLO fail',
    'cleanup.compareSlot': 'Compare {slot}',
    'cleanup.compare': 'Compare',
    'cleanup.autoRefresh': 'Auto-refresh every 2s',
    'cleanup.envelopeLabel': 'B={b}: bandwidth-bound ceiling {y} TFLOPs/s',
    'cleanup.measured': 'Measured',
    'cleanup.dominatedBy': '{label} dominant',
    'rule.regime-classify.name': 'Arithmetic intensity vs ridge (Roofline locate)',
    'rule.regime-classify.hypothesis': 'AI < ridge → memory-bound; otherwise compute-bound (definitional). When memory-bound with high SM util: the high util is a physical ceiling, not headroom.',
    'rule.A.name': 'Under-utilized (compute & bandwidth both idle)', 'rule.A.hypothesis': 'Requests are running, yet neither roof is close — both compute and bandwidth have headroom, so the bottleneck isn’t hardware (likely: batch not assembled / launch overhead / small ops not fused). Idle-guarded: won’t fire with no requests running, separating "under-utilized" from "simply idle".', 'rule.A.suggestion': 'Raise max-num-seqs (batch not filled) / chunked-prefill’s partial-prefills sub-knobs. Note: Continuous Batching, CUDA Graph, and chunked-prefill are ON by default on 0.21 — first confirm none is force-disabled (e.g. enforce-eager); don’t re-enable what’s already on (a wasted round).',
    'rule.B.name': 'Bandwidth bottleneck (hugging the HBM bandwidth roof)', 'rule.B.hypothesis': 'Memory-bound: each decode step re-reads weights + KV; bandwidth is the ceiling. Confirmed by NVML HBM-controller busy / kernel memory_throttle (memory pipe saturated) / memory_dependency (waiting on memory loads). (Note: perf measured-MBU exceeds 1 on small models due to L2 reuse, so it is not used as a threshold.)', 'rule.B.suggestion': 'Speculative decoding (watch acceptance rate, avoid compute backlash) / KV quantization (FP8) / move to a higher-bandwidth GPU.',
    'rule.C.name': 'Compute bottleneck (compute saturated)', 'rule.C.hypothesis': 'Compute-bound: FLOPs saturated, compute is the ceiling (intrinsic to long-prompt prefill). Confirmed by measured MFU at the roof / kernel math_pipe (FMA/ALU/Tensor compute pipe saturated).', 'rule.C.suggestion': 'Switch to a faster attention backend (auto-selected by hardware on 0.21; try --attention-backend to override) / weight quantization (FP8/FP4) / upgrade the compute GPU.',
    'rule.D.name': 'Capacity bottleneck (KV overflows and preempts)', 'rule.D.hypothesis': 'VRAM can’t hold the KV → concurrency stalls → preemption. V1 preemption is pure recompute (KV dropped, re-prefilled from scratch); once it hits, decode throughput collapses off a cliff.', 'rule.D.suggestion': 'KV quantization (FP8) / lower max-model-len / KV offload / lower max-num-seqs.',
    'rule.A.detector.roofline': 'Compute/bandwidth both idle (low MFU + HBM controller idle)',
    'rule.A.detector.kernel_slack': 'Kernel scheduler_slack (warps ready but not issued)',
    'rule.B.detector.hbm_busy': 'HBM controller busy (NVML mem-util)',
    'rule.B.detector.kernel_throttle': 'Kernel memory-pipe throttle (LSU saturated)',
    'rule.B.detector.kernel_memdep': 'Kernel memory latency (long_scoreboard waiting on memory)',
    'rule.C.detector.measured_mfu': 'Measured MFU near the roof',
    'rule.C.detector.kernel_mathpipe': 'Kernel compute-pipe saturated (FMA/ALU/Tensor)',
    'rule.D.detector.kv_pressure': 'KV pool near exhaustion',
    'rule.D.detector.preemption': 'Preemption has occurred',
    'decode.vllmFusedRms': 'vLLM custom · fused add + RMSNorm', 'decode.vllmRms': 'vLLM custom · RMSNorm',
    'decode.vllmRope': 'vLLM custom · RoPE', 'decode.vllmSilu': 'vLLM custom · SiLU/activation',
    'decode.vllmCuda': 'vLLM custom CUDA kernel', 'decode.flashinferSample': 'FlashInfer · sampling kernel',
    'decode.cublasGemv': 'cuBLAS GEMV (matrix×vector, typical for small batch)',
    'decode.torchNative': 'PyTorch · native elementwise/reduce kernel',
    'decode.cppCompiled': 'C++ compiled kernel (closed-source / no lineinfo)',
    'lang.label': 'Language / 语言',
  },
};
function _uiLang() {
  try { const s = window.Alpine && Alpine.store('i18n'); if (s && s.lang) return s.lang; } catch (e) { /* pre-init */ }
  return localStorage.getItem('pping_lang_ui')
    || ((navigator.language || '').toLowerCase().startsWith('zh') ? 'zh' : 'en');
}
window.t = function (key, params) {
  const lang = _uiLang();
  let s = (I18N[lang] && I18N[lang][key]) || I18N.en[key] || key;
  // 占位符插值：t('k', {ai: 3.0}) 把 '… {ai} …' 里的 {ai} 换成 3.0(中英语序不同时用)
  if (params) for (const k in params) s = s.split('{' + k + '}').join(params[k]);
  return s;
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
          label: t('chart.currentSamples'), data: [],
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
          // 调优地图：decode 的算术强度≈batch → 扩 batch 沿带宽上界向右爬,ridge point 后 compute-bound
          label: 'batch scaling envelope', data: [], showLine: true, borderColor: '#9a9aa4',
          borderDash: [5, 4], borderWidth: 1.5, pointRadius: 3.5, pointStyle: 'rectRot',
          backgroundColor: '#9a9aa4', fill: false, order: 4,
        },
        {
          // P0-C：实测 scaling 曲线(压测扫并发档)—— 缺口从哪个 B 张开 = 真实瓶颈位置
          label: t('chart.measuredScaling'), data: [], showLine: true, borderColor: '#0d8b80',
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
              if (ds === t('chart.currentSamples')) {
                const n = ctx.raw && ctx.raw.n > 1 ? [t('chart.samplesAgg', {n: ctx.raw.n})] : [];
                return [`AI:  ${ctx.parsed.x.toFixed(2)} FLOPs/byte`, `TPut: ${ctx.parsed.y.toFixed(1)} TFLOPs/s`, ...n];
              }
              if (ds === 'batch scaling envelope') {
                return t('cleanup.envelopeLabel', {b: ctx.raw.b, y: ctx.parsed.y.toFixed(1)});
              }
              if (ds === t('chart.measuredScaling')) {
                return [t('chart.measuredConcurrency', {b: ctx.raw.b, y: ctx.parsed.y.toFixed(2)}),
                        t('chart.theoreticalEnvelope', {e: (ctx.raw.env || 0).toFixed(2)}),
                        t('chart.gap', {g: (ctx.raw.gap || 0).toFixed(0)})];
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

// 相近 step 合并成簇心：log 网格分桶(x 每十倍程 6 桶、y 4 桶),桶内取几何均值,
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
  // 图例关着(legend:false),这两个 label 只在 tooltip 显示;懒建的图表只在建图那刻求值一次
  // t(),切语言后不会自动重算——每次刷新时顺手重赋值,让 tooltip 也跟着语言走。
  chart.data.datasets[0].label = t('chart.currentSamples');
  chart.data.datasets[4].label = t('chart.measuredScaling');
  const agg = _aggRooflinePoints((data.points || []).map(p => ({ x: p.ai, y: p.throughput_tflops })));
  // A：簇语义标签 —— 步数最多的簇 = decode 主体(decode 步数远多于 prefill);
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
  // 点半径 ∝ log(合并步数)：单步 4px,几十步 ~10px,封顶 13px
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
  // P0-C：实测 scaling 曲线(压测扫出来的真实扩展点,叠在理论 envelope 上)
  const rows = (data.scaling && data.scaling.verdict && data.scaling.verdict.rows) || [];
  chart.data.datasets[4].data = rows.map((r, i) => ({
    x: r.b, y: r.tflops, b: r.b, env: r.envelope_tflops, gap: r.gap_pct,
    label: i === rows.length - 1 ? t('cleanup.measured') : '', labelDy: 14, labelColor: '#0d8b80', labelBold: true,
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
          label: t('common.avg'),
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
  chart.data.datasets[1].label = t('common.avg');   // 同上,legend 关着、只在 tooltip 显示,每次刷新重取当前语言
  chart.data.labels = buckets.map(b => Math.round(b.t) + 's');
  chart.data.datasets[0].data = buckets.map(b => b.p99);
  chart.data.datasets[1].data = buckets.map(b => b.avg != null ? b.avg : b.p50);
  chart.update('none');
}

// kernel 类(堆叠面积)— 顺序 = 画的层序
const _KCLASSES = [
  ['gemm', '#5b5bd6', 'GEMM'], ['attention', '#0d8b80', 'Attention'],
  ['comm', '#d8483f', t('chart.kclassComm')], ['norm', '#b7791f', 'Norm'],
  ['activation', '#3f9a63', 'Activation'], ['rotary', '#c2334f', 'Rotary'],
  ['other', '#9a9aa4', t('chart.kclassOther')],
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
      {label: t('chart.syncWait'), data: [], borderColor: '#b7791f', backgroundColor: 'transparent', borderWidth: 2, fill: false, pointRadius: 0, tension: 0.3},
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
  // 懒创建：canvas 在 x-if="kernels.enabled" 里,init() 时还不存在,数据到了才建
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

// 业务形态 → 三个 SLO 默认值(TTFT/TPOT/E2E p99 ms),同 Live/Autopilot 一套数值
// (workload.py / diagnosis_config._WORKLOAD_SLA);custom = 全手动,不套默认。
const BENCH_SLO_SHAPES = {
  chat: [1000, 50, 3000], rag: [3000, 50, 8000], agent: [1000, 50, 15000],
  reasoning: [1000, 30, 90000], code: [100, 20, 2000], custom: null,
};

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
      sloShape: 'chat',
      sloTtft: 1000, sloTpot: 50, sloE2e: 3000,   // 可选：清空即不设该项约束
      prompt_source: 'synthetic',
    },
    promptSources: [
      { value: 'synthetic', label: t('bench.promptSourceSynthetic'), uses_prompt_tokens: true,
        description: t('bench.promptSourceDesc') },
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
        // Endpoint：优先用后端解析出的真实 vLLM 端点(从启动 cmdline 的 --host/--port,
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
        const r = await fetch('/api/bench/prompt-sources?lang=' + _uiLang()).then(r => r.json());
        if (r.sources && r.sources.length) {
          this.promptSources = r.sources;
        }
      } catch (e) {
        console.warn('[bench] prompt-sources discovery failed:', e);
      }
      await this.refresh();
      this._timer = setInterval(() => this.refresh(), 3000);
      // 切语言时重取 prompt-source 标签/描述(后端按 ?lang= 出双语)
      this.$watch('$store.i18n.lang', async () => {
        try {
          const r = await fetch('/api/bench/prompt-sources?lang=' + _uiLang()).then(x => x.json());
          if (r.sources && r.sources.length) this.promptSources = r.sources;
        } catch (e) { /* fail-open */ }
      });
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
      if (sec < 60) return t('bench.agoSeconds', {s: sec.toFixed(0)});
      if (sec < 3600) return t('bench.agoMinutes', {m: (sec / 60).toFixed(0)});
      if (sec < 86400) return t('bench.agoHours', {h: (sec / 3600).toFixed(1)});
      return t('bench.agoDays', {d: (sec / 86400).toFixed(1)});
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

    // ===== 压测结果对比：任选两个 run,A=先选(基准),B=后选,Δ=B 相对 A =====
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
      return t('bench.concurrencyLabel', {c: s.concurrency, p: s.prompt_tokens, o: s.output_tokens, l: len});
    },
    // 对比卡数据：逐指标 A/B 双横条(按本指标 max 归一,免得 ms 与 tok/s 挤同轴)+ Δ%。
    // 延迟类越低越好,吞吐越高越好;|Δ|<2% 视为持平(压测运行间噪声)
    cmpTable() {
      const pair = this.cmpRuns();
      if (!pair) return [];
      const [A, B] = pair;
      const g = (r, p) => p.split('.').reduce((o, k) => (o == null ? null : o[k]), r);
      const defs = [
        { label: t('bench.ttftAvg'), path: 'client_metrics.ttft_ms.mean', lower: true, unit: 'ms' },
        { label: 'TTFT p99',  path: 'client_metrics.ttft_ms.p99',  lower: true, unit: 'ms' },
        { label: t('bench.tpotAvg'), path: 'client_metrics.tpot_ms.mean', lower: true, unit: 'ms' },
        { label: 'TPOT p99',  path: 'client_metrics.tpot_ms.p99',  lower: true, unit: 'ms' },
        { label: t('bench.e2eAvg'),  path: 'client_metrics.e2e_ms.mean',  lower: true, unit: 'ms' },
        { label: 'E2E p99',   path: 'client_metrics.e2e_ms.p99',   lower: true, unit: 'ms' },
        { label: t('bench.outputThroughput'), path: 'client_metrics.output_throughput_tps', lower: false, unit: 'tok/s' },
        { label: t('bench.completionErrors'), path: 'client_metrics.ok', path2: 'client_metrics.errors', lower: false, unit: '' },
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
        // "完成 / 错误"特例：数值文案带上错误数
        const fa = d.path2 ? `${f(a)} / ${g(A, d.path2) ?? '—'}` : f(a);
        const fb = d.path2 ? `${f(b)} / ${g(B, d.path2) ?? '—'}` : f(b);
        return { label: d.label, aText: fa, bText: fb, barA: bar(a), barB: bar(b), pct, good };
      });
    },

    // ===== SLO 约束：业务形态套三个默认值,仍可单独清空/改小 =====
    onSloShapeChange() {
      const s = BENCH_SLO_SHAPES[this.form.sloShape];
      if (s) { this.form.sloTtft = s[0]; this.form.sloTpot = s[1]; this.form.sloE2e = s[2]; }
      // custom：不动,全手动(留着用户已填的值)
    },
    buildSloSpec() {
      const parts = [];
      const add = (metric, v) => {
        if (v === '' || v == null || isNaN(v)) return;   // 空 = 不设该项约束
        parts.push(`${metric}:p99<=${v}ms`);
      };
      add('ttft', this.form.sloTtft);
      add('tpot', this.form.sloTpot);
      add('e2e', this.form.sloE2e);
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
          slo: this.buildSloSpec() || null,
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
          alert(t('bench.submitError', {e: err.detail || r.status}));
          return;
        }
        await this.refresh();
      } catch (e) {
        alert(t('bench.submitException', {e: e}));
      } finally {
        this.submitting = false;
      }
    },
  };
}

function rulesTab() {
  // 命中诊断的 rule_id 直接就是瓶颈字母(A 双低 / B 带宽瓶颈 / C 算力瓶颈 / D 容量瓶颈);其它一律 N。
  const BOTTLENECK = ['A', 'B', 'C', 'D'];
  return {
    rules: [], expandedDiag: [], cfg: {},

    async init() { await this.load(); },
    async load() {
      const d = await fetch('/api/diagnosis_rules').then(r => r.json()).catch(() => ({}));
      this.rules = d.rules || [];
      this.cfg = d.config || {};      // 阈值：操作点 gauge 标低/高刻度用
    },
    _rule(id) { return this.rules.find(x => x.id === id); },
    // 操作点 gauge：当前值落在哪个区 → far(离上限,双低侧)/ mid(上升中)/ wall(逼近上限)
    roofZone(v, low, high) {
      if (v == null) return 'na';
      if (high != null && v >= high) return 'wall';
      if (low != null && v >= low) return 'mid';
      return 'far';
    },
    pctClamp(v) { return Math.max(0, Math.min(100, (v || 0) * 100)); },   // 0-1 值 → 0-100
    pctRaw(v) { return Math.max(0, Math.min(100, v || 0)); },             // 已是 0-100(NVML)
    // 规则内容(名/推断/建议)按 rule_id 取译文;无键回退后端原值(中文规则文案)。
    ruleI18n(id, field, fallback) {
      const k = 'rule.' + id + '.' + field;
      const v = t(k);
      return v === k ? (fallback || '') : v;
    },
    ruleName(rule_id) { const r = this._rule(rule_id); return this.ruleI18n(rule_id, 'name', r ? r.name : rule_id); },
    ruleHyp(r) { return this.ruleI18n(r.id, 'hypothesis', r.hypothesis || ''); },   // 内置目录：规则的根因推断
    ruleSug(r) { return this.ruleI18n(r.id, 'suggestion', r.suggestion || ''); },   // 内置目录：规则的处方
    // 检测手段名(同 ruleI18n 机制,key = rule.{id}.detector.{det.key});/api/diagnosis_rules
    // 没有 ?lang=,det.name 恒为后端中文,靠这层客户端译文表接住(同 rule name/hypothesis/suggestion)。
    detName(rule_id, det) { return this.ruleI18n(rule_id, 'detector.' + det.key, det.name); },
    // 该瓶颈当前是否命中(diagnoses 来自父 dashboard 作用域,模板里传进来)→ 命中的诊断对象 或 null
    firedFor(id, diags) { return (diags || []).find(d => d.rule_id === id) || null; },
    // ── 诊断密集表：展开 + 检测手段(detector)──
    toggleDiag(id) { const i = this.expandedDiag.indexOf(id); if (i >= 0) this.expandedDiag.splice(i, 1); else this.expandedDiag.push(id); },
    diagRegime(rule_id) { const k = BOTTLENECK.includes(rule_id) ? rule_id : 'N'; return { k, label: t('regime.' + k) }; },
    fmtVal(v) { if (typeof v !== 'number') return String(v); return Number.isInteger(v) ? String(v) : (Math.abs(v) < 1 ? v.toFixed(3) : v.toFixed(1)); },
    _op(v, op, t) { switch (op) { case '>': return v > t; case '>=': return v >= t; case '<': return v < t; case '<=': return v <= t; case '==': return v === t; case '!=': return v !== t; } return false; },
    // 一条手段的条件串(checks 的 AND);metric/op/threshold 本身语言无关,只需翻译连接词。
    // det.name(手段人话名)走 detName() 的 ruleI18n 译文表,不在这里处理。
    detCond(det) {
      const and = _uiLang() === 'en' ? '  and  ' : '  且  ';
      return (det.checks || []).map(c =>
        `${c.aggregation}(${c.metric.replace(/^vllm\./, '').replace(/^kernel\.stall\./, 'stall.')}) ${c.op} ${c.threshold}`
      ).join(and);
    },
    // 该手段当前是否命中：从命中诊断的 context 复算(每个 check 都有数据且过阈值)。diag = firedFor() 的返回
    detFired(det, diag) {
      if (!diag || !diag.context || !det.checks) return false;
      return det.checks.every(c => {
        const v = diag.context[c.metric + ':' + c.aggregation];
        return v != null && this._op(v, c.op, c.threshold);
      });
    },
    // 该手段命中时取一个实测值展示
    detValue(det, diag) {
      if (!diag || !diag.context) return null;
      for (const c of (det.checks || [])) {
        const v = diag.context[c.metric + ':' + c.aggregation];
        if (v != null) return this.fmtVal(v);
      }
      return null;
    },
    // 命中了几条手段(n) / 共几条(m)
    hitCount(r, diags) {
      const d = this.firedFor(r.id, diags);
      return d ? (r.detectors || []).filter(det => this.detFired(det, d)).length : 0;
    },
  };
}

// 首页 SLO 面板：选业务形态 → 带出 TTFT/TPOT SLA 默认 → 监控当前 p99 是否达标(读父 dashboard 的 kpis)。
// 高级阈值只放 4 瓶颈检测阈值(MFU/MBU/KV);改完热生效(/api/diagnosis_config)。
function sloPanel() {
  // (TTFT_p99_ms, TPOT_p99_ms, E2E_p99_ms) —— 同 autopilot 的 WORKLOAD_SHAPES /
  // 后端 diagnosis_config._WORKLOAD_SLA 一套数值,三个 tab 说同一种 SLO 语言。
  const WORKLOAD_SLA = {
    chat: [1000, 50, 3000], rag: [3000, 50, 8000], agent: [1000, 50, 15000],
    reasoning: [1000, 30, 90000], code: [100, 20, 2000], custom: [2000, 50, 5000],
  };
  const ADV_LABELS = {
    min_running_reqs: ['cfg.min_running', 'reqs'],
    mfu_low_ratio: ['cfg.mfu_low', '0–1'],
    mfu_high_ratio: ['cfg.mfu_high', '0–1'],
    mbu_low_pct: ['cfg.mbu_low', '%'],
    mbu_high_pct: ['cfg.mbu_high', '%'],
    kv_pressure_ratio: ['cfg.kv', '0–1'],
    stall_memory_throttle_pct: ['cfg.stall_mem', '%'],
    stall_memory_dep_pct: ['cfg.stall_memdep', '%'],
    stall_math_pipe_pct: ['cfg.stall_math', '%'],
  };
  return {
    config: {}, cfgDraft: {}, workloadForms: [], active: false,
    advancedOpen: false, saving: false, toast: '', toastError: false,
    advLabels: ADV_LABELS,

    async init() { await this.load(); },
    async load() {
      const d = await fetch('/api/diagnosis_rules').then(r => r.json()).catch(() => ({}));
      this.config = d.config || {};
      this.workloadForms = d.workload_forms || [];
      this.active = !!d.active;
      this.cfgDraft = JSON.parse(JSON.stringify(this.config));
    },
    advKeys() { return Object.keys(ADV_LABELS).filter(k => k in this.cfgDraft); },
    onFormChange(form) {
      form = form || this.cfgDraft.workload_form;
      const sla = WORKLOAD_SLA[form];
      if (sla) {
        this.cfgDraft.sla_ttft_p99_ms = sla[0];
        this.cfgDraft.sla_tpot_p99_ms = sla[1];
        this.cfgDraft.sla_e2e_p99_ms = sla[2];
      }
    },
    dirty() { return JSON.stringify(this.cfgDraft) !== JSON.stringify(this.config); },
    resetConfig() { this.cfgDraft = JSON.parse(JSON.stringify(this.config)); },
    // 达标判定：当前 p99(父 kpis)<= SLA → true;缺值 → null(未知,不上色)
    slaPass(cur, sla) { return (cur == null || sla == null) ? null : cur <= sla; },
    async saveConfig() {
      this.saving = true;
      try {
        const r = await fetch('/api/diagnosis_config', {
          method: 'PUT', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(this.cfgDraft),
        });
        const body = await r.json().catch(() => ({}));
        if (!r.ok) { this.showToast(t('toast.saveFailed', {e: body.detail || r.status}), true); return; }
        this.config = body.config || this.config;
        this.cfgDraft = JSON.parse(JSON.stringify(this.config));
        this.showToast(body.applied ? t('toast.saveApplied') : t('toast.savePending'));
      } catch (e) { this.showToast(t('toast.error', {e: e}), true); }
      finally { this.saving = false; }
    },
    showToast(msg, error = false) {
      this.toast = msg; this.toastError = error;
      setTimeout(() => { this.toast = ''; }, 3000);
    },
  };
}

// Autopilot tab：轮询 /api/autopilot/status 展示诊断驱动的真实/模拟调优会话。
function autopilotTab() {
  // 中英双语字典 + lang-aware 取值函数(2026-07 修:曾经整个 tab 硬编码中文,理由是
  // 用 t('regime.'+bn) 拼句会拼出"本次持续诊断到Bandwidth bottleneck"这种中英混杂病句;
  // 正解不是放弃 i18n,是按语言给完整句子模板(而非拼接翻译碎片)——见下方各 narrative 函数。
  const VERDICT_ZH = {
    baseline: { t: '基线', c: 'base' }, kept: { t: '✓ 保留', c: 'kept' },
    reverted: { t: '↩ 回滚', c: 'rev' }, tie: { t: '≈ 持平', c: 'tie' }, done: { t: '■ 完成', c: 'stop' },
  };
  const VERDICT_EN = {
    baseline: { t: 'Baseline', c: 'base' }, kept: { t: '✓ Kept', c: 'kept' },
    reverted: { t: '↩ Reverted', c: 'rev' }, tie: { t: '≈ Tie', c: 'tie' }, done: { t: '■ Done', c: 'stop' },
  };
  const VERDICT = () => (_uiLang() === 'en' ? VERDICT_EN : VERDICT_ZH);
  // 停机归因 → 人话(用户反馈：session 跑完没说明白"为什么",光看轮次/判定看不出
  // 全局原因——同 stop_cause 枚举,见 runner.py append_stop 的 cause 取值)。
  const STOP_LABELS_ZH = {
    agent_done: 'Agent 判断已接近最优,主动收尾',
    no_candidates: '已无对症参数可试(诊断到的瓶颈没有安全参数能缓解,或负载/配置已到边界)',
    budget_rounds: '达到最大轮数上限',
    budget_time: '达到时间预算上限(还没跑完计划轮数)',
    no_improve_k: '连续多轮没有实质提升,判断已收敛',
    user_stop: '用户手动停止',
    failed: '会话出错终止',
  };
  const STOP_LABELS_EN = {
    agent_done: 'Agent judged it near-optimal and stopped on its own',
    no_candidates: 'No on-target parameters left to try (the diagnosed bottleneck has no safe parameter that helps, or load/config is already at its edge)',
    budget_rounds: 'Reached the maximum round budget',
    budget_time: 'Reached the time budget (planned rounds not yet finished)',
    no_improve_k: 'No real improvement for several rounds in a row — judged converged',
    user_stop: 'Stopped manually by the user',
    failed: 'Session aborted on error',
  };
  const STOP_LABELS = () => (_uiLang() === 'en' ? STOP_LABELS_EN : STOP_LABELS_ZH);
  // 瓶颈字母 → 人话(同后端 action_space.BOTTLENECK_LABEL)。
  const BOTTLENECK_LABEL_ZH = { A: '双低', B: '带宽瓶颈', C: '算力瓶颈', D: '容量瓶颈' };
  const BOTTLENECK_LABEL_EN = { A: 'Under-utilized', B: 'Bandwidth bottleneck', C: 'Compute bottleneck', D: 'Capacity bottleneck' };
  const BOTTLENECK_LABEL = () => (_uiLang() === 'en' ? BOTTLENECK_LABEL_EN : BOTTLENECK_LABEL_ZH);
  const bottleneckOther = () => (_uiLang() === 'en' ? 'Symptom/other' : '症状/其它');
  // 业务形态 WorkloadSpec(M1)：形态是唯一主维度——自带 bench 负载与默认 SLA(数值同后端
  // autopilot/workload.py)。"目标"(吞吐优先/延迟优先/性价比)UI 已去掉,调优统一"不破
  // SLA 前提下最大化吞吐"(业界惯例;objective.py 后端仍支持 target,只是 UI 不再暴露选择,
  // 免得用户在"形态"和"目标"两个维度间双重决策——ap-20260719-004104 那次泛形态+泛目标
  // 组合在轻载下 load_limited 交白卷,正是这种维度纠缠的产物)。load 是负载参数简写(p/o/c),
  // 本身就是数字+缩写,语言无关;仅 custom 的负载描述是自然语言,走 i18n key。
  const WORKLOAD_SHAPES = {
    chat:      { sla: [1000, 50, 3000],  load: 'p500/o128 · c64' },
    rag:       { sla: [3000, 50, 8000],  load: 'p4000/o256 · c16' },
    agent:     { sla: [1000, 50, 15000], load: 'p2000/o512 · c32' },
    reasoning: { sla: [1000, 30, 90000], load: 'p1000/o4096 · c16' },
    code:      { sla: [100, 20, 2000],   load: 'p300/o128 · c16' },
    custom:    { sla: null,              load: null },
  };
  return {
    // target 固定 throughput(不破 SLA 前提下最大化吞吐,业界惯例);UI 不再暴露"目标"选择,
    // 只留形态 —— 后端 objective.py 的 target 仍支持 latency/cost,供 CLI/API 直调用。
    obj: { workload: 'chat', ttft: 1000, tpot: 50, e2e: '' },
    budget: { rounds: 30, minutes: 120 },
    agentOpen: false,
    preset: 'openrouter',
    agent: { provider: '', base_url: 'https://openrouter.ai/api/v1', api_key: '', model: 'anthropic/claude-opus-4' },
    agentTest: '',
    agentTesting: false,
    advOpen: false,
    showPrompt: false,
    adv: { guidance: '', temperature: 0.4, timeout_s: 240, retries: 2 },
    lockedPrompt() { return t('ap.lockedPrompt'); },
    PRESETS: {
      openrouter:  { provider: '', base_url: 'https://openrouter.ai/api/v1', model: 'anthropic/claude-opus-4' },
      openai:      { provider: '', base_url: 'https://api.openai.com/v1',    model: 'gpt-4o' },
      deepseek:    { provider: '', base_url: 'https://api.deepseek.com/v1',  model: 'deepseek-chat' },
      kimi_coding: { provider: 'kimi_coding', base_url: 'https://api.kimi.com/coding/v1', model: 'kimi-for-coding', temperature: 0.4 },
      kimi:        { provider: '', base_url: 'https://api.moonshot.ai/v1',   model: 'kimi-k2.6', temperature: 0.6 },
      local:       { provider: '', base_url: 'http://localhost:11434/v1',    model: 'llama3.1' },
      custom:      { provider: '', base_url: '', model: '' },
    },
    applyPreset() {
      const p = this.PRESETS[this.preset];
      if (p) {
        this.agent.base_url = p.base_url;
        this.agent.model = p.model;
        this.agent.provider = p.provider || '';
        if (p.temperature != null) this.adv.temperature = p.temperature;
      }
    },
    async testAgent() {
      if (!this.agent.base_url || !this.agent.api_key || !this.agent.model) {
        this.agentTest = t('ap.needFields');
        setTimeout(() => this.agentTest = '', 2600);
        return;
      }
      this.agentTesting = true;
      this.agentTest = t('ap.connecting');
      const body = {
        agent: Object.assign({}, this.agent, {
          temperature: this.adv.temperature,
          timeout_s: this.adv.timeout_s,
        }),
      };
      try {
        const r = await fetch(this.apiUrl('/api/autopilot/agent-test'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const out = await r.json();
        this.agentTest = out.ok ? t('ap.connectionOk') : (t('ap.connectionFailed') + (out.error || `HTTP ${r.status}`)).slice(0, 80);
      } catch (e) {
        this.agentTest = t('ap.connectionFailed') + String(e).slice(0, 70);
      } finally {
        this.agentTesting = false;
        setTimeout(() => this.agentTest = '', 4500);
      }
    },
    session: null,
    running: false,
    stopping: false,
    shownRounds: [],
    pending: null,
    pollFailures: 0,
    drawerOpen: false,
    promoteOpen: false,
    expandedRounds: [],
    maxTps: 1,
    _poll: null,
    _syncSeq: 0,
    activeSessionId: '',

    init() { this._sync(); },          // 进页时拉一次：断线重连/已有 session 直接接管直播
    fmt(n) { return (n == null || !isFinite(n)) ? '—' : Number(n).toLocaleString('en-US'); },
    get baselineTps() { return (this.session && this.session.baseline_score) || (this.shownRounds[0] || {}).tps || 0; },
    _bestTps() { let b = 0; this.shownRounds.forEach(r => { if (r.decision === 'kept' && r.tps > b) b = r.tps; }); return b || this.baselineTps; },
    get best() { return { tps: this._bestTps(), cmd: (this.session && this.session.recommended_command) || (this.shownRounds.find(r => r.decision === 'kept') || {}).cmd || '' }; },
    get fallbackCount() { return this.shownRounds.filter(r => r.fallback).length; },
    // 结果总结：session 跑完只甩一个吞吐比值(×1.00 也说不清是"没提升"还是"基线就没达标"),
    // 用户反馈"很懵逼"——把 SLA 违规/回滚原因/停机原因拼成人话,别让用户自己倒推轮次表。
    // 只查 TTFT/TPOT(同 objective.py sla_ok):E2E 不进闸门,只在 Live 面板监控展示,不会
    // 导致这里的 reverted——如果还把 E2E 算进"违反 SLA",会跟后端实际判定对不上(真机
    // 2026-07-23:7B-AWQ 上 chat/code 形态基线 E2E 常年超标,但硬卡会把 TTFT/TPOT 真实
    // 变好的候选也一并判负,掩盖收益)。
    _violatesSla(sc) {
      const sla = (this.session && this.session.objective && this.session.objective.sla) || {};
      if (!sc) return [];
      const bad = [];
      if (sla.ttft_p99_ms != null && sc.ttft_p99_ms > sla.ttft_p99_ms) bad.push('TTFT');
      if (sla.tpot_p99_ms != null && sc.tpot_p99_ms > sla.tpot_p99_ms) bad.push('TPOT');
      return bad;
    },
    get resultSummary() {
      const rounds = (this.session && this.session.rounds) || [];
      const baseline = rounds.find(r => r.kind === 'baseline');
      const cands = rounds.filter(r => r.kind === 'candidate');
      const kept = cands.filter(r => r.decision === 'kept');
      const tie = cands.filter(r => r.decision === 'tie');
      const revertedSla = cands.filter(r => r.decision === 'reverted' && this._violatesSla(r.scorecard_after).length > 0);
      const revertedWorse = cands.filter(r => r.decision === 'reverted' && this._violatesSla(r.scorecard_after).length === 0);
      const baselineBad = this._violatesSla(baseline && baseline.scorecard_after);
      return {
        improved: kept.length > 0, triedCount: cands.length,
        keptCount: kept.length, tieCount: tie.length,
        revertedSlaCount: revertedSla.length, revertedWorseCount: revertedWorse.length,
        baselineViolatesSla: baselineBad.length > 0, baselineViolatedMetrics: baselineBad,
      };
    },
    get resultSummaryText() {
      const s = this.resultSummary;
      const en = _uiLang() === 'en';
      const parts = [];
      if (s.improved) {
        parts.push(en
          ? `Found ${s.keptCount} better configuration(s) and kept them; converged to the current best.`
          : `找到 ${s.keptCount} 个更优配置并保留,已收敛到当前最优。`);
      } else if (s.triedCount === 0) {
        parts.push(en
          ? 'No candidates were tried this session (stopped right after the baseline).'
          : '本次没跑出任何候选(基线之后即停止)。');
      } else if (s.baselineViolatesSla) {
        parts.push(en
          ? `The baseline itself didn't meet SLA (exceeded ${s.baselineViolatedMetrics.join('/')} SLA); `
            + `none of the ${s.triedCount} candidates that followed could pull it back within SLA either, so all were `
            + `reverted — this looks more like the SLA thresholds being too tight for this hardware/model than the `
            + `parameters being useless.`
          : `基线本身就没达标(超出 ${s.baselineViolatedMetrics.join('/')} SLA),后续 ${s.triedCount} `
            + `个候选也都没能拉回 SLA 内,因此全部回滚——更像是 SLA 阈值对当前硬件/模型偏紧,而不是参数没用。`);
      } else if (s.revertedSlaCount > 0 && s.revertedWorseCount === 0 && s.tieCount === 0) {
        parts.push(en
          ? `Tried ${s.triedCount} candidate(s), all reverted for breaking SLA — throughput couldn't be compared `
            + `while staying within SLA.`
          : `试了 ${s.triedCount} 个候选,全部因破坏 SLA 被回滚,吞吐没能在达标前提下比较。`);
      } else if (s.tieCount === s.triedCount && s.triedCount > 0) {
        parts.push(en
          ? `Tried ${s.triedCount} candidate(s); throughput changes were all within noise (judged as ties) — `
            + `already close to what the current configuration can do.`
          : `试了 ${s.triedCount} 个候选,吞吐变化都在噪声范围内(判定持平)——已接近当前配置能做到的上限。`);
      } else {
        parts.push(en
          ? `Tried ${s.triedCount} candidate(s): ${s.revertedWorseCount} measured worse and reverted, `
            + `${s.tieCount} tied, ${s.revertedSlaCount} broke SLA.`
          : `试了 ${s.triedCount} 个候选：${s.revertedWorseCount} 个实测更差被回滚,`
            + `${s.tieCount} 个持平,${s.revertedSlaCount} 个破 SLA。`);
      }
      return parts.join(' ');
    },
    // 主线逻辑(用户反馈,2026-07-22)：只看"结果"和"候选池"两段,拼不出"为什么会
    // 这么走"的因果链——本次诊断到什么瓶颈、中途有没有变、正因为这样才只从对应的
    // 参数池里选、又是为什么恰好在这一轮停。这里只讲因果转折,2-4 句话,不逐轮复述
    // (逐轮细节由 knobLedger 承担)。
    get sessionNarrativeText() {
      const rounds = (this.session && this.session.rounds) || [];
      const en = _uiLang() === 'en';
      const timeline = [];
      let lastBn = null;
      rounds.forEach(r => {
        const bn = r.diagnosis && r.diagnosis.bottleneck;
        if (bn && bn !== lastBn) { timeline.push({ round: r.round, bn }); lastBn = bn; }
      });
      if (!timeline.length) return '';
      const label = bn => BOTTLENECK_LABEL()[bn] || bottleneckOther();
      const stopLabels = STOP_LABELS();
      const parts = [];
      if (timeline.length === 1) {
        parts.push(en
          ? `This session was diagnosed as ${label(timeline[0].bn)} throughout, so every round's candidates came `
            + `from the parameter pool for that bottleneck.`
          : `本次从头到尾都诊断为${label(timeline[0].bn)},所以每轮候选都从这个瓶颈对症的参数池里选。`);
      } else {
        const switches = en
          ? timeline.slice(1).map(t => `switched to ${label(t.bn)} from round ${t.round}`).join(', ')
          : timeline.slice(1).map(t => `第 ${t.round} 轮起转为${label(t.bn)}`).join('、');
        parts.push(en
          ? `This session was first diagnosed as ${label(timeline[0].bn)}, then ${switches}; the parameter pool `
            + `switched targets along with the bottleneck.`
          : `本次先诊断为${label(timeline[0].bn)},${switches},参数池跟着瓶颈变化切换过对症目标。`);
      }
      const stopRound = [...rounds].reverse().find(r => r.kind === 'stop');
      const cause = stopRound && stopRound.stop_cause;
      if (cause) {
        const aas = (this.session && this.session.action_space_summary) || {};
        const untried = (aas.relevant_knobs || []).filter(k => !(aas.tried_knobs || []).includes(k));
        const budgetExhausted = cause === 'budget_rounds' || cause === 'budget_time';
        const causeLabel = stopLabels[cause] || cause;
        if (budgetExhausted && untried.length) {
          parts.push(en
            ? `Eventually stopped at round ${stopRound.round} — ${causeLabel}; ${untried.length} on-target `
              + `parameter(s) never got a turn (see the candidate pool below).`
            : `最终第 ${stopRound.round} 轮${causeLabel},还有 ${untried.length} `
              + `个对症参数没轮到试(具体见下方候选池)。`);
        } else if (cause === 'no_candidates') {
          parts.push(en
            ? `By round ${stopRound.round} there were no more safe, on-target parameters left to try for `
              + `${label(lastBn)} — judged as the ceiling of what the current configuration can do.`
            : `第 ${stopRound.round} 轮已经没有对${label(lastBn)}安全对症的参数可试,`
              + `判定到了当前配置能做的上限。`);
        } else {
          parts.push(en
            ? `Eventually stopped at round ${stopRound.round} — ${causeLabel}.`
            : `最终第 ${stopRound.round} 轮${causeLabel}。`);
        }
      }
      return parts.join(' ');
    },
    // 逐轮账本(用户反馈,2026-07-22/23)：先是只甩"参数名(结果)",后来嫌不具体——现在
    // 每行说清楚"调了哪个参数、从什么值改到什么值、为什么这么改"。"为什么"不解析 agent
    // 自由文本 rationale(不同 agent/模型措辞差异大,截断容易语义碎片化),改用本轮诊断到
    // 的瓶颈标签——每轮必有、够简短,也跟上面"为什么"小节的因果链呼应,不是另起一套解释。
    get knobLedger() {
      const rounds = (this.session && this.session.rounds) || [];
      const en = _uiLang() === 'en';
      const fmtVal = v => (v == null) ? '—' : (typeof v === 'boolean' ? (v ? (en ? 'on' : '开') : (en ? 'off' : '关')) : String(v));
      const verdict = VERDICT();
      const label = bn => BOTTLENECK_LABEL()[bn] || bottleneckOther();
      return rounds.filter(r => r.kind === 'candidate' && r.action && r.action.knob).map(r => {
        const v = verdict[r.decision] || { t: r.decision || '', c: '' };
        const d = r.delta_pct;
        const bn = r.diagnosis && r.diagnosis.bottleneck;
        return {
          round: r.round, param: r.action.knob,
          fromText: fmtVal(r.action.from), toText: fmtVal(r.action.to),
          whyText: bn ? (en ? `To ease ${label(bn)}` : `为了缓解${label(bn)}`) : '',
          verdictText: v.t, verdictClass: v.c,
          deltaText: (d == null || !isFinite(d)) ? '' : `${d > 0 ? '+' : ''}${d.toFixed(1)}%`,
        };
      });
    },
    // 用户反馈(2026-07-22)：每次 session 只调 2-3 个参数,不知道剩下的参数是"不该调"
    // 还是"没顾上调"。这里把后端 action_space_summary(见 runner.py _build_action_space_summary)
    // 拼成人话：全量参数按跳过原因分类计数,再对照本次实际诊断到的瓶颈,列出对症但没试到的参数。
    get actionSpaceSummaryText() {
      const s = (this.session && this.session.action_space_summary) || {};
      if (!s.total) return '';
      const en = _uiLang() === 'en';
      const label = bn => BOTTLENECK_LABEL()[bn] || bottleneckOther();
      const parts = [];
      parts.push(en
        ? `Tunable scope: of all ${s.total} vLLM parameters, ${s.default_on_count} are already self-tuned to `
          + `their optimal default at engine startup, ${s.unsupported_count} aren't supported by the current `
          + `vLLM version, and ${s.precision_excluded_count} would lower precision so they're withheld by policy `
          + `— leaving ${s.considerable_count} as the real candidate pool.`
        : `可调范围：全部 ${s.total} 个 vLLM 参数里,${s.default_on_count} 个已是引擎启动时自调的`
          + `最优默认值、${s.unsupported_count} 个当前 vLLM 版本不支持、${s.precision_excluded_count} `
          + `个会降精度按策略不提供,剩下 ${s.considerable_count} 个才是真正的候选池。`);
      const seen = (s.bottlenecks_seen || []).map(label).join('/');
      if (seen) {
        const rel = s.relevant_knobs || [], tried = s.tried_knobs || [];
        const untried = rel.filter(k => !tried.includes(k));
        if (en) {
          parts.push(`This session was consistently diagnosed with ${seen}; the candidate pool has ${rel.length} `
            + `on-target parameter(s)` + (rel.length ? ` (${rel.join(' / ')})` : '')
            + `, and ${tried.length} were actually tried (see the round-by-round ledger above)`
            + (untried.length
              ? `; ${untried.length} on-target parameter(s) never got a turn (${untried.join(' / ')}, mostly `
                + `because the round/time budget ran out first).`
              : ' — every on-target one was tried.')
            + ` The rest of the candidate pool wasn't proposed because it doesn't target this session's bottleneck.`);
        } else {
          parts.push(`本次持续诊断到${seen},候选池里对症的有 ${rel.length} 个`
            + (rel.length ? `(${rel.join(' / ')})` : '') + `,实际试了 ${tried.length} 个(结果见上方逐轮账本)`
            + (untried.length
              ? `,还剩 ${untried.length} 个对症但没轮到(${untried.join(' / ')},多因轮数/时间预算先耗尽)。`
              : '——对症的都试过了。')
            + `候选池里其余参数因为跟这次的瓶颈不对症,没有被提议。`);
        }
      }
      return parts.join(' ');
    },
    get etaText() {
      // 按已完成轮次平均节奏外推的上界(提前收敛/时间预算会更早停)
      if (!this.running) return '';
      const ts = this.shownRounds.map(r => Date.parse(r.ts || '')).filter(Number.isFinite);
      const done = this.shownRounds.filter(r => r.kind === 'candidate').length;
      const total = Number(this.budget.rounds) || 0;
      if (ts.length < 2 || !total) return '';
      const perMin = (ts[ts.length - 1] - ts[0]) / (ts.length - 1) / 60000;
      const left = Math.max(0, total - done);
      if (!left) return _uiLang() === 'en' ? 'Finishing up…' : '收尾中';
      const mins = Math.max(1, Math.round(perMin * left));
      return _uiLang() === 'en' ? `≤${mins} min remaining (est.)` : `预计剩余 ≤${mins} min`;
    },
    get promote() { return (this.session && this.session.promote_package) || null; },
    get promoteDiff() { return (this.promote && this.promote.diff && this.promote.diff.changes) || []; },
    get promoteRisks() { return (this.promote && this.promote.risk_notes) || []; },
    get promoteChecklist() { return (this.promote && this.promote.checklist) || []; },
    bridgeBase() {
      const h = window.location.hostname;
      if (!h || h === 'localhost' || h === '127.0.0.1') return '';
      return `${window.location.protocol}//${h}:8776`;
    },
    apiUrl(path) { return `${this.bridgeBase()}${path}`; },
    hasBridge() { return !!this.bridgeBase(); },
    startLabel() {
      const en = _uiLang() === 'en';
      const real = this.hasBridge();
      if (this.running) return real ? (en ? 'Tuning (real)…' : '真实调优中…') : (en ? 'Tuning…' : '调优中…');
      return en ? 'Run tuning' : '执行调优';
    },
    applyWorkload() {
      // 形态是唯一主维度：直接套用形态 SLA(之后仍可手改);custom 不动任何字段(全手动透传)。
      const s = WORKLOAD_SHAPES[this.obj.workload];
      if (s && s.sla) {
        this.obj.ttft = s.sla[0];
        this.obj.tpot = s.sla[1];
        this.obj.e2e = s.sla[2];
      }
    },
    workloadHint() {
      const s = WORKLOAD_SHAPES[this.obj.workload];
      if (!s) return '';
      const load = s.load == null ? t('ap.customLoadNote') : s.load;
      return _uiLang() === 'en' ? `Load ${load}` : `负载 ${load}`;
    },
    stateLabel() {
      if (!this.session) return '';
      const en = _uiLang() === 'en';
      if (this.session.state === 'failed') return en ? 'Failed' : '失败';
      if (this.session.state === 'running') {
        return this.hasBridge() ? (en ? 'Tuning (real)' : '真实调优中') : (en ? 'Tuning in progress' : '调优进行中');
      }
      return en ? 'Done' : '已完成';
    },
    _phaseLabel(phase) {
      const en = _uiLang() === 'en';
      return (en ? {
        baseline: 'Baseline',
        observe: 'Reading diagnosis',
        propose: 'Waiting on agent decision',
        apply: 'Starting candidate sandbox',
        benchmark: 'Benchmarking',
        decide: 'Scoring the gain',
        restore: 'Restoring best',
        finalize: 'Finalizing',
      } : {
        baseline: '建立基线',
        observe: '读取诊断',
        propose: '等待 Agent 决策',
        apply: '启动候选沙盒',
        benchmark: '真实压测中',
        decide: '判定收益',
        restore: '恢复 best',
        finalize: '收尾恢复',
      })[phase] || (en ? 'Running' : '运行中');
    },
    // propose 阶段挤了 6 种不同的事(P0 剪枝/候选检索/思考/决策/网络重试/收敛判断),
    // 共用"等待 Agent 决策"一个标签会看花——按消息前缀拆细,其余阶段维持原标签。
    // 注:后端 runner.py 的 ev.message 本身恒为中文(事件日志未做 lang 分支,是已知的
    // 后续待办);这里只翻译匹配后"展示给用户看"的短标签,匹配条件仍按后端中文原文比对。
    _eventLabel(ev) {
      const msg = ev.message || '';
      const en = _uiLang() === 'en';
      if (ev.phase === 'propose') {
        // 匹配串是后端 runner.py/agent.py 事件文案的前缀/子串——那边现在按 session lang
        // 双语(self._msg(zh, en)),两种都要认。"agent 思考："/"agent 决策：" 前缀本身
        // 保持中文不变(内部协议标记,从不直接展示给用户,见 runner.py 对应注释)。
        if (msg.startsWith('P0 预测剪枝') || msg.startsWith('P0 predictive pruning'))
          return en ? 'P0 pruning' : 'P0 剪枝';
        if (msg.startsWith('诊断命中') || msg.startsWith('Diagnosed '))
          return en ? 'Candidate lookup' : '候选检索';
        if (msg.startsWith('agent 思考：')) return en ? 'Agent thinking' : 'Agent 思考';
        if (msg.startsWith('agent 决策：')) return en ? 'Agent decision' : 'Agent 决策';
        if (msg.startsWith('agent 调用失败') || msg.startsWith('agent call failed'))
          return en ? 'Retrying' : '网络重试';
        if (msg.includes('LLM 调用失败') || msg.includes('LLM call failed'))
          return en ? 'LLM fallback' : 'LLM 兜底';
        if (msg.includes('没有对症候选') || msg.includes('近最优')
            || msg.includes('No on-target candidates') || msg.includes('near-optimal'))
          return en ? 'Convergence check' : '收敛判断';
      }
      return this._phaseLabel(ev.phase);
    },
    _eventTime(ev) {
      const t = Date.parse(ev && ev.ts_wall);
      return Number.isFinite(t) ? t : Date.now();
    },
    _eventAge(ev) {
      const s = Math.max(0, Math.round((Date.now() - this._eventTime(ev)) / 1000));
      const en = _uiLang() === 'en';
      if (en) return s < 90 ? `${s}s ago` : `${Math.round(s / 60)}min ago`;
      return s < 90 ? `${s}s 前` : `${Math.round(s / 60)}min 前`;
    },
    _benchProgress(ev) {
      if (!ev || ev.phase !== 'benchmark') return '';
      const bench = ((ev.detail || {}).bench) || {};
      const total = Number(bench.duration_s || 0) + Number(bench.warmup_s || 0);
      if (!total) return '';
      const elapsed = Math.max(0, Math.round((Date.now() - this._eventTime(ev)) / 1000));
      return `bench ${Math.min(elapsed, total)}s / ${total}s`;
    },
    _latestReasoning(allEvents, round) {
      // 本轮最新的 agent 思考/决策(全量事件里找,不受 5 条滚动窗口影响)——
      // 心跳(15s 一条)量远大于 propose 事件(每轮 1-2 条),纯"取最新事件"会把
      // 思考/决策挤出窗口(真机实测：2.5 分钟后被 18+ 条心跳冲掉)。
      let thinking = null, decision = null;
      for (let i = allEvents.length - 1; i >= 0; i--) {
        const e = allEvents[i];
        if (Number(e.round) !== round) continue;
        const msg = e.message || '';
        if (!decision && msg.startsWith('agent 决策：')) decision = e;
        if (!thinking && msg.startsWith('agent 思考：')) thinking = e;
        if (decision && thinking) break;
      }
      if (!decision && !thinking) return null;
      const d = (decision && decision.detail) || {};
      return {
        // message = "knob from→to —— rationale前110字"(服务端为滚动事件流截断拼的);
        // 只取 " —— " 前半段(参数变更),完整 rationale 用下面的 detail.rationale——
        // 否则会先显示截断预览、紧接着又完整重复一遍。
        knobLine: decision ? decision.message.replace(/^agent 决策：/, '').split(' —— ')[0].trim() : '',
        rationale: d.rationale || '',
        expectedEffect: d.expected_effect || '',
        guardrailNotes: d.guardrail_notes || '',
        evidence: (d.evidence_refs || []).slice(0, 4),
        thinking: (thinking && thinking.detail && thinking.detail.thinking) || '',
      };
    },
    _pendingFromStatus(s, term) {
      if (term) return null;
      const allEvents = s.events || [];
      const events = allEvents.slice(-5);
      const ev = events[events.length - 1] || null;
      if (!ev) {
        return { phase: 'baseline', round: 0, title: t('ap.startingRealTuning'), hyp: t('ap.creatingSession'), events: [], progress: '' };
      }
      const completedRounds = (s.rounds || []).map(r => Number(r.round)).filter(Number.isFinite);
      const maxCompletedRound = completedRounds.length ? Math.max(...completedRounds) : -1;
      const evRound = Number(ev.round);
      if (Number.isFinite(evRound) && evRound <= maxCompletedRound) return null;
      const detail = ev.detail || {};
      const action = detail.flag ? `${detail.flag} ${detail.from} → ${detail.to}` : '';
      const evidence = (detail.evidence_refs || []).slice(0, 3).join(' · ');
      // agent 思考/决策事件的完整内容已经在下面的常驻推理框里显示;这里再放一遍
      // ev.message(服务端为滚动流截断过的版本)只会造出"截断预览 + 完整重复"的观感。
      // 注:后端 f-string 用的是全角冒号"："(U+FF1A),不是半角":"——这条正则曾经写成
      // 半角,导致这个判断从未真正命中(遗留 bug,顺手修)。
      const isReasoningEvent = /^agent (决策|思考)：/.test(ev.message || '');
      let hyp = isReasoningEvent ? '' : (ev.message || this._phaseLabel(ev.phase));
      if (hyp && evidence) hyp += ` · ${evidence}`;
      const curRound = Number.isFinite(evRound) ? evRound : maxCompletedRound + 1;
      return {
        phase: ev.phase,
        round: curRound,
        title: this._eventLabel(ev),
        hyp,
        action,
        progress: this._benchProgress(ev),
        age: this._eventAge(ev),
        reasoning: this._latestReasoning(allEvents, curRound),   // 常驻,不被心跳挤走
        events: events.slice().reverse().map(x => Object.assign({}, x, {
          label: this._eventLabel(x),
          age: this._eventAge(x),
        })),
      };
    },

    async start() {
      if (this.hasBridge() && (!this.agent.api_key || !this.agent.base_url || !this.agent.model)) {
        this.agentTest = t('ap.needAgentConfig');
        this.agentOpen = true;
        setTimeout(() => this.agentTest = '', 3500);
        return;
      }
      const body = {
        workload: this.obj.workload,      // 业务形态：bridge/run.py 侧展开 bench 负载参数
        objective: {
          // 固定 throughput——不破 SLA 前提下最大化吞吐,UI 不再暴露"目标"选择(见上方注释)。
          target: 'throughput',
          sla: { ttft_p99_ms: Number(this.obj.ttft) || null, tpot_p99_ms: Number(this.obj.tpot) || null,
                 e2e_p99_ms: Number(this.obj.e2e) || null },
        },
        budget: { rounds: Number(this.budget.rounds) || 30, minutes: Number(this.budget.minutes) || 120 },
        agent: Object.assign({}, this.agent, {
          guidance: this.adv.guidance, temperature: this.adv.temperature,
          timeout_s: this.adv.timeout_s, retries: this.adv.retries,
          lang: _uiLang(),   // agent 自由文本(rationale/思考过程)跟随界面语言,而非默认英文
        }),
      };
      try {
        const r = await fetch(this.apiUrl('/api/autopilot/start'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        if (r.status === 409) { this.agentTest = t('ap.sessionAlreadyRunning'); setTimeout(() => this.agentTest = '', 2500); return; }
        if (!r.ok) { throw new Error(`HTTP ${r.status}`); }
        const out = await r.json().catch(() => ({}));
        this.running = true; this.stopping = false; this.shownRounds = []; this.expandedRounds = [];
        this.activeSessionId = out.session_id || '';
        this.session = { session_id: this.activeSessionId, state: 'running', rounds: [], events: [] };
        this.pending = { phase: 'baseline', title: t('ap.startingRealTuning'), hyp: t('ap.creatingSession'), events: [], progress: '' };
        this._startPoll();
      } catch (e) { this.agentTest = this.bridgeBase() ? t('ap.bridgeNotConnected') : t('ap.startFailed'); setTimeout(() => this.agentTest = '', 3500); }
    },
    async stop() {
      if (!this.running || this.stopping) return;
      this.stopping = true;
      this.agentTest = t('ap.stoppingAndRestoring');
      this.pending = Object.assign({}, this.pending || {}, {
        phase: 'restore',
        title: t('ap.stopBtn'),
        hyp: t('ap.terminatingSandbox'),
        progress: t('ap.stopRequestedNoKeep'),
      });
      try {
        const r = await fetch(this.apiUrl('/api/autopilot/stop'), { method: 'POST' });
        const out = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(out.error || `HTTP ${r.status}`);
        this.agentTest = out.stopped ? t('ap.stoppedRestoring') : t('ap.noRunningSession');
        await this._sync();
      } catch (e) {
        this.agentTest = t('ap.stopFailed') + String(e.message || e).slice(0, 70);
      } finally {
        setTimeout(() => { this.agentTest = ''; }, 3500);
      }
    },

    _startPoll() { if (this._poll) clearInterval(this._poll); this._sync(); this._poll = setInterval(() => this._sync(), 2000); },
    async _sync() {
      // setInterval 每 2s 发一次,不等上一发落地——候选轮里 vLLM 崩溃重启/恢复 best
      // 这类慢操作能让某次请求晚到,若后到的旧响应覆盖了先到的新响应(轮次更多、已
      // 终态),UI 会永久卡在那个旧快照上(term 时已 clearInterval,没有下一轮来纠正)。
      // 真机复现：R2(候选崩溃重试)拖慢那次请求,R3(stop)的响应先到、poll 已停,
      // R2 请求的响应姗姗来迟,把 shownRounds 覆盖回只有 2 轮——序号哨兵防这个。
      const seq = ++this._syncSeq;
      let s; try { s = await fetch(this.apiUrl('/api/autopilot/status')).then(x => x.json()); } catch (e) {
        if (seq !== this._syncSeq) return;           // 期间已有更新的请求,这次报错作废
        if (this.running) {
          this.pollFailures += 1;
          if (this.pending) this.pending.progress = t('ap.statusRefreshRetry');
          if (this.pollFailures < 3) return;
          this.running = false;
          this.stopping = false;
          this.pending = null;
          this.session = Object.assign({}, this.session || {}, { state: 'failed', error: 'status fetch failed' });
          this.agentTest = this.hasBridge() ? t('ap.bridgeUnreachable') : t('ap.statusUnreachable');
          setTimeout(() => this.agentTest = '', 3500);
        }
        return;
      }
      if (seq !== this._syncSeq) return;              // 过期响应,不覆盖更新的状态
      this.pollFailures = 0;
      if (!s || s.state === 'idle') { return; }
      if (this.activeSessionId && s.session_id && s.session_id !== this.activeSessionId) {
        return;
      }
      const bridgeStopped = s.bridge && s.bridge.running === false &&
        ['starting', 'baselining', 'proposing', 'applying', 'warming_up', 'benchmarking', 'deciding', 'finalizing'].includes(s.state);
      const state = bridgeStopped ? 'stopped' : s.state;
      const term = ['done', 'stopped', 'failed'].includes(state);
      // Cold-opening the tab used to suppress a terminal session entirely so it
      // wouldn't look like it "just finished" — but the recommended command and
      // scorecard are the whole point of running Autopilot, and this is a
      // single-tenant/single-session tool (one GPU, one session at a time), so
      // hiding the last result on a simple reload just throws away the answer.
      // Show it, but flag it as a past result so the framing stays honest.
      const isColdLoadedTerminal = term && !this.session && !this.running;
      this.session = Object.assign({}, s, { state: state === 'failed' ? 'failed' : (term ? 'done' : 'running'),
                                            coldLoaded: isColdLoadedTerminal });
      this.shownRounds = (s.rounds || []).map(r => this._map(r));
      this.maxTps = Math.max(1, ...this.shownRounds.map(r => r.tps || 0));
      this.shownRounds.forEach(r => { r.barPct = r.tps ? Math.max(6, (r.tps / this.maxTps) * 100) : 0; });
      this.pending = this._pendingFromStatus(s, term);
      this.running = !term;
      if (term) {
        this.stopping = false;
        this.activeSessionId = '';
        if (this._poll) { clearInterval(this._poll); this._poll = null; }
      }
    },

    _map(r) {
      const verdict = VERDICT();
      const v = verdict[r.decision] || verdict.kept;
      const sa = r.scorecard_after, sb = r.scorecard_before;
      const sc = (m) => m ? { tps: Math.round(m.output_tps), ttft: Math.round(m.ttft_p99_ms), tpot: Math.round(m.tpot_p99_ms) } : null;
      const dg = r.diagnosis || {};
      return {
        round: r.round, kind: r.kind, decision: r.decision, verdict: v.t, vcls: v.c,
        ts: r.ts_wall || '',
        thinking: r.agent_thinking || '',
        fallback: (r.action && r.action.llm_fallback) || null,
        diag: (dg.evidence_refs || []).join(' · '),
        hyp: r.rationale || '', rationale: r.rationale || '',
        changed: (r.action && r.action.knob) ? `${r.action.flag} ${r.action.from}→${r.action.to}` : null,
        cmd: r.command || '',
        snap: dg.metrics ? Object.entries(dg.metrics).map(([k, val]) => `${k}=${val}`).join(' · ') : '',
        evidence: r.evidence_refs || [], expected: '',
        tps: sa ? sa.output_tps : null,
        delta: (r.delta_pct != null) ? r.delta_pct / 100 : null,
        scoreBefore: sc(sb), scoreAfter: sc(sa),
        decideLogic: this._decideLogic(r),
      };
    },
    _decideLogic(r) {
      const en = _uiLang() === 'en';
      const a = this.fmt(r.objective_score_after), b = this.fmt(r.objective_score_before);
      if (r.decision === 'baseline') {
        return en ? 'Set as initial best (baseline; every later candidate compares against it + best-so-far)'
                   : '设为初始 best(基准线,后续候选都跟它 + best-so-far 比)';
      }
      if (r.decision === 'kept') {
        return en ? `objective_score ${a} exceeds best ${b}'s noise margin and sla_ok → kept, set as new best`
                   : `objective_score ${a} 超 best ${b} 的噪声边界 且 sla_ok → 保留,设为新 best`;
      }
      if (r.decision === 'reverted') {
        return en ? 'Candidate score unacceptable (broke SLA / failed to start / high error rate) → reverted, best kept'
                   : '候选 score 不可接受(破 SLA / 起不来 / 高错误率)→ 回滚,保留 best';
      }
      if (r.decision === 'tie') {
        return en ? `Gain within the noise margin (${a} vs best ${b}) → recorded as a tie, best unchanged`
                   : `收益在噪声边界内(${a} vs best ${b})→ 记平,不替换 best`;
      }
      if (r.decision === 'done') {
        return r.rationale || (en ? 'agent done:true → finalize back to best' : 'agent done:true → 收尾回 best');
      }
      return '';
    },

    copyText(text, e, label) {
      if (!text) return;
      try { navigator.clipboard.writeText(text); } catch (_) {}
      if (e && e.target) {
        const old = label || e.target.textContent;
        e.target.textContent = t('ap.copied');
        setTimeout(() => { e.target.textContent = old; }, 1800);
      }
    },
    copyCmd(e) { this.copyText(this.best.cmd, e, t('common.copy')); },
    toggleRow(n) { const i = this.expandedRounds.indexOf(n); if (i >= 0) this.expandedRounds.splice(i, 1); else this.expandedRounds.push(n); },
    dlt(r, f) { return r.scoreBefore ? (r.scoreAfter[f] - r.scoreBefore[f]) : 0; },
    traceMarkdown() {
      const en = _uiLang() === 'en';
      let md = en ? '# Autopilot Reasoning Trace\n\n' : '# Autopilot 推理轨迹\n\n';
      this.shownRounds.forEach(r => {
        md += `## R${r.round} — ${r.verdict}\n`;
        if (r.snap) md += en ? `- Diagnosis snapshot: ${r.snap}\n` : `- 诊断快照： ${r.snap}\n`;
        if (r.rationale) md += en ? `- Agent reasoning: ${r.rationale}\n` : `- Agent 推理： ${r.rationale}\n`;
        if (r.evidence && r.evidence.length) md += en ? `- Evidence refs: ${r.evidence.join(', ')}\n` : `- 证据引用： ${r.evidence.join(', ')}\n`;
        if (r.cmd) md += en ? `- This round's command: \`${r.cmd}\`\n` : `- 本轮命令： \`${r.cmd}\`\n`;
        if (r.scoreAfter) md += `${en ? '- Bench' : '- 压测'}： output_tps ${r.scoreBefore ? r.scoreBefore.tps + '→' : ''}${r.scoreAfter.tps}\n`;
        if (r.decideLogic) md += en ? `- Decision: ${r.decideLogic}\n` : `- 判定： ${r.decideLogic}\n`;
        md += '\n';
      });
      return md;
    },
    copyTrace(e) { try { navigator.clipboard.writeText(this.traceMarkdown()); } catch (_) {}; e.target.textContent = t('ap.copied'); setTimeout(() => e.target.textContent = t('ap.copyMarkdown'), 2000); },
    downloadTrace() {
      const blob = new Blob([JSON.stringify(this.shownRounds, null, 2)], { type: 'application/json' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob); a.download = 'autopilot-trace.json'; a.click();
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
    // Deep Evidence(阶段 2 PC Sampling 按需取证)：为什么这些 kernel 慢
    deep: { running: false, available_now: false, result: null, findings: [], error: null },
    // #6 改动前后对比：两个 Deep Evidence 快照(A=改动前基准,B=改动后)。存 localStorage,
    // 让"改 kernel → 重启服务 → 再采一次"这个真实工作流跨页面刷新还留得住 A。
    kernelSnaps: { A: null, B: null },   // 每槽 {ts, result}
    kernelShowAll: false,        // Kernel 明细表：false=只显示前 N 行
    kernelCollapsed: 10,         // 收起时显示的行数
    kernelExpanded: null,        // 展开看 stall 构成 + 建议的行索引(null=都收起)
    stallExpanded: null,         // Deep Evidence：展开看某 stall 类的原始 PerfWorks reason 名
    timeline: null,              // 执行时间线(最近 N 条 kernel 的 start/end/stream)
    tlFrozen: false,             // 冻结：停 2s 刷新,便于缩放/读
    tlPxPerMs: null,             // 时间线缩放：每毫秒像素;null=适应容器宽度
    tlSelIdx: null,              // 选中块索引(高亮)
    tlSelMs: null,               // 选中块中心时间(ms),缩放锚点
    tlSelName: '',               // 选中块名(显示)
    tpotSource: 'tpot',
    rooflineSource: 'measured',
    rooflineFormula: '',
    rooflineParamsB: '0',
    // Verdict card — populated each refresh from the recent points
    rooflineVerdict: null,    // {bound, computeUtil, bwUtil, knee, suggestions[]}
    rooflineScale: null,      // 调优指引：{ai, cur, t32, gain, knee}(decode 强度≈并发 → 扩并发能到哪)
    scalingSweep: { running: false, progress: null, error: null, verdict: null },  // P0-C 实测 scaling
    ttftHasData: false,
    tpotHasData: false,
    e2eHasData: false,
    e2eAvg: null,
    diagnoses: [],
    diagnosesStale: false,   // true = 当前无触发,面板显示的是最近一次命中(history 回退)
    benchRunning: 0,
    showStartupInfo: false,
    cmdlineCopyLabel: '',   // 空 = 交给 x-text 的 `|| t('common.copy')` 回退实时取当前语言,别在 init 时把值冻住

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
    // comm 子类 → 展示名(集合通信操作名是业界通用术语,不翻译)
    commSubLabel(sub) {
      return {
        allreduce: 'AllReduce', reducescatter: 'ReduceScatter', allgather: 'AllGather',
        sendrecv: 'SendRecv', broadcast: 'Broadcast', alltoall: 'AllToAll',
        reduce: 'Reduce', other: 'Other',
      }[sub] || sub;
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
    // 后端 decode_kernel_name 的中文标签 → 按短语客户端翻译(保留中文,en 出英文;PC sampling 出数据时才显示)
    kernelDecodeI18n(s) {
      if (!s) return s;
      const M = {
        'vLLM 自定义 · fused add + RMSNorm': 'decode.vllmFusedRms',
        'vLLM 自定义 · RMSNorm': 'decode.vllmRms',
        'vLLM 自定义 · RoPE': 'decode.vllmRope',
        'vLLM 自定义 · SiLU/激活': 'decode.vllmSilu',
        'vLLM 自定义 CUDA kernel': 'decode.vllmCuda',
        'FlashInfer · 采样 kernel': 'decode.flashinferSample',
        'cuBLAS GEMV(矩阵×向量,小 batch 典型)': 'decode.cublasGemv',
        'PyTorch · 原生 elementwise/reduce kernel': 'decode.torchNative',
        'C++ 编译 kernel(闭源/无 lineinfo)': 'decode.cppCompiled',
      };
      const k = M[s];
      return k ? t(k) : s;
    },
    // #3 这个 kernel 浪费的"全局 GPU 时间"= 时间占比 × 它内部 stall 比例
    kernelStallTimePct(k) {
      if (!k || !k.samples) return 0;
      return (k.time_pct || 0) * (k.stall_samples || 0) / k.samples;
    },
    // #1 GPU 有效执行 vs 等待(issued = 真正发射指令的样本占比)
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
        memory_dependency: { t: t('kernel.bottleneck.memDep'), a: t('kernel.bottleneck.memDepAction') },
        memory_throttle: { t: t('kernel.bottleneck.memThrottle'), a: t('kernel.bottleneck.memThrottleAction') },
        math_pipe: { t: t('kernel.bottleneck.mathPipe'), a: t('kernel.bottleneck.mathPipeAction') },
        exec_dependency: { t: t('kernel.bottleneck.execDep'), a: t('kernel.bottleneck.execDepAction') },
        shared_dependency: { t: t('kernel.bottleneck.sharedDep'), a: t('kernel.bottleneck.sharedDepAction') },
        sync: { t: t('kernel.bottleneck.sync'), a: t('kernel.bottleneck.syncAction') },
        fetch_control: { t: t('kernel.bottleneck.fetchCtrl'), a: t('kernel.bottleneck.fetchCtrlAction') },
        dispatch: { t: t('kernel.bottleneck.dispatch'), a: t('kernel.bottleneck.dispatchAction') },
      };
      const m = map[top.cls] || { t: t('cleanup.dominatedBy', {label: this.stallLabel(top.cls)}), a: '' };
      return { cls: top.cls, pct: top.pct, type: m.t, action: m.a };
    },
    // #3 全局最大可回收点：stall 时间占比最高的 kernel
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
        return t('kernel.suggestion.gemmMem');
      if (c === 'gemm' && ds === 'math_pipe')
        return t('kernel.suggestion.gemmMath');
      if (c === 'attention' && (ds === 'memory_dependency' || ds === 'memory_throttle'))
        return t('kernel.suggestion.attnMem');
      if (c === 'elementwise')
        return t('kernel.suggestion.elementwise');
      if (c === 'sampling')
        return t('kernel.suggestion.sampling');
      if (c === 'index')
        return t('kernel.suggestion.index');
      if (ds === 'exec_dependency')
        return t('kernel.suggestion.execDep');
      return '';
    },
    // P3 行级归因：取该 kernel 的"最深热点"(源码行 / SASS 偏移)。按 .so 原始 functionName 精确匹配
    kernelHotspot(k) {
      if (!k) return null;
      const hs = (this.deep.result && this.deep.result.pc_hotspots) || [];
      return hs.find(h => h.kernel === k.kernel) || null;
    },
    // P3 launch 栈：把 native 栈清洗成可读帧链(caller→callee:host 代码在前,启动原语在后)
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
    // P3：所有"能定位到 Python 源码行"的 kernel(差异化能力,单独提到顶部,免得埋在长表里)
    sourceHotspots() {
      const hs = (this.deep.result && this.deep.result.pc_hotspots) || [];
      return hs.filter(h => h.mappable && h.lines && h.lines.length);
    },
    // 这些可映射 kernel 合计占多少 GPU 时间(诚实标注：小模型上往往很小,主导在闭源 GEMM)
    sourceHotspotsTimePct() {
      const kt = (this.deep.result && this.deep.result.kernel_table) || [];
      const names = new Set(this.sourceHotspots().map(h => h.kernel));
      let sum = 0;
      for (const k of kt) if (names.has(k.kernel)) sum += (k.time_pct || 0);
      return sum;
    },
    // === Deep Evidence(全局 / warp 效率 / 方法论)辅助 ===
    // Warp 周期三态(占全部样本)：发指令 / 就绪未选中(余量) / 真 stall(在等)
    // res 省略时用当前取证结果;传入则可算任意快照(#6 前后对比复用)
    warpSplit(res) {
      const r = res || this.deep.result;
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
        memory_dependency: t('kernel.meaning.memDep'),
        shared_dependency: t('kernel.meaning.sharedDep'),
        memory_throttle: t('kernel.meaning.memThrottle'),
        math_pipe: t('kernel.meaning.mathPipe'),
        exec_dependency: t('kernel.meaning.execDep'),
        sync: t('kernel.meaning.sync'),
        fetch_control: t('kernel.meaning.fetchCtrl'),
        dispatch: t('kernel.meaning.dispatch'),
        scheduler_slack: t('kernel.meaning.schedulerSlack'),
        other: t('kernel.meaning.other'),
      }[cls] || '';
    },
    // 原始 PerfWorks reason 名：去掉公共前缀,留语义后缀(给专家看真实指标名)
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
        memory_dependency: t('kernel.label.memDep'), shared_dependency: t('kernel.label.sharedDep'),
        memory_throttle: t('kernel.label.memThrottle'), math_pipe: t('kernel.label.mathPipe'),
        exec_dependency: t('kernel.label.execDep'), sync: t('kernel.label.sync'), fetch_control: t('kernel.label.fetchCtrl'),
        dispatch: t('kernel.label.dispatch'), scheduler_slack: t('kernel.label.schedulerSlack'), other: t('kernel.label.other'),
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
    // 打开 Kernel tab 时调：先拉缓存结果;若可用且还没有结果,自动跑一次取证 ——
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
    // P0-C：启动实测 scaling 压测(串扫并发 1/4/16/64,约 2 分钟),轮询直到出结果
    async startScalingSweep() {
      if (this.scalingSweep.running) return;
      this.scalingSweep.running = true;
      this.scalingSweep.error = null;
      this.scalingSweep.progress = t('kernel.scaling.progress');
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
          this.scalingSweep.progress = s.progress || t('kernel.scaling.testing');
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
        const r = await fetch('/api/kernels/deep_evidence?lang=' + _uiLang()).then(x => x.json());
        this.deep.available_now = !!r.available_now;
        if (r.last) { this.deep.result = r.last; this.deep.findings = r.findings || []; }
      } catch (e) { /* fail-closed：静默 */ }
    },
    // 触发一个取证短窗(阻塞 ~window 秒)
    async runDeepEvidence(window) {
      if (this.deep.running) return;
      this.deep.running = true; this.deep.error = null;
      try {
        const r = await fetch(`/api/kernels/deep_evidence?window=${window || 5}&lang=${_uiLang()}`,
          { method: 'POST' }).then(x => x.json());
        if (r.available) {
          this.deep.result = r; this.deep.findings = r.findings || []; this.deep.available_now = true;
        } else {
          this.deep.error = r.error || t('kernel.pcSamplingUnavailable'); this.deep.available_now = false;
        }
      } catch (e) {
        this.deep.error = t('kernel.requestFailed', {e: e});
      } finally {
        this.deep.running = false;
      }
    },
    // ===== #6 Kernel 快照 A/B 对比：改完 kernel 之后,工具内直接验证有没有变好 =====
    // 不需要任何新采集源 —— deep.result 本身就是一次完整快照,缺的只是"存下来 + 对齐 diff"。
    _KSNAP_KEY: 'pping.kernelSnaps',
    _ksnapLoad() {
      try {
        const o = JSON.parse(localStorage.getItem(this._KSNAP_KEY) || 'null');
        if (o && typeof o === 'object') this.kernelSnaps = { A: o.A || null, B: o.B || null };
      } catch (e) { /* 存储损坏 / 隐私模式：退回空快照,不阻断 Kernel tab */ }
    },
    _ksnapPersist() {
      try { localStorage.setItem(this._KSNAP_KEY, JSON.stringify(this.kernelSnaps)); }
      catch (e) { /* 配额满 / 隐私模式：内存里照样能对比,只是刷新后丢 */ }
    },
    saveKernelSnap(slot) {
      const r = this.deep.result;
      if (!r || !r.available) return;
      this.kernelSnaps[slot] = { ts: Date.now(), result: r };
      this._ksnapPersist();
    },
    clearKernelSnap(slot) {
      this.kernelSnaps[slot] = null;
      this._ksnapPersist();
    },
    ksnapTime(slot) {
      const s = this.kernelSnaps[slot];
      return s ? new Date(s.ts).toLocaleTimeString() : '';
    },
    ksnapReady() { return !!(this.kernelSnaps.A && this.kernelSnaps.B); },

    // 采样"绝对速率"：samples × 2^period / window_s ∝ 该 kernel 每墙钟秒消耗的 GPU 周期。
    // 为什么不用 time_pct：占比是**相对**值,把一个 kernel 优化掉会自动抬高其余所有 kernel
    // 的占比 —— 直接拿占比做前后对比会得出"其它 kernel 都变差了"的假结论。乘 2^period 是为了
    // 让采样周期不同的两次快照也可比(样本数 ∝ 时间 / 周期)。
    _ksnapRate(samples, res) {
      const w = Number(res && res.window_s) || 0;
      if (w <= 0 || samples == null) return null;
      return samples * Math.pow(2, Number(res.period_log2) || 0) / w;
    },

    // 负载对照。★ 这里不能用"总采样速率变化"当漂移信号 —— 优化成功本身就会让总速率下降
    // (kernel 变快 → GPU 活跃周期变少),那样会在优化生效时误报"负载不可比"。
    // 真正能区分两者的是**变化的分散程度**：
    //   - 负载变了(一次高峰一次空闲)→ 几乎所有 kernel 的绝对速率一起同向平移 → 中位数大;
    //   - 优化生效 → 只有被改的那几个 kernel 动,其余不动 → 中位数≈0,尾部有大值。
    // 故用 per-kernel Δ 的**中位数**作漂移判据,总速率只作为上下文数字展示。
    ksnapLoad() {
      if (!this.ksnapReady()) return null;
      const A = this.kernelSnaps.A.result, B = this.kernelSnaps.B.result;
      const ra = this._ksnapRate(A.sample_total, A), rb = this._ksnapRate(B.sample_total, B);
      const totalPct = (ra && rb) ? 100 * (rb - ra) / ra : null;
      // 只取两侧都存在的 kernel(新增/消失的没有可比基准)
      const deltas = this.ksnapRows()
        .filter(r => r.ratePct != null).map(r => r.ratePct).sort((x, y) => x - y);
      let medianPct = null;
      if (deltas.length) {
        const m = Math.floor(deltas.length / 2);
        medianPct = deltas.length % 2 ? deltas[m] : (deltas[m - 1] + deltas[m]) / 2;
      }
      return {
        totalPct, medianPct,
        // 样本太少(<4 个共同 kernel)时中位数不稳,不下漂移结论,只展示数字
        drift: medianPct != null && deltas.length >= 4 && Math.abs(medianPct) >= 15,
        periodA: A.period_log2, periodB: B.period_log2,
        periodMismatch: A.period_log2 !== B.period_log2,
      };
    },

    // 逐 kernel 对齐 diff。|Δ|<5% 视为持平：PC Sampling 是统计估计,窗与窗之间本身有噪声,
    // 阈值比 bench 的 2% 放宽是因为采样噪声大于压测运行间噪声。
    ksnapRows() {
      if (!this.ksnapReady()) return [];
      const A = this.kernelSnaps.A.result, B = this.kernelSnaps.B.result;
      const index = (res) => new Map((res.kernel_table || []).map(k => [k.kernel, k]));
      const ma = index(A), mb = index(B);
      const rows = [];
      for (const name of new Set([...ma.keys(), ...mb.keys()])) {
        const a = ma.get(name), b = mb.get(name);
        const ra = a ? this._ksnapRate(a.samples, A) : null;
        const rb = b ? this._ksnapRate(b.samples, B) : null;
        const ratePct = (ra != null && rb != null && ra > 0) ? 100 * (rb - ra) / ra : null;
        const shareA = a ? a.time_pct : null, shareB = b ? b.time_pct : null;
        rows.push({
          kernel: name,
          cls: (b || a).cls,
          status: !a ? 'new' : (!b ? 'gone' : 'both'),
          shareA, shareB,
          shareDelta: (shareA != null && shareB != null) ? shareB - shareA : null,
          ratePct,
          good: (ratePct != null && Math.abs(ratePct) >= 5) ? ratePct < 0 : null,
          stallA: a ? a.dominant_stall : null,
          stallB: b ? b.dominant_stall : null,
          stallChanged: !!(a && b && a.dominant_stall !== b.dominant_stall),
          weight: Math.max(shareA || 0, shareB || 0),
        });
      }
      rows.sort((x, y) => y.weight - x.weight);
      return rows.slice(0, 25);
    },

    // 全局 stall 构成的百分点差。stall 占比下降 = 更好;|Δ|<1 个百分点视为持平。
    ksnapStallRows() {
      if (!this.ksnapReady()) return [];
      const pick = (res) => Object.fromEntries((res.stall_shares || []).map(s => [s.cls, s.pct]));
      const a = pick(this.kernelSnaps.A.result), b = pick(this.kernelSnaps.B.result);
      return [...new Set([...Object.keys(a), ...Object.keys(b)])]
        .map(cls => {
          const pa = a[cls] || 0, pb = b[cls] || 0, delta = pb - pa;
          // scheduler_slack 高常是好事(有余量),不套"越低越好"
          const good = (cls === 'scheduler_slack' || Math.abs(delta) < 1) ? null : delta < 0;
          return { cls, a: pa, b: pb, delta, good };
        })
        .filter(r => r.a >= 0.5 || r.b >= 0.5)
        .sort((x, y) => Math.abs(y.delta) - Math.abs(x.delta));
    },

    // Warp 三态的前后差(issued 越高越好,stall 越低越好,slack 是中性信息)
    ksnapWarpRows() {
      if (!this.ksnapReady()) return [];
      const wa = this.warpSplit(this.kernelSnaps.A.result);
      const wb = this.warpSplit(this.kernelSnaps.B.result);
      if (!wa || !wb) return [];
      return [
        { key: 'issued', label: t('kern.issued'), a: wa.issued, b: wb.issued, higherBetter: true },
        { key: 'slack', label: t('kern.slack'), a: wa.slack, b: wb.slack, higherBetter: null },
        { key: 'stall', label: t('kern.stallWait'), a: wa.stall, b: wb.stall, higherBetter: false },
      ].map(r => {
        const delta = r.b - r.a;
        const good = (r.higherBetter == null || Math.abs(delta) < 1) ? null
          : (r.higherBetter ? delta > 0 : delta < 0);
        return { ...r, delta, good };
      });
    },
    // Δ 数字统一成 "+x.x%" / "−x.x%"(带符号,便于扫读)
    ksnapDelta(v, digits) {
      if (v == null || isNaN(v)) return '—';
      const n = Number(v);
      return (n > 0 ? '+' : '') + n.toFixed(digits == null ? 1 : digits);
    },

    // kernel 数据是否"实时"(采集时刻够近),用于新鲜度横幅
    // 延迟分位条(三行式)：某分位占 p99 的宽度%(p99=满刻度;三段挤一条看不清,实测反馈)
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
      if (a < 1.5) return t('kernel.fresh');
      if (a < 90) return t('kernel.agoSeconds', {s: Math.round(a)});
      return t('kernel.agoMinutes', {m: Math.round(a / 60)});
    },
    // 执行时间线：px-based,横向滚动 + 缩放按钮。放大=每毫秒像素翻倍(内层变宽),
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
    // 缩放：以"选中块中心"为锚点(没选则以当前视口中心),缩放后调滚动位置让锚点居中。
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
        if (!r.available) { alert(t('kernel.traceUnavailable')); return; }
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
        headline = t('roofline.memoryBound');
        suggestions = [
          t('roofline.memSugg1'),
          t('roofline.memSugg2'),
          t('roofline.memSugg3'),
          t('roofline.memSugg4', {bw: (peak.mem_bw_tbs * 1000).toFixed(0)}),
        ];
      } else {
        bound = 'compute';
        headline = t('roofline.computeBound');
        suggestions = [
          t('roofline.compSugg1'),
          t('roofline.compSugg2'),
          t('roofline.compSugg3'),
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

      this._ksnapLoad();   // #6 前后对比快照：刷新页面/重启服务后 A 还在
      this.fetchSystem();
      this.refresh();
      setInterval(() => this.refresh(), 2000);
      // 切语言时立刻重取 kernel findings(后端按 ?lang= 出双语)：实时 kernels 走 refresh,
      // deep evidence 是按需的也一并重取。轮询本身已带 _uiLang(),这里只为即时生效。
      this.$watch('$store.i18n.lang', () => { this.refresh(); this.loadDeepEvidence(); });
      // 打开 Kernel tab 自动取证(§A)：进去就有真数据,不用手点按钮
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
      // P0-C：实测 scaling verdict(随 roofline 响应带回)
      this.scalingSweep.verdict = (data.scaling && data.scaling.verdict) || null;
      // 调优指引(decode 强度≈并发)：当前簇 → 并发32 的带宽上界 → 拐点
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
        this.cmdlineCopyLabel = t('ui.copied');
      } catch (e) {
        this.cmdlineCopyLabel = t('ui.copyFailed');
      }
      setTimeout(() => { this.cmdlineCopyLabel = ''; }, 1800);
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
          fetch('/api/kernels?window=60&lang=' + _uiLang()).then(r => r.json()).catch(() => ({enabled: false, class_shares: []})),
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
