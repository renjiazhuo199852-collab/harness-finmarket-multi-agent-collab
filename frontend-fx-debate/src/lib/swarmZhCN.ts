import type { SwarmPresetAgent, SwarmPresetSummary, SwarmPresetTask } from "@/types";

export const AGENT_TEAM_CATEGORIES = [
  "全部",
  "股票与基本面",
  "宏观与外汇",
  "固收与衍生品",
  "量化策略",
  "数字资产",
  "资产配置与风险",
  "事件与另类",
] as const;

export type AgentTeamCategory = (typeof AGENT_TEAM_CATEGORIES)[number];
export type PresetCategory = Exclude<AgentTeamCategory, "全部"> | "开发与测试";

interface PresetDisplay {
  title: string;
  description: string;
  category: PresetCategory;
  searchAliases?: string[];
  badge: "项目核心" | "平台内置" | "本地自定义" | "测试预设";
  isCore?: boolean;
  isTesting?: boolean;
  hiddenFromCatalog?: boolean;
}

const PRESET_DISPLAY: Record<string, PresetDisplay> = {
  commodity_research_team: {
    title: "商品研究团队",
    description: "从供给与需求两个方向并行开展商品研究，由周期策略智能体综合形成投资研判。",
    category: "事件与另类",
    badge: "平台内置",
  },
  convertible_bond_team: {
    title: "可转债研究团队",
    description: "并行评估债底、正股弹性与期权价值，汇总形成可转债投资策略。",
    category: "固收与衍生品",
    badge: "平台内置",
  },
  credit_research_team: {
    title: "固收信用研究团队",
    description: "从主体信用、利率环境和行业信用三条线并行审视，输出债券投资判断。",
    category: "固收与衍生品",
    badge: "平台内置",
  },
  crypto_research_lab: {
    title: "加密资产研究实验室",
    description: "结合链上、DeFi 与情绪信号开展数字资产研究，沉淀可交易的 Alpha 线索。",
    category: "数字资产",
    badge: "平台内置",
  },
  crypto_trading_desk: {
    title: "加密资产交易与风险团队",
    description: "聚焦资金费率、清算结构与链上资金流，输出仓位、执行和风险控制建议。",
    category: "数字资产",
    badge: "平台内置",
  },
  derivatives_strategy_desk: {
    title: "衍生品策略团队",
    description: "围绕波动率、策略结构和 Greeks 风险逐层分析，形成期权交易方案。",
    category: "固收与衍生品",
    badge: "平台内置",
  },
  earnings_research_desk: {
    title: "财报与业绩研究团队",
    description: "跟踪财报基本面、预期修正和事件交易机会，形成业绩窗口期研究结论。",
    category: "股票与基本面",
    badge: "平台内置",
  },
  equity_research_team: {
    title: "股票研究团队",
    description: "按宏观、行业和个股层层推进研究，由编辑智能体汇总成完整股票研究报告。",
    category: "股票与基本面",
    badge: "平台内置",
  },
  etf_allocation_desk: {
    title: "ETF 配置团队",
    description: "并行完成 ETF 筛选、宏观配置和风险预算，生成组合配置与回测建议。",
    category: "资产配置与风险",
    badge: "平台内置",
  },
  event_driven_task_force: {
    title: "事件驱动研究团队",
    description: "从事件发现、影响评估到策略构建逐步推进，形成事件驱动投资方案。",
    category: "事件与另类",
    badge: "平台内置",
  },
  factor_research_committee: {
    title: "因子研究委员会",
    description: "并行挖掘和验证因子，再组合回测并复核稳健性，服务量化研究决策。",
    category: "量化策略",
    badge: "平台内置",
  },
  fund_selection_panel: {
    title: "基金筛选评审组",
    description: "结合基金筛选、绩效归因和组合优化，输出 FOF 或基金配置建议。",
    category: "资产配置与风险",
    badge: "平台内置",
  },
  fundamental_research_team: {
    title: "基本面研究团队",
    description: "从财务、估值与经营质量并行拆解公司，汇总为买方基本面研究报告。",
    category: "股票与基本面",
    badge: "平台内置",
  },
  fx_debate_team: {
    title: "外汇多智能体辩论团队",
    description: "围绕指定货币对，由多头、空头、宏观技术、风控和裁决智能体协作，形成证据约束的外汇决策结论。",
    category: "宏观与外汇",
    searchAliases: ["宏观与外汇", "外汇与宏观", "外汇", "FX", "Forex", "宏观", "Macro"],
    badge: "项目核心",
    isCore: true,
  },
  geopolitical_war_room: {
    title: "地缘政治研判团队",
    description: "并行分析地缘事件、能源冲击和供应链影响，形成危机情境下的资产配置预案。",
    category: "事件与另类",
    badge: "平台内置",
  },
  global_allocation_committee: {
    title: "全球资产配置委员会",
    description: "并行研究 A 股、港美股和加密资产，由配置智能体形成跨市场组合建议。",
    category: "资产配置与风险",
    badge: "平台内置",
  },
  global_equities_desk: {
    title: "全球股票研究团队",
    description: "覆盖 A 股、港美股与数字资产相关股票线索，输出跨市场股票配置判断。",
    category: "资产配置与风险",
    badge: "平台内置",
  },
  investment_committee: {
    title: "投资决策委员会",
    description: "组织多空研究、风险复核和组合经理决策，模拟买方投委会形成最终判断。",
    category: "资产配置与风险",
    badge: "平台内置",
  },
  macro_rates_fx_desk: {
    title: "宏观、利率与外汇策略团队",
    description: "跟踪央行、收益率曲线、汇率和通胀商品信号，形成宏观交易与配置建议。",
    category: "宏观与外汇",
    badge: "平台内置",
  },
  macro_strategy_forum: {
    title: "宏观策略研讨组",
    description: "汇聚全球、国内和政策视角，由首席策略智能体输出跨资产配置观点。",
    category: "宏观与外汇",
    badge: "平台内置",
  },
  ml_quant_lab: {
    title: "机器学习量化研究实验室",
    description: "并行完成特征工程和模型设计，并通过回测复核样本外表现与可用性。",
    category: "量化策略",
    badge: "平台内置",
  },
  pairs_research_lab: {
    title: "配对交易研究实验室",
    description: "并行扫描相关性和协整关系，再评估执行结构，形成配对交易策略。",
    category: "量化策略",
    badge: "平台内置",
  },
  portfolio_review_board: {
    title: "投资组合评审委员会",
    description: "并行复盘归因、风险和执行质量，由 CIO 智能体形成再平衡建议。",
    category: "资产配置与风险",
    badge: "平台内置",
  },
  quant_strategy_desk: {
    title: "量化策略团队",
    description: "并行推进选股和因子研究，随后完成策略回测与风险审计。",
    category: "量化策略",
    badge: "平台内置",
  },
  risk_committee: {
    title: "风险管理委员会",
    description: "围绕回撤、尾部风险和市场状态并行复核，由风险负责人给出审查意见。",
    category: "资产配置与风险",
    badge: "平台内置",
  },
  sector_rotation_team: {
    title: "行业轮动研究团队",
    description: "结合经济周期、行业景气和资金流信号，生成行业轮动策略与回测判断。",
    category: "股票与基本面",
    badge: "平台内置",
  },
  sentiment_intelligence_team: {
    title: "市场情绪研判团队",
    description: "并行读取新闻、社交情绪和资金流，合成市场情绪评分与反转信号。",
    category: "事件与另类",
    badge: "平台内置",
  },
  social_alpha_team: {
    title: "社交舆情 Alpha 研究团队",
    description: "从社交平台和社区讨论中提炼情绪与主题变化，形成可交易的另类数据信号。",
    category: "事件与另类",
    badge: "平台内置",
  },
  statistical_arbitrage_desk: {
    title: "统计套利策略团队",
    description: "并行筛选价差关系和微观结构机会，构建统计套利策略并完成风险复核。",
    category: "量化策略",
    badge: "平台内置",
  },
  technical_analysis_panel: {
    title: "技术分析评审组",
    description: "多种技术分析框架并行研判趋势与结构，由信号汇总智能体形成共识评分。",
    category: "量化策略",
    badge: "平台内置",
  },
  value_investing_committee: {
    title: "价值投资委员会",
    description: "以护城河、反向思维、好生意和长期确定性多视角审视公司，形成价值投资结论。",
    category: "股票与基本面",
    badge: "平台内置",
  },
  fx_pair_debate_desk_smoke: {
    title: "外汇辩论流程测试团队",
    description: "用于验证外汇辩论编排、任务顺序、上游摘要传递和报告写入的本地测试预设。",
    category: "开发与测试",
    badge: "测试预设",
    isTesting: true,
    hiddenFromCatalog: true,
  },
};

