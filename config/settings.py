from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """Hermes-OPC runtime configuration."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Financial data
    tushare_token: str | None = Field(default=None)

    # Search
    tavily_api_key: str | None = Field(default=None)

    # Model providers, used later by Agent Router
    deepseek_api_key: str | None = Field(default=None)
    longcat_api_key: str | None = Field(default=None)
    dashscope_api_key: str | None = Field(default=None)
    xiaomi_api_key: str | None = Field(default=None)
    tokenhub_api_key: str | None = Field(default=None)

    # Local runtime
    opc_database_path: Path = Field(
        default=PROJECT_ROOT / "database" / "hermes_opc.duckdb"
    )
    opc_log_level: str = Field(default="INFO")
    opc_router_host: str = Field(default="127.0.0.1")
    opc_router_port: int = Field(default=8765)
    opc_mcp_host: str = Field(default="127.0.0.1")
    opc_mcp_port: int = Field(default=8767)
    opc_mcp_transport: str = Field(default="stdio")
    opc_no_proxy: str = Field(
        default=(
            "localhost,127.0.0.1,push2his.eastmoney.com,"
            ".eastmoney.com,.tushare.pro,.baostock.com"
        )
    )

    # Canonical data policy. Comma-separated priorities are intentionally
    # environment-configurable without changing provider or strategy code.
    canonical_market_source_priority: str = Field(
        default="Tushare Pro,AKShare / Eastmoney,BaoStock"
    )
    canonical_financial_source_priority: str = Field(
        default="Tushare Pro,CNInfo,AKShare,BaoStock"
    )
    canonical_price_tolerance: float = Field(default=0.005, ge=0, le=0.25)
    canonical_volume_tolerance: float = Field(default=0.01, ge=0, le=0.50)
    canonical_financial_tolerance: float = Field(default=0.01, ge=0, le=0.50)

    # Fundamental point-in-time policy. Financial ratios are decimals internally
    # (for example 18% is stored as 0.18); missing values are never coerced to 0.
    fundamental_disclosure_date_available_hour: int = Field(
        default=18,
        ge=0,
        le=23,
    )
    fundamental_stale_annual_days: int = Field(default=500, ge=1)
    fundamental_stale_semiannual_days: int = Field(default=260, ge=1)
    fundamental_stale_quarterly_days: int = Field(default=150, ge=1)
    fundamental_confidence_verified: float = Field(default=1.0, ge=0, le=1)
    fundamental_confidence_single_source: float = Field(
        default=0.6,
        ge=0,
        le=1,
    )
    fundamental_confidence_disclosure_missing: float = Field(
        default=0.2,
        ge=0,
        le=1,
    )
    fundamental_confidence_conflict: float = Field(
        default=0.0,
        ge=0,
        le=1,
    )
    fundamental_current_fetch_max_age_days: int = Field(default=7, ge=0)
    fundamental_fetch_lookback_days: int = Field(default=550, ge=90)
    fundamental_fetch_max_attempts: int = Field(default=2, ge=1, le=3)

    # Sentiment v1 policy. Model output is only structured input to the local
    # deterministic scorer; every persisted sentiment factor remains shadow.
    sentiment_source_official_weight: float = Field(default=1.0, ge=0, le=1)
    sentiment_source_authoritative_media_weight: float = Field(
        default=0.8,
        ge=0,
        le=1,
    )
    sentiment_source_media_weight: float = Field(default=0.5, ge=0, le=1)
    sentiment_source_analyst_weight: float = Field(default=0.4, ge=0, le=1)
    sentiment_source_unknown_weight: float = Field(default=0.2, ge=0, le=1)
    sentiment_source_rumor_weight: float = Field(default=0.1, ge=0, le=1)
    sentiment_verification_official_weight: float = Field(
        default=1.0,
        ge=0,
        le=1,
    )
    sentiment_verification_multi_source_weight: float = Field(
        default=0.9,
        ge=0,
        le=1,
    )
    sentiment_verification_single_official_weight: float = Field(
        default=0.85,
        ge=0,
        le=1,
    )
    sentiment_verification_single_media_weight: float = Field(
        default=0.5,
        ge=0,
        le=1,
    )
    sentiment_verification_conflict_weight: float = Field(
        default=0.0,
        ge=0,
        le=1,
    )
    sentiment_verification_unverified_weight: float = Field(
        default=0.25,
        ge=0,
        le=1,
    )
    sentiment_verification_retracted_weight: float = Field(
        default=0.0,
        ge=0,
        le=1,
    )
    sentiment_half_life_intraday_hours: float = Field(default=6, gt=0)
    sentiment_half_life_1d_hours: float = Field(default=24, gt=0)
    sentiment_half_life_1_5d_hours: float = Field(default=72, gt=0)
    sentiment_half_life_1_4w_hours: float = Field(default=336, gt=0)
    sentiment_half_life_long_term_hours: float = Field(default=2160, gt=0)
    sentiment_half_life_unknown_hours: float = Field(default=48, gt=0)
    sentiment_max_single_event_contribution: float = Field(
        default=0.5,
        gt=0,
        le=1,
    )
    sentiment_model_max_retries: int = Field(default=1, ge=0, le=1)
    sentiment_max_events_per_request: int = Field(default=20, ge=1, le=100)
    sentiment_escalation_confidence_threshold: float = Field(
        default=0.45,
        ge=0,
        le=1,
    )
    sentiment_escalation_intensity_threshold: float = Field(
        default=0.85,
        ge=0,
        le=1,
    )
    sentiment_expected_market_universe_size: int = Field(default=5000, ge=1)
    sentiment_market_partial_confidence_cap: float = Field(
        default=0.35,
        ge=0,
        le=1,
    )
    sentiment_backfill_model_call_budget: int = Field(default=10, ge=0)

    # Policy/news v1 policy. These values are local, deterministic controls;
    # model output may propose structured facts but cannot change the weights.
    policy_news_source_central_government_weight: float = Field(
        default=1.0,
        ge=0,
        le=1,
    )
    policy_news_source_regulator_weight: float = Field(
        default=1.0,
        ge=0,
        le=1,
    )
    policy_news_source_local_government_weight: float = Field(
        default=0.9,
        ge=0,
        le=1,
    )
    policy_news_source_company_announcement_weight: float = Field(
        default=1.0,
        ge=0,
        le=1,
    )
    policy_news_source_authoritative_media_weight: float = Field(
        default=0.75,
        ge=0,
        le=1,
    )
    policy_news_source_media_weight: float = Field(
        default=0.5,
        ge=0,
        le=1,
    )
    policy_news_source_analyst_weight: float = Field(
        default=0.35,
        ge=0,
        le=1,
    )
    policy_news_source_unknown_weight: float = Field(
        default=0.2,
        ge=0,
        le=1,
    )
    policy_news_source_rumor_weight: float = Field(
        default=0.1,
        ge=0,
        le=1,
    )
    policy_news_implementation_rumor_weight: float = Field(
        default=0.10,
        ge=0,
        le=1,
    )
    policy_news_implementation_draft_weight: float = Field(
        default=0.30,
        ge=0,
        le=1,
    )
    policy_news_implementation_consultation_weight: float = Field(
        default=0.45,
        ge=0,
        le=1,
    )
    policy_news_implementation_announced_weight: float = Field(
        default=0.70,
        ge=0,
        le=1,
    )
    policy_news_implementation_approved_weight: float = Field(
        default=0.85,
        ge=0,
        le=1,
    )
    policy_news_implementation_implementing_weight: float = Field(
        default=0.95,
        ge=0,
        le=1,
    )
    policy_news_implementation_executed_weight: float = Field(
        default=1.0,
        ge=0,
        le=1,
    )
    policy_news_implementation_suspended_weight: float = Field(
        default=0.20,
        ge=0,
        le=1,
    )
    policy_news_implementation_terminated_weight: float = Field(
        default=0.0,
        ge=0,
        le=1,
    )
    policy_news_implementation_retracted_weight: float = Field(
        default=0.0,
        ge=0,
        le=1,
    )
    policy_news_implementation_unknown_weight: float = Field(
        default=0.20,
        ge=0,
        le=1,
    )
    policy_news_verification_official_weight: float = Field(
        default=1.0,
        ge=0,
        le=1,
    )
    policy_news_verification_multi_source_weight: float = Field(
        default=0.9,
        ge=0,
        le=1,
    )
    policy_news_verification_single_official_weight: float = Field(
        default=0.85,
        ge=0,
        le=1,
    )
    policy_news_verification_single_media_weight: float = Field(
        default=0.5,
        ge=0,
        le=1,
    )
    policy_news_verification_unverified_weight: float = Field(
        default=0.2,
        ge=0,
        le=1,
    )
    policy_news_verification_conflict_weight: float = Field(
        default=0.0,
        ge=0,
        le=1,
    )
    policy_news_freshness_half_life_short_hours: float = Field(
        default=72,
        gt=0,
    )
    policy_news_freshness_half_life_medium_hours: float = Field(
        default=720,
        gt=0,
    )
    policy_news_freshness_half_life_long_hours: float = Field(
        default=2160,
        gt=0,
    )
    policy_news_title_only_confidence_cap: float = Field(
        default=0.35,
        ge=0,
        le=1,
    )
    policy_news_partial_text_confidence_cap: float = Field(
        default=0.65,
        ge=0,
        le=1,
    )
    policy_news_max_single_event_contribution: float = Field(
        default=0.5,
        gt=0,
        le=1,
    )
    policy_news_model_max_retries: int = Field(default=1, ge=0, le=1)
    policy_news_max_events_per_request: int = Field(default=20, ge=1, le=100)
    policy_news_escalation_confidence_threshold: float = Field(
        default=0.45,
        ge=0,
        le=1,
    )
    policy_news_escalation_intensity_threshold: float = Field(
        default=0.85,
        ge=0,
        le=1,
    )
    policy_news_date_only_available_hour: int = Field(
        default=23,
        ge=0,
        le=23,
    )
    policy_news_backfill_model_call_budget: int = Field(default=5, ge=0)

    # Capital-flow v1 is deterministic and shadow-only. Units are CNY, shares,
    # and decimal rates. These thresholds never alter formal strategy weights.
    capital_flow_short_window: int = Field(default=5, ge=2, le=20)
    capital_flow_medium_window: int = Field(default=20, ge=5, le=60)
    capital_flow_long_window: int = Field(default=60, ge=20, le=250)
    capital_flow_price_change_threshold: float = Field(
        default=0.005,
        gt=0,
        le=0.10,
    )
    capital_flow_volume_change_threshold: float = Field(
        default=0.10,
        gt=0,
        le=1,
    )
    capital_flow_high_turnover_threshold: float = Field(
        default=0.20,
        gt=0,
        le=1,
    )
    capital_flow_low_liquidity_amount: float = Field(default=20_000_000, ge=0)
    capital_flow_high_liquidity_amount: float = Field(default=200_000_000, ge=0)
    capital_flow_stale_market_days: int = Field(default=7, ge=1, le=30)
    capital_flow_expected_market_universe_size: int = Field(default=5000, ge=1)
    capital_flow_partial_confidence_cap: float = Field(default=0.35, ge=0, le=1)
    capital_flow_estimated_flow_confidence_cap: float = Field(
        default=0.25,
        ge=0,
        le=0.5,
    )
    capital_flow_volume_weight: float = Field(default=0.20, ge=0, le=1)
    capital_flow_amount_weight: float = Field(default=0.20, ge=0, le=1)
    capital_flow_turnover_weight: float = Field(default=0.15, ge=0, le=1)
    capital_flow_price_volume_weight: float = Field(default=0.20, ge=0, le=1)
    capital_flow_financing_weight: float = Field(default=0.10, ge=0, le=1)
    capital_flow_sector_flow_weight: float = Field(default=0.10, ge=0, le=1)
    capital_flow_liquidity_weight: float = Field(default=0.05, ge=0, le=1)
    capital_flow_max_single_component_contribution: float = Field(
        default=0.40,
        gt=0,
        le=1,
    )

    # Full-market data foundation. These caps bound network and model work;
    # they do not change any formal strategy weight.
    full_market_universe_request_budget: int = Field(default=3, ge=1, le=20)
    full_market_snapshot_request_budget: int = Field(default=1, ge=1, le=5)
    full_market_snapshot_max_age_seconds: int = Field(
        default=300,
        ge=0,
        le=86_400,
    )
    full_market_expansion_request_budget: int = Field(
        default=10,
        ge=0,
        le=10_000,
    )
    full_market_event_batch_size: int = Field(default=100, ge=1, le=1000)
    full_market_candidate_limit: int = Field(default=20, ge=1, le=30)
    full_market_candidate_model_call_limit: int = Field(
        default=10,
        ge=0,
        le=10,
    )

    # Historical daily-bar expansion. RAW is the only provider-verified
    # default; changing adjustment never changes strategy weights.
    history_default_adjustment: str = Field(default="RAW")
    history_default_provider: str = Field(default="BAOSTOCK")
    history_batch_size: int = Field(default=100, ge=1, le=300)
    history_concurrency: int = Field(default=1, ge=1, le=3)
    history_request_budget: int = Field(default=100, ge=1, le=10_000)
    history_max_retries: int = Field(default=1, ge=0, le=3)
    history_max_symbols_per_run: int = Field(default=300, ge=1, le=6000)
    history_minimum_free_bytes: int = Field(
        default=2_147_483_648,
        ge=0,
    )
    history_market_close_available_hour: int = Field(default=16, ge=15, le=23)
    history_long_suspension_min_snapshots: int = Field(default=10, ge=2, le=60)


settings = Settings()
