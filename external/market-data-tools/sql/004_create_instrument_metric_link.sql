-- 为“查询与 EURUSD 相关的宏观指标”建立正式关系表。
--
-- 关系不是由模型生成，也不修改现有 source 业务表的数据。脚本可以重复执行：
-- 表、约束、索引和 EURUSD 的 16 条 METRIC 关系均使用 IF NOT EXISTS 或 UPSERT。

CREATE TABLE IF NOT EXISTS source.instrument_metric_link (
    link_id BIGSERIAL PRIMARY KEY,
    instrument_id TEXT NOT NULL,
    metric_id TEXT NOT NULL,
    relationship_role TEXT NOT NULL
        CHECK (relationship_role IN ('base_currency', 'quote_currency')),
    provider TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'inactive')),
    effective_date DATE,
    expire_date DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT instrument_metric_link_date_range_ck
        CHECK (expire_date IS NULL OR effective_date IS NULL OR effective_date < expire_date),
    CONSTRAINT instrument_metric_link_unique_key
        UNIQUE (instrument_id, metric_id, provider)
);

CREATE INDEX IF NOT EXISTS idx_instrument_metric_link_instrument_provider_status
    ON source.instrument_metric_link (instrument_id, provider, status);

CREATE INDEX IF NOT EXISTS idx_instrument_metric_link_metric_provider
    ON source.instrument_metric_link (metric_id, provider);

-- 当前 instrument_master 的正式唯一键是 canonical_symbol，instrument_id 没有
-- 独立 UNIQUE 约束，PostgreSQL 因此不能建立直接外键。触发器承担等价的业务约束：
-- 只允许 active 工具和已有观测数据登记关系。观测表也没有稳定的 metric_id 外键，
-- 所以使用 metric_id + provider 做存在性校验。
CREATE OR REPLACE FUNCTION source.validate_instrument_metric_link()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM source.instrument_master
        WHERE instrument_id = NEW.instrument_id AND status = 'active'
    ) THEN
        RAISE EXCEPTION 'instrument_metric_link requires an active instrument: %', NEW.instrument_id;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM source.macro_observations
        WHERE metric_id = NEW.metric_id AND source = NEW.provider
    ) THEN
        RAISE EXCEPTION 'instrument_metric_link requires an observed metric: % / %', NEW.metric_id, NEW.provider;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_validate_instrument_metric_link
    ON source.instrument_metric_link;
CREATE TRIGGER trg_validate_instrument_metric_link
BEFORE INSERT OR UPDATE OF instrument_id, metric_id, provider
ON source.instrument_metric_link
FOR EACH ROW
EXECUTE FUNCTION source.validate_instrument_metric_link();

-- 只取 EUR/US 两侧、instrument_id 已明确为 METRIC 的观测序列，因此不会把
-- INTEREST_RATE 或 BOND_YIELD 关系混入当前首期字段目录支持范围。
INSERT INTO source.instrument_metric_link (
    instrument_id,
    metric_id,
    relationship_role,
    provider,
    status,
    effective_date,
    expire_date,
    created_at,
    updated_at
)
SELECT
    'FX_EURUSD',
    observation.metric_id,
    CASE observation.country
        WHEN 'EU' THEN 'base_currency'
        WHEN 'US' THEN 'quote_currency'
    END,
    observation.source,
    'active',
    NULL,
    NULL,
    CURRENT_TIMESTAMP,
    CURRENT_TIMESTAMP
FROM source.macro_observations AS observation
JOIN source.instrument_master AS instrument
  ON instrument.instrument_id = 'FX_EURUSD'
 AND instrument.status = 'active'
 AND instrument.base_currency = 'EUR'
 AND instrument.quote_currency = 'USD'
WHERE observation.source = 'LSEG'
  AND observation.country IN ('EU', 'US')
  AND observation.instrument_id LIKE 'METRIC_%'
GROUP BY observation.metric_id, observation.country, observation.source
ON CONFLICT (instrument_id, metric_id, provider)
DO UPDATE SET
    relationship_role = EXCLUDED.relationship_role,
    status = 'active',
    effective_date = EXCLUDED.effective_date,
    expire_date = EXCLUDED.expire_date,
    updated_at = CURRENT_TIMESTAMP;

-- 迁移自检：首期必须登记欧元区和美国两侧的 16 个 METRIC 关系。
DO $$
DECLARE
    relation_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO relation_count
    FROM source.instrument_metric_link
    WHERE instrument_id = 'FX_EURUSD'
      AND provider = 'LSEG'
      AND status = 'active';
    IF relation_count <> 16 THEN
        RAISE EXCEPTION 'expected 16 active EURUSD metric links, got %', relation_count;
    END IF;
END;
$$;