const VARIABLE_LABELS: Record<string, string> = {
  commodity: "商品",
  horizon: "投资周期",
  target: "研究标的",
  market: "市场",
  timeframe: "时间周期",
  goal: "研究目标",
  view: "分析视角",
  risk_profile: "风险偏好",
  risk_tolerance: "风险偏好",
  event_type: "事件类型",
  factor_type: "因子类型",
  fund_type: "基金类型",
  strategy_type: "策略类型",
  target_variable: "预测目标",
  sector: "行业范围",
  crisis: "风险事件",
  portfolio: "投资组合",
  review_period: "复盘周期",
  company: "公司",
};

const ROLE_LABELS: Record<string, string> = {
  supply_analyst: "供应分析师",
  demand_analyst: "需求分析师",
  cycle_strategist: "周期策略师",
  bond_analyst: "债底分析师",
  equity_analyst: "正股分析师",
  option_analyst: "内嵌期权分析师",
  cb_strategist: "可转债策略师",
  credit_analyst: "信用分析师",
  rate_analyst: "利率分析师",
  sector_credit_analyst: "行业信用分析师",
  fixed_income_strategist: "固收策略师",
  onchain_analyst: "链上数据分析师",
  defi_analyst: "DeFi 协议分析师",
  crypto_sentiment_analyst: "加密情绪分析师",
  alpha_synthesizer: "Alpha 汇总分析师",
  funding_basis_analyst: "资金费率与基差分析师",
  liquidation_analyst: "清算与微观结构分析师",
  flow_analyst: "资金流分析师",
  desk_risk_manager: "交易台风险经理",
  vol_analyst: "波动率分析师",
  strategy_designer: "策略设计师",
  greeks_manager: "Greeks 风险经理",
  fundamental_analyst: "基本面与公告分析师",
  revision_tracker: "业绩预期跟踪员",
  event_options_analyst: "业绩事件与期权分析师",
  earnings_strategist: "业绩策略师",
  macro_analyst: "宏观分析师",
  sector_analyst: "行业分析师",
  stock_picker: "个股分析师",
  aggregator: "研究汇总编辑",
  etf_screener: "ETF 筛选分析师",
  macro_allocator: "宏观配置分析师",
  risk_budgeter: "风险预算分析师",
  portfolio_optimizer: "组合优化师",
  event_scanner: "事件扫描分析师",
  impact_analyst: "影响评估分析师",
  strategy_builder: "策略构建师",
  factor_miner: "因子挖掘分析师",
  factor_validator: "因子验证分析师",
  factor_combiner: "因子组合分析师",
  backtest_reviewer: "回测复核分析师",
  fund_screener: "基金筛选分析师",
  attribution_analyst: "绩效归因分析师",
  fof_optimizer: "FOF 组合优化师",
  financial_analyst: "财务分析师",
  valuation_analyst: "估值分析师",
  quality_analyst: "经营质量分析师",
  report_editor: "研究报告编辑",
  pair_bull: "货币对多头分析师",
  pair_bear: "货币对空头分析师",
  macro_technical: "宏观与技术分析师",
  fx_risk_officer: "外汇风险官",
  debate_judge: "辩论裁决与外汇组合经理",
  geopolitical_analyst: "地缘政治分析师",
  energy_analyst: "能源冲击分析师",
  supply_chain_analyst: "供应链影响分析师",
  chief_strategist: "首席策略师",
  a_share_analyst: "A 股分析师",
  crypto_analyst: "加密资产分析师",
  us_hk_analyst: "港美股分析师",
  allocator: "资产配置策略师",
  a_share_researcher: "A 股研究员",
  us_hk_researcher: "港美股研究员",
  crypto_researcher: "加密资产研究员",
  global_strategist: "全球股票策略师",
  bull_advocate: "多头研究员",
  bear_advocate: "空头研究员",
  risk_officer: "首席风险官",
  portfolio_manager: "组合经理",
  rates_analyst: "全球利率与曲线分析师",
  fx_strategist: "外汇策略师",
  commodity_inflation_analyst: "商品与通胀分析师",
  macro_pm: "宏观组合经理",
  global_economist: "全球经济学家",
  domestic_economist: "中国经济学家",
  policy_analyst: "政策分析师",
  feature_engineer: "特征工程师",
  data_scientist: "数据科学家",
  backtest_engineer: "回测工程师",
  correlation_scanner: "相关性扫描分析师",
  cointegration_tester: "协整检验分析师",
  pair_strategist: "配对策略师",
  microstructure_reviewer: "微观结构复核员",
  risk_inspector: "风险检查员",
  execution_analyst: "执行质量分析师",
  chief_investment_officer: "首席投资官",
  screener: "股票筛选分析师",
  backtester: "策略回测员",
  risk_auditor: "风险审计员",
  drawdown_analyst: "回撤分析师",
  tail_risk_analyst: "尾部风险分析师",
  regime_detector: "市场状态分析师",
  cycle_analyst: "经济周期分析师",
  prosperity_analyst: "行业景气分析师",
  rotation_strategist: "行业轮动策略师",
  news_analyst: "新闻情报分析师",
  social_analyst: "社交情绪分析师",
  signal_synthesizer: "情绪信号汇总分析师",
  twitter_analyst: "Twitter 情绪分析师",
  telegram_analyst: "Telegram 社群分析师",
  reddit_analyst: "Reddit 舆情分析师",
  pair_scanner: "配对标的扫描分析师",
  microstructure_analyst: "微观结构分析师",
  arb_strategist: "套利策略师",
  risk_monitor: "风险监控员",
  classic_ta_analyst: "经典技术分析师",
  ichimoku_analyst: "一目均衡表分析师",
  harmonic_analyst: "谐波形态分析师",
  wave_analyst: "波浪理论分析师",
  smc_analyst: "SMC 与订单流分析师",
  signal_aggregator: "技术信号裁决员",
  buffett: "巴菲特视角分析师",
  munger: "芒格反向思维分析师",
  duan_yongping: "段永平好生意分析师",
  li_lu: "李录长期确定性分析师",
  chair: "委员会主席",
};

