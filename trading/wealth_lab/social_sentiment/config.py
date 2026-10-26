"""Wealth Social Sentiment 配置与来源授权注册表种子（合规优先）。

最高原则：任何未经授权的社区评论自动采集不得上线。
- 注册表（social_sources）是唯一采集/导入闸门；UNVERIFIED / PROHIBITED /
  PENDING_AUTHORIZATION 一律默认关闭；
- robots.txt 不构成授权证据（仅作技术立场佐证）；
- 数据落独立库 database/wealth_social_sentiment.duckdb，与既有业务完全隔离。
"""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SOCIAL_DB_PATH = PROJECT_ROOT / "database" / "wealth_social_sentiment.duckdb"

MODULE_VERSION = "social-sentiment/1.0"
PROMPT_VERSION = "social-sentiment-v1"
DEEPSEEK_MODEL = "deepseek-flash"  # 结构化情绪分类（用户指定）
QWEN_ROLE = "投资者观点摘要（按需生成）"

# 操作类型（permitted_operations 取值域）
OPERATIONS = (
    "auto_collect",       # 平台自动采集
    "import_comments",    # 导入授权/获准数据
    "store_content",      # 保存评论内容
    "llm_analysis",       # LLM 分析
    "display_derived",    # 展示摘要/词频/衍生指标
    "commercial_use",     # 公开或商业化系统使用
)

STATUSES = ("AUTHORIZED", "PENDING_AUTHORIZATION", "PROHIBITED", "UNVERIFIED")

# ------------------------------------------------------------------ 注册表种子
# checked_at=2026-10-25 人工核对官方协议原文；证据与结论详见 AUTHORIZATION_REPORT.md。
REGISTRY_SEED: list[dict] = [
    {
        "source_id": "eastmoney-guba",
        "platform": "东方财富股吧",
        "terms_url": "https://about.eastmoney.com/home/protocol",
        "checked_at": "2026-10-25",
        "status": "PROHIBITED",
        "authorized_scope": None,
        "permitted_operations": [],
        "retention_limit_days": 0,
        "authorization_evidence": (
            "《东方财富用户服务协议》(2025-07 版)第六章第1条：平台内容未经事先书面许可"
            "不得以任何形式使用或创造衍生作品（涵盖保存评论/摘要/词频/衍生指标）；"
            "第七章第2条禁止第三方软件干扰产品运行；无公开评论数据 API。"
        ),
        "notes": "唯一合规路径：东方财富 Choice 商业授权（含舆情数据，choice.eastmoney.com）",
        "application_channel": "Choice 商务合作：choice.eastmoney.com / quantapi.eastmoney.com",
    },
    {
        "source_id": "xueqiu-community",
        "platform": "雪球",
        "terms_url": "https://xueqiu.com/about/terms",
        "checked_at": "2026-10-25",
        "status": "PROHIBITED",
        "authorized_scope": None,
        "permitted_operations": [],
        "retention_limit_days": 0,
        "authorization_evidence": (
            "《雪球服务协议》第三条5(2) 禁止任何爬虫/抓取工具访问、存储、索引；"
            "5(7) 禁止非人工方式使用；6(4) 与第七条禁止未经同意将内容用于 AI（含 RAG/训练）；"
            "5(9) 禁止收费/获利性第三方提供；robots.txt 本身亦处反爬拦截（302 挑战）。"
        ),
        "notes": "LLM 分析被条款明确限制，需雪球书面授权方可开展",
        "application_channel": "雪球官方商务合作渠道（书面授权，xueqiu.com/about 联系方式）",
    },
    {
        "source_id": "cninfo-irm",
        "platform": "深交所互动易（irm.cninfo.com.cn）",
        "terms_url": "https://webapi.cninfo.com.cn",
        "checked_at": "2026-10-25",
        "status": "PENDING_AUTHORIZATION",
        "authorized_scope": None,
        "permitted_operations": [],
        "retention_limit_days": 90,
        "authorization_evidence": (
            "深证信数据服务平台（webapi.cninfo.com.cn）提供正式 API 授权申请通道，"
            "覆盖上市公司/基金/公告/互动问答；尚未取得本项目授权，未启用任何采集。"
        ),
        "notes": "申请通过并核对授权范围后方可启用导入/采集",
        "application_channel": "webapi.cninfo.com.cn 平台注册申请数据服务授权",
    },
    {
        "source_id": "tushare-irm-relay",
        "platform": "Tushare Pro 深证易互动问答（持牌数据服务商转授）",
        "terms_url": "https://tushare.pro/document/2?doc_id=356",
        "checked_at": "2026-10-25",
        "status": "PENDING_AUTHORIZATION",
        "authorized_scope": None,
        "permitted_operations": [],
        "retention_limit_days": 90,
        "authorization_evidence": (
            "Tushare Pro 提供互动易问答接口；本项目已持有 Tushare 商用 Token，"
            "但其协议对衍生研究与再展示的授权范围尚未逐条核对，暂不启用。"
        ),
        "notes": "需核对 Tushare 服务协议对评论类数据的存储/展示/LLM 处理条款",
        "application_channel": "已有 Tushare Pro 账号（.env 凭证），待条款核对",
    },
    {
        "source_id": "sse-einteract",
        "platform": "上证 e 互动（sns.sseinfo.com）",
        "terms_url": "https://sns.sseinfo.com",
        "checked_at": "2026-10-25",
        "status": "UNVERIFIED",
        "authorized_scope": None,
        "permitted_operations": [],
        "retention_limit_days": 0,
        "authorization_evidence": "上交所官方投资者互动平台；使用条款与自动化访问政策未完成核查。",
        "notes": "默认关闭，待核查",
        "application_channel": None,
    },
    {
        "source_id": "jisilu-community",
        "platform": "集思录",
        "terms_url": "https://www.jisilu.cn",
        "checked_at": "2026-10-25",
        "status": "UNVERIFIED",
        "authorized_scope": None,
        "permitted_operations": [],
        "retention_limit_days": 0,
        "authorization_evidence": "服务条款与数据使用政策未完成核查；存在会员 API 但授权范围不明。",
        "notes": "默认关闭，待核查",
        "application_channel": None,
    },
    {
        "source_id": "manual-authorized-file",
        "platform": "人工导入的授权文件（用户自备且声明已获授权）",
        "terms_url": None,
        "checked_at": "2026-10-25",
        "status": "AUTHORIZED",
        "authorized_scope": "用户显式声明已获平台授权的文件导入，仅研究用途",
        "permitted_operations": ["import_comments", "store_content", "llm_analysis",
                                 "display_derived"],
        "retention_limit_days": 180,
        "authorization_evidence": (
            "导入时用户必须提交 authorization_assertion（授权依据声明），"
            "记录于每条评论的 authorization_ref；责任由导入声明人承担，系统留存审计。"
        ),
        "notes": "唯一默认启用来源；不含 commercial_use（商业化需另行授权）",
        "application_channel": "本模块 POST /import 接口",
    },
    {
        "source_id": "synthetic-test-lab",
        "platform": "合成测试数据（仅联调，永不计入研究结论）",
        "terms_url": None,
        "checked_at": "2026-10-25",
        "status": "AUTHORIZED",
        "authorized_scope": "单元测试与链路联调；data_origin 固定为 synthetic_test",
        "permitted_operations": ["import_comments", "store_content", "llm_analysis",
                                 "display_derived"],
        "retention_limit_days": 7,
        "authorization_evidence": "自造数据，无第三方权利；接口默认 include_synthetic=false。",
        "notes": "严禁与真实数据混用；严禁以合成数据冒充真实研究",
        "application_channel": None,
    },
]

