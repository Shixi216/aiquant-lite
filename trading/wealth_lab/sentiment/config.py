"""Wealth Sentiment v1 配置：数据源注册表、资产关联规则、版本化聚合权重。

独立研究模块：不读写任何股票业务表，不触碰 S0-S8 策略、WealthRiskEngine 与冻结契约。
- 数据落独立库 database/wealth_sentiment.duckdb（与 wealth_lab.duckdb 完全隔离）；
- 聚合权重随快照入库（component_breakdown），版本号 sentiment_version 永久保留；
- LLM 模型：deepseek 优先（默认 deepseek-flash，用户指定），qwen 兜底。
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SENTIMENT_DB_PATH = PROJECT_ROOT / "database" / "wealth_sentiment.duckdb"

INGEST_VERSION = "sentiment-ingest/1.1"
SENTIMENT_VERSION = "wealth-sentiment/1.1"
PROMPT_VERSION = "wealth-sentiment-v1.1"

# ------------------------------------------------------------------ 关联层级（v1.1 P0）
# EXACT        新闻明确点名该池内产品（出现基金代码或完整登记名称）→ 直接资产证据
# SAME_INDEX   新闻主题/标的指数与该产品相同（如金价、标普500、另一同类基金产品）
# CLASS_ONLY   仅命中资产类别（如"利率"→BOND），不指向任何具体产品
# MODEL_INFERRED LLM 推断关联但确定性规则未找到文本依据（聚合按 SAME_INDEX 加权，
#                但绝不计入直接证据）
LINK_LEVELS = ("EXACT", "SAME_INDEX", "CLASS_ONLY", "MODEL_INFERRED")
LINK_LEVEL_RANK = {lv: i for i, lv in enumerate(LINK_LEVELS)}

# 评分可信度基线（v1.1 P1）
EVIDENCE_BASIS = ("DIRECT_EVIDENCE", "CLASS_LEVEL_EVIDENCE", "INSUFFICIENT_DATA")

# ------------------------------------------------------------------ 模型
# 用户指定：deepseek 用 deepseek-flash（不是 v4-pro）；不可用时 qwen 兜底。
DEEPSEEK_SENTIMENT_MODEL = os.environ.get(
    "WEALTH_SENTIMENT_DEEPSEEK_MODEL", "deepseek-flash"
).strip() or "deepseek-flash"

# ------------------------------------------------------------------ 资产类别
ASSET_CLASSES = (
    "EQUITY", "BOND", "CONVERTIBLE", "GOLD", "COMMODITY",
    "REIT", "OVERSEAS_EQUITY", "FX",
)

# 池内 symbol → 情绪资产类别（确定性映射，按 universe 33 资产逐一登记）
SYMBOL_CLASS: dict[str, str] = {
    "159972.SZ": "BOND", "511010.SH": "BOND", "511090.SH": "BOND",
    "511260.SH": "BOND", "511520.SH": "BOND",
    "159985.SZ": "COMMODITY", "518880.SH": "GOLD",
    "511380.SH": "CONVERTIBLE",
    "159915.SZ": "EQUITY", "159949.SZ": "EQUITY", "510300.SH": "EQUITY",
    "510500.SH": "EQUITY", "510880.SH": "EQUITY", "588000.SH": "EQUITY",
    "512170.SH": "EQUITY", "512480.SH": "EQUITY", "512660.SH": "EQUITY",
    "512690.SH": "EQUITY", "512800.SH": "EQUITY", "515030.SH": "EQUITY",
    "180101.SZ": "REIT", "180201.SZ": "REIT", "508000.SH": "REIT", "508056.SH": "REIT",
    "159545.SZ": "OVERSEAS_EQUITY",  # 恒生港股通红利（港股暴露）
    "512890.SH": "EQUITY", "563020.SH": "EQUITY",
    "510900.SH": "OVERSEAS_EQUITY", "513030.SH": "OVERSEAS_EQUITY",
    "513050.SH": "OVERSEAS_EQUITY", "513100.SH": "OVERSEAS_EQUITY",
    "513500.SH": "OVERSEAS_EQUITY", "162411.SZ": "OVERSEAS_EQUITY",
}

# 基金代码（6 位）→ 池内 symbol，用于正则关联
FUND_CODE_TO_SYMBOL = {sym.split(".")[0]: sym for sym in SYMBOL_CLASS}

# ------------------------------------------------------------------ 资讯类别
RAW_CATEGORIES = (
    "MONETARY_POLICY", "MACRO_DATA", "FUND_ANNOUNCE",
    "GOLD_COMMODITY", "BOND_MARKET", "QDII_OVERSEAS", "GENERAL",
)

# 关键词 → (raw_category, 资产类别, 直接关联 symbols)
# stock_news_em 每个关键词一次调用；关键词同时用于确定性资产关联。
KEYWORD_TOPICS: dict[str, tuple[str, str | None, tuple[str, ...]]] = {
    "央行": ("MONETARY_POLICY", None, ()),
    "逆回购": ("MONETARY_POLICY", None, ()),
    "降息": ("MONETARY_POLICY", "BOND", ("511010.SH", "511260.SH", "511090.SH", "511520.SH", "159972.SZ")),
    "LPR": ("MONETARY_POLICY", "BOND", ("511010.SH", "511260.SH", "511090.SH", "511520.SH", "159972.SZ")),
    "国债": ("BOND_MARKET", "BOND", ("511010.SH", "511260.SH", "511090.SH", "511520.SH", "159972.SZ")),
    "可转债": ("BOND_MARKET", "CONVERTIBLE", ("511380.SH",)),
    "黄金": ("GOLD_COMMODITY", "GOLD", ("518880.SH",)),
    "豆粕": ("GOLD_COMMODITY", "COMMODITY", ("159985.SZ",)),
    "REITs": ("FUND_ANNOUNCE", "REIT", ("180101.SZ", "180201.SZ", "508000.SH", "508056.SH")),
    "QDII": ("QDII_OVERSEAS", "OVERSEAS_EQUITY",
             ("513100.SH", "513500.SH", "513050.SH", "510900.SH", "513030.SH", "162411.SZ")),
    "纳斯达克": ("QDII_OVERSEAS", "OVERSEAS_EQUITY", ("513100.SH",)),
    "标普": ("QDII_OVERSEAS", "OVERSEAS_EQUITY", ("513500.SH",)),
    "恒生": ("QDII_OVERSEAS", "OVERSEAS_EQUITY", ("510900.SH", "513050.SH", "159545.SZ")),
    "汇率": ("MONETARY_POLICY", "FX", ()),
}

# 文本扫描规则（关键词→类别/资产），用于非关键词源（全球快讯等）的确定性预关联
TEXT_LINK_RULES: tuple[tuple[str, str | None, tuple[str, ...]], ...] = (
    ("黄金", "GOLD", ("518880.SH",)),
    ("金价", "GOLD", ("518880.SH",)),
    ("国债", "BOND", ("511010.SH", "511260.SH", "511090.SH", "511520.SH", "159972.SZ")),
    ("利率", "BOND", ("511010.SH", "511260.SH", "511090.SH", "511520.SH", "159972.SZ")),
    ("LPR", "BOND", ("511010.SH", "511260.SH", "511090.SH", "511520.SH", "159972.SZ")),
    ("降准", "BOND", ("511010.SH", "511260.SH", "511090.SH", "511520.SH", "159972.SZ")),
    ("降息", "BOND", ("511010.SH", "511260.SH", "511090.SH", "511520.SH", "159972.SZ")),
    ("逆回购", "BOND", ("511010.SH", "511260.SH", "511090.SH", "511520.SH", "159972.SZ")),
    ("MLF", "BOND", ("511010.SH", "511260.SH", "511090.SH", "511520.SH", "159972.SZ")),
    ("可转债", "CONVERTIBLE", ("511380.SH",)),
    ("REIT", "REIT", ("180101.SZ", "180201.SZ", "508000.SH", "508056.SH")),
    ("纳斯达克", "OVERSEAS_EQUITY", ("513100.SH",)),
    ("标普", "OVERSEAS_EQUITY", ("513500.SH",)),
    ("金龙", "OVERSEAS_EQUITY", ("513050.SH",)),      # 标注反馈：金龙指数=中概，非纳指
    ("中概", "OVERSEAS_EQUITY", ("513050.SH",)),
    ("DAX", "OVERSEAS_EQUITY", ("513030.SH",)),       # 标注反馈：欧元区/德国→DAX
    ("欧元区", "OVERSEAS_EQUITY", ("513030.SH",)),
    ("恒生", "OVERSEAS_EQUITY", ("510900.SH", "513050.SH", "159545.SZ")),
    ("港股", "OVERSEAS_EQUITY", ("510900.SH", "513050.SH", "159545.SZ")),
    ("美股", "OVERSEAS_EQUITY", ("513100.SH", "513500.SH", "162411.SZ")),
    ("石油", "COMMODITY", ("162411.SZ",)),            # 标注反馈：石油供给冲击→油气LOF
    ("油价", "COMMODITY", ("162411.SZ",)),
    ("美元指数", "FX", ()),
    ("人民币汇率", "FX", ()),
    ("沪深300", "EQUITY", ("510300.SH",)),
    ("中证500", "EQUITY", ("510500.SH",)),
    ("创业板", "EQUITY", ("159915.SZ", "159949.SZ")),
    ("科创板", "EQUITY", ("588000.SH",)),
    ("半导体", "EQUITY", ("512480.SH",)),
    ("医疗", "EQUITY", ("512170.SH",)),
    ("军工", "EQUITY", ("512660.SH",)),
    ("银行", "EQUITY", ("512800.SH",)),
    ("白酒", "EQUITY", ("512690.SH",)),
    ("新能源汽车", "EQUITY", ("515030.SH",)),
    ("红利", "EQUITY", ("510880.SH", "512890.SH", "563020.SH", "159545.SZ")),
    ("豆粕", "COMMODITY", ("159985.SZ",)),
    ("原油", "COMMODITY", ("162411.SZ",)),
)

# ------------------------------------------------------------------ 数据源注册表
# enabled=False 的源登记在册但不采集（保留扩展位，避免"为凑数量而采集"）。
SOURCES: dict[str, dict] = {
    "eastmoney-keyword-news": {
        "provider": "akshare",
        "adapter": "keyword_news",
        "enabled": True,
        "description": "东方财富关键词资讯（含正文摘要），覆盖货币政策/黄金/债券/REITs/QDII",
        "doc": "ak.stock_news_em",
    },
    "eastmoney-global-flash": {
        "provider": "akshare",
        "adapter": "global_flash",
        "enabled": True,
        "description": "东方财富全球财经快讯（标题+摘要+链接），QDII/海外市场",
        "doc": "ak.stock_info_global_em",
    },
    "eastmoney-fund-announcements": {
        "provider": "akshare",
        "adapter": "fund_announcements",
        "enabled": True,
        "description": "基金定期报告与分红公告（管理人公告转载），逐一覆盖池内基金",
        "doc": "ak.fund_announcement_report_em / fund_announcement_dividend_em",
    },
    "baidu-eco-calendar": {
        "provider": "akshare",
        "adapter": "eco_calendar",
        "enabled": True,
        "description": "宏观经济数据发布日历（含公布值/预期/前值），筛中国区",
        "doc": "ak.news_economic_baidu",
    },
    # ---- 以下为已实测可用但一期暂不启用的备选源 ----
    "ths-global-flash": {
        "provider": "akshare",
        "adapter": "ths_global",
        "enabled": False,
        "description": "同花顺全球资讯（备选；一期不启用）",
        "doc": "ak.stock_info_global_ths",
    },
    "shmet-commodity-flash": {
        "provider": "akshare",
        "adapter": "shmet",
        "enabled": False,
        "description": "上海金属网商品快讯（备选；一期不启用）",
        "doc": "ak.futures_news_shmet",
    },
}

# 已实测弃用的源（登记原因，防止后续误接）
REJECTED_SOURCES = {
    "cls-telegraph": "实测直连/代理均 45s 超时，网络不可达",
    "caixin-main": "接口无发布时间字段，违反发布时间必须可核实原则",
    "cctv-xinwenlianbo": "仅日期粒度且全文过长，信息密度低",
    "sina-global-flash": "无标题无链接，弱于东财全球快讯",
    "akshare-fund-nav": "净值数据非资讯，不在资讯采集范围",
}

# 采集默认参数
DEFAULT_KEYWORDS = tuple(KEYWORD_TOPICS.keys())
FUND_ANNOUNCE_LOOKBACK_DAYS = 60   # 公告仅保留近 60 天（当前研究，不做无法验证的历史回填）
ECO_CALENDAR_DAYS = 2              # 经济日历取今天+昨天（晚间公布延迟入库）
REQUEST_SLEEP_SECONDS = 0.3        # 源内请求间隔（限速礼貌）

# 直连域名（国内站点不走代理；与项目 config/settings.py 的 NO_PROXY 策略一致）
DIRECT_HOSTS = (
    ".eastmoney.com", ".baidu.com", ".10jqka.com.cn", ".shmet.com",
    ".sina.com.cn", ".cls.cn",
)

# ------------------------------------------------------------------ 聚合权重（版本化 v1）
# 全部确定性常量；改动任何数值必须递增 SENTIMENT_VERSION 并保留旧版本快照可查。
WEIGHTS_V1: dict = {
    "impact": {"MINOR": 0.4, "MODERATE": 1.0, "MAJOR": 1.8},
    "eco_importance": {1: 0.3, 2: 0.7, 3: 1.4},  # 经济日历重要性→impact 折算
    "half_life_hours": {  # 时效衰减 exp(-ln2 * age/half_life)
        "MONETARY_POLICY": 240.0, "MACRO_DATA": 240.0, "FUND_ANNOUNCE": 120.0,
        "GOLD_COMMODITY": 72.0, "BOND_MARKET": 72.0, "QDII_OVERSEAS": 72.0,
        "GENERAL": 48.0,
    },
    "source_diversity_cap": 1.5,     # 多来源证实增益 1 + 0.25*(n-1)，封顶
    "source_diversity_step": 0.25,
    "direct_link_weight": 1.0,       # 事件直接点名资产
    "class_link_weight": 0.45,       # 事件仅命中资产类别
    "mixed_direction_scale": 0.15,   # MIXED 事件贡献系数（方向对冲后残余）
    "asset_target_observations": 6,  # coverage 归一目标
    "market_target_observations": 10,
    "top_evidence": 10,              # evidence_ids 上限
    # 市场风险偏好符号映射（研究假设 v1：债券/黄金看涨情绪 → 避险 → 风险偏好为负贡献）
    "risk_appetite_sign": {
        "EQUITY": 1.0, "OVERSEAS_EQUITY": 1.0, "REIT": 0.8, "CONVERTIBLE": 0.8,
        "COMMODITY": 0.5, "GOLD": -0.6, "BOND": -0.8, "FX": 0.0,
    },
    # 市场层类别权重（事件对"总体市场情绪"的相关性）
    "market_class_weight": {
        "EQUITY": 1.0, "BOND": 0.9, "GOLD": 0.8, "OVERSEAS_EQUITY": 0.8,
        "COMMODITY": 0.6, "REIT": 0.6, "CONVERTIBLE": 0.7, "FX": 0.3,
    },
}

# ---- v1.1 聚合权重（在 V1 基础上引入关联分层；旧 V1 权重保留用于历史快照核对）----
WEIGHTS_V1_1: dict = {
    **{k: v for k, v in WEIGHTS_V1.items()
       if k not in ("direct_link_weight", "class_link_weight")},
    "weights_id": "v1.1",
    # 关联层级权重：只有 EXACT 是直接证据；主题/推断/类别均为间接，低权重
    "link_weight": {"EXACT": 1.0, "SAME_INDEX": 0.35,
                    "MODEL_INFERRED": 0.35, "CLASS_ONLY": 0.25},
    # 仅类别推导的个券情绪不得展示为高可信度
    "class_only_confidence_cap": 0.6,
    "min_direct_obs_for_direct_basis": 1,
}

# ------------------------------------------------------------------ 事件归并（v1.1 P0）
# 政策/数据类操作的实体识别（标题中出现的主体归一）
POLICY_ENTITIES = {
    "央行": "中国人民银行", "中国人民银行": "中国人民银行", "PBoC": "中国人民银行",
    "美联储": "美联储", "Fed": "美联储", "欧央行": "欧央行", "日本央行": "日本央行",
    "证监会": "证监会", "国家统计局": "国家统计局", "统计局": "国家统计局",
    "财政部": "财政部", "外汇局": "国家外汇管理局", "国家外汇管理局": "国家外汇管理局",
}
# 操作类型关键词（归并键的一部分：同主体同金额同日但不同操作不得合并）
OPERATION_KEYWORDS = (
    "MLF", "逆回购", "降准", "降息", "加息", "LPR", "国债买卖", "买断式逆回购",
    "CPI", "PPI", "PMI", "GDP", "社融", "M2", "M1", "LPR报价",
)
# 允许模糊归并的类别（政策/宏观数据/债券与黄金市场操作类）
MERGE_CATEGORIES = ("MONETARY_POLICY", "MACRO_DATA", "BOND_MARKET", "GOLD_COMMODITY")
MERGE_DAY_WINDOW = 1          # 同一操作跨天报道（预告/落地）允许 ±1 天
MERGE_TITLE_SIMILARITY = 0.8  # 无金额无操作词时，需标题 bigram 相似度 ≥0.8 才归并

# 分析批次默认参数
ANALYSIS_BATCH_LIMIT = 8
ANALYSIS_BATCH_LIMIT_MAX = 20
FACTOR_LOOKBACK_HOURS = 168  # 默认聚合窗口 7 天