const SKILL_LABELS: Record<string, string> = {
  'fx-hypothesis-falsification': '外汇假设证伪',
  'fx-regime-cross-confirmation': '外汇状态交叉确认',
  'fx-relative-macro-interpretation': '外汇相对宏观解读',
  'risk-analysis': '风险分析与压力测试',
  'hedging-strategy': '对冲策略设计',
  'correlation-analysis': '相关性与协整分析',
  'correlation-regime': '相关性状态与危机归因',
  'macro-analysis': '宏观周期与央行政策',
  'global-macro': '全球宏观研究',
  'cross-market-strategy': '跨市场策略',
};

export function skillLabel(skill: string): string {
  return SKILL_LABELS[skill] || skill;
}
const PRESET_ROLE_OVERRIDES: Record<string, Record<string, string>> = {
  fx_pair_debate_desk_smoke: {
    pair_bull: "多头流程测试智能体",
    pair_bear: "空头流程测试智能体",
    macro_technical: "宏观技术流程测试智能体",
    risk_officer: "风控流程测试智能体",
    debate_judge: "裁决流程测试智能体",
  },
};

const RESPONSIBILITY_OVERRIDES: Record<string, Record<string, string>> = {
  fx_debate_team: {
    pair_bull: "围绕货币对上行假设组织证据，识别支撑因素、反证和情景边界。",
    pair_bear: "围绕货币对下行假设组织证据，检验压力来源、反证和失效条件。",
    macro_technical: "综合相对宏观状态与技术结构，判断趋势、区间和跨周期一致性。",
    fx_risk_officer: "复核多空与宏观技术结论的证据质量、风险敞口和必要失效条件。",
    debate_judge: "汇总各方观点与风控意见，形成外汇方向、概率和交易建议。",
  },
  fx_pair_debate_desk_smoke: {
    pair_bull: "验证多头测试节点能按预设启动并写入最小报告。",
    pair_bear: "验证空头测试节点能按预设启动并写入最小报告。",
    macro_technical: "验证宏观技术测试节点能按预设启动并写入最小报告。",
    risk_officer: "验证下游节点能够接收上游摘要并完成流程检查。",
    debate_judge: "验证最终裁决节点能够读取风控结果并完成收尾报告。",
  },
};

