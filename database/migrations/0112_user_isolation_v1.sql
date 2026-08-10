-- 阶段1.2 用户隔离基础迁移
-- 新增：user_profiles / external_user_bindings / user_permissions

-- 用户档案表
CREATE TABLE IF NOT EXISTS user_profiles (
    local_user_id       VARCHAR PRIMARY KEY,          -- 业务隔离主键
    display_name        VARCHAR NOT NULL,
    risk_level          VARCHAR NOT NULL DEFAULT 'MEDIUM',  -- LOW/MEDIUM/HIGH
    total_assets        DOUBLE DEFAULT 0,             -- 总资产（元）
    max_single_position DOUBLE DEFAULT 0.20,          -- 最大单股仓位（默认20%）
    max_sector_position DOUBLE DEFAULT 0.35,          -- 最大行业仓位
    is_admin            BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP WITH TIME ZONE
);

-- 外部渠道用户绑定表
CREATE TABLE IF NOT EXISTS external_user_bindings (
    binding_id          VARCHAR PRIMARY KEY,
    external_channel    VARCHAR NOT NULL,             -- wecom / qq / desktop / api
    external_user_id    VARCHAR NOT NULL,             -- 企微user_id / QQ号 / 本地ID
    local_user_id       VARCHAR NOT NULL REFERENCES user_profiles(local_user_id),
    created_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (external_channel, external_user_id)
);

-- 用户权限表
CREATE TABLE IF NOT EXISTS user_permissions (
    permission_id       VARCHAR PRIMARY KEY,
    local_user_id       VARCHAR NOT NULL REFERENCES user_profiles(local_user_id),
    permission_level    VARCHAR NOT NULL,             -- LEVEL_1_QUERY / LEVEL_2_ANALYSIS / LEVEL_3_LOCAL_WRITE / LEVEL_4_LIVE_TRADING
    granted_by          VARCHAR NOT NULL,             -- 授权人（管理员 local_user_id）
    granted_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revoked_at          TIMESTAMP WITH TIME ZONE,
    UNIQUE (local_user_id, permission_level)
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_external_bindings_channel_user
    ON external_user_bindings (external_channel, external_user_id);
CREATE INDEX IF NOT EXISTS idx_user_permissions_user
    ON user_permissions (local_user_id);