# ------------------------------------------------------------------ 词云
# 停用词：功能词 + 泛噪声（广告/水文标记），可按需扩展；词频为确定性统计，不经 LLM。
STOPWORDS_ZH: set[str] = set("""
的 了 在 是 我 你 他 她 它 我们 你们 他们 姐妹 大家伙 和 与 及 跟 就 都 也 很 还 要 不 没
有 是不是 什么 怎么 怎么样 为什么 这个 那个 这些 那些 现在 今天 昨天 明天 上午 下午 晚上
啊 吧 呢 吗 哦 嗯 呵 哈哈 哈哈哈 的确 其实 感觉 觉得 应该 可能 一直 已经 还是 就是 不是
但是 因为 所以 如果 虽然 然后 而且 不过 一下 一个 一些 一直 起来 出来 进去 时候 地方
分享 查看 点击 更多 详情 链接 网址 加群 群号 微信 vx 电话 优惠 折扣 领取 免费送 广告
老师 大神 求带 带单 指导 建仓 跟单 满仓 干一票 一字板 涨停敢死队 私信 私聊
etf基金 就是说 也就是说 等等 之类 什么的 而已 罢了
""".split())

# 金融主题词（词频单独聚合；分词后按词典匹配，非 LLM 生成）
FINANCE_TOPIC_TERMS: tuple[tuple[str, str], ...] = (
    ("利率", "RATES"), ("降息", "RATES"), ("加息", "RATES"), ("LPR", "RATES"),
    ("降准", "RATES"), ("逆回购", "RATES"), ("MLF", "RATES"), ("国债", "BOND"),
    ("债券", "BOND"), ("转债", "CONVERTIBLE"), ("可转债", "CONVERTIBLE"),
    ("黄金", "GOLD"), ("金价", "GOLD"), ("白银", "GOLD"),
    ("原油", "COMMODITY"), ("商品", "COMMODITY"), ("豆粕", "COMMODITY"),
    ("REITs", "REIT"), ("REIT", "REIT"), ("基础设施", "REIT"),
    ("纳斯达克", "OVERSEAS"), ("标普", "OVERSEAS"), ("美股", "OVERSEAS"),
    ("恒生", "OVERSEAS"), ("港股", "OVERSEAS"), ("QDII", "OVERSEAS"),
    ("汇率", "FX"), ("人民币", "FX"), ("美元", "FX"),
    ("分红", "DIVIDEND"), ("净值", "FUND_NAV"), ("申购", "FUND_FLOW"),
    ("赎回", "FUND_FLOW"), ("溢价", "PREMIUM"), ("折价", "PREMIUM"),
    ("管理费", "FUND_FEE"), ("牛市", "MARKET_REGIME"), ("熊市", "MARKET_REGIME"),
    ("震荡", "MARKET_REGIME"), ("抄底", "BEHAVIOR"), ("追涨", "BEHAVIOR"),
    ("割肉", "BEHAVIOR"), ("满仓", "BEHAVIOR"), ("定投", "BEHAVIOR"),
)

# 分析批次
ANALYSIS_BATCH_LIMIT_MAX = 60
DEFAULT_RETENTION_DAYS = 180
PII_FIELDS_REJECTED = ("nickname", "avatar", "profile_url", "contact", "email",
                       "phone", "user_id", "ip")