const TASK_LABELS: Record<string, string> = {
  "task-supply-research": "供给侧研究",
  "task-demand-research": "需求侧研究",
  "task-cycle-strategy": "周期策略汇总",
  "task-pair-bull": "多头论证",
  "task-pair-bear": "空头论证",
  "task-macro-technical": "宏观技术研判",
  "task-risk": "风险复核",
  "task-judge": "最终裁决",
  "task-decision": "最终决策",
};

export function presetDisplay(preset: SwarmPresetSummary): PresetDisplay {
  const configured = PRESET_DISPLAY[preset.name];
  if (configured) return configured;
  const isTesting = preset.name.includes("smoke") || preset.name.includes("test");
  return {
    title: preset.title || preset.name,
    description: preset.description || "该团队尚未配置中文简介，当前展示真实预设元数据。",
    category: isTesting ? "开发与测试" : "资产配置与风险",
    badge: isTesting ? "测试预设" : preset.source === "user" ? "本地自定义" : "平台内置",
    isTesting,
  };
}

export function isCorePreset(preset: SwarmPresetSummary): boolean {
  return presetDisplay(preset).isCore === true;
}

export function isTestingPreset(preset: SwarmPresetSummary): boolean {
  return presetDisplay(preset).isTesting === true;
}

export function isCatalogVisible(preset: SwarmPresetSummary): boolean {
  return presetDisplay(preset).hiddenFromCatalog !== true;
}

