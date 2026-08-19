-- 登记金融工具标准化目录。
--
-- 本迁移只修改 source 下的两个目录表，不修改 instrument_master 的业务数据，
-- 也不创建新的数据库表。使用 ON CONFLICT 保证重复执行不会重复插入记录。

INSERT INTO source.dataset_catalog (
    dataset_id,
    dataset_name,
    dataset_type,
    provider,
    description,
    frequency,
    data_category,
    access_method,
    storage_table_name,
    created_at,
    updated_at
)
VALUES (
    'INSTRUMENT_MASTER',
    'Instrument Master Directory',
    'instrument_directory',
    NULL,
    'Standard financial instrument master used to resolve canonical symbols and active instrument identities',
    'static',
    'Instrument',
    'resolve_instrument()',
    'instrument_master',
    CURRENT_TIMESTAMP,
    CURRENT_TIMESTAMP
)
ON CONFLICT (dataset_id) DO NOTHING;

-- field_name 使用 source.instrument_master 的实际小写物理列名。
-- 只登记标准化路由需要返回的字段，避免把创建时间等技术元数据暴露为业务结果。
INSERT INTO source.dataset_field_catalog (
    field_id,
    dataset_id,
    field_name,
    business_name,
    description,
    data_type,
    unit,
    created_at,
    updated_at
)
VALUES
    (
        'INSTRUMENT_MASTER.INSTRUMENT_ID',
        'INSTRUMENT_MASTER',
        'instrument_id',
        'Instrument ID',
        '正式金融工具唯一标识',
        'string',
        NULL,
        CURRENT_TIMESTAMP,
        CURRENT_TIMESTAMP
    ),
    (
        'INSTRUMENT_MASTER.CANONICAL_SYMBOL',
        'INSTRUMENT_MASTER',
        'canonical_symbol',
        'Canonical Symbol',
        '金融工具标准代码',
        'string',
        NULL,
        CURRENT_TIMESTAMP,
        CURRENT_TIMESTAMP
    ),
    (
        'INSTRUMENT_MASTER.NAME',
        'INSTRUMENT_MASTER',
        'name',
        'Instrument Name',
        '金融工具名称',
        'string',
        NULL,
        CURRENT_TIMESTAMP,
        CURRENT_TIMESTAMP
    ),
    (
        'INSTRUMENT_MASTER.INSTRUMENT_TYPE',
        'INSTRUMENT_MASTER',
        'instrument_type',
        'Instrument Type',
        '金融工具类型',
        'string',
        NULL,
        CURRENT_TIMESTAMP,
        CURRENT_TIMESTAMP
    ),
    (
        'INSTRUMENT_MASTER.STATUS',
        'INSTRUMENT_MASTER',
        'status',
        'Status',
        '金融工具当前状态；标准化路由只返回 active 记录',
        'string',
        NULL,
        CURRENT_TIMESTAMP,
        CURRENT_TIMESTAMP
    )
ON CONFLICT (dataset_id, field_name) DO NOTHING;
