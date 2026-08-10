-- 阶段2.1 TradePlan 表迁移
CREATE TABLE IF NOT EXISTS trade_plans (
    plan_id             VARCHAR PRIMARY KEY,
    local_user_id       VARCHAR NOT NULL,               -- 用户隔离主键
    symbol              VARCHAR NOT NULL,
    stock_name          VARCHAR,
    action              VARCHAR NOT NULL,               -- 动作枚举
    current_position    DOUBLE DEFAULT 0,
    recommended_position DOUBLE DEFAULT 0,
    position_change     DOUBLE DEFAULT 0,
    current_price       DOUBLE DEFAULT 0,
    entry_zone_json     VARCHAR,                        -- [low, high]
    add_zone_json       VARCHAR,
    reduce_zone_json    VARCHAR,
    stop_loss_price     DOUBLE,
    stop_loss_condition VARCHAR,
    take_profit_zone_json VARCHAR,
    invalidation_condition VARCHAR,
    expected_holding_period VARCHAR,
    risk_reward_ratio   DOUBLE DEFAULT 0,
    formal_score        DOUBLE DEFAULT 0,
    enhanced_score      DOUBLE,
    confidence          DOUBLE DEFAULT 0,
    data_status         VARCHAR DEFAULT 'FRESH',
    snapshot_id         VARCHAR,
    data_cutoff         VARCHAR,
    strategy_version    VARCHAR,
    veto_status         VARCHAR DEFAULT 'NO_VETO',
    supporting_evidence_json VARCHAR,
    major_risks_json    VARCHAR,
    status              VARCHAR DEFAULT 'DRAFT',
    created_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at          TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_trade_plans_user ON trade_plans (local_user_id);
CREATE INDEX IF NOT EXISTS idx_trade_plans_symbol ON trade_plans (symbol);