export function variableLabels(preset: SwarmPresetSummary): string {
  const labels = (preset.variables || [])
    .map((item) => typeof item === "string" ? item : item.name)
    .filter((name): name is string => Boolean(name))
    .map((name) => VARIABLE_LABELS[name] || name);
  return labels.length ? labels.join(" · ") : "未声明输入";
}

export function agentRoleLabel(presetName: string, agent: SwarmPresetAgent): string {
  return PRESET_ROLE_OVERRIDES[presetName]?.[agent.id] || ROLE_LABELS[agent.id] || agent.role || agent.id;
}

export function agentResponsibility(presetName: string, agent: SwarmPresetAgent): string {
  const override = RESPONSIBILITY_OVERRIDES[presetName]?.[agent.id];
  if (override) return override;
  const role = agentRoleLabel(presetName, agent);
  if (role.includes("风险")) return `围绕${role}职责，复核风险来源、约束条件和下游决策边界。`;
  if (role.includes("策略") || role.includes("经理") || role.includes("主席") || role.includes("官")) {
    return `整合上游研究结论，形成可执行的策略判断、配置建议或最终决策意见。`;
  }
  if (role.includes("工程师") || role.includes("数据科学家")) return `处理特征、模型或回测环节，验证研究方案的数据基础与稳定性。`;
  if (role.includes("汇总") || role.includes("编辑") || role.includes("裁决")) return `汇总多方输入，提炼关键分歧、共识和面向业务用户的研究结论。`;
  return `围绕${role}职责，分析相关数据、结构和市场线索，形成可交给下游复核的研究观点。`;
}

export function taskLabel(task?: SwarmPresetTask): string {
  if (!task) return "未绑定任务";
  return TASK_LABELS[task.id] || task.id;
}
