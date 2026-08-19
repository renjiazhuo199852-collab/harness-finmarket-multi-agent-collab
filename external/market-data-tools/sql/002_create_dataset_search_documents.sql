-- 数据集目录的独立 AI 检索表。
--
-- 这张表只对应 source.dataset_catalog，不与金融工具检索表共用数据行。
-- source 仍然是正式目录，ai_search 只保存检索所需的目录镜像和派生索引。

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') THEN
        RAISE EXCEPTION '需要数据库管理员预先安装 pg_trgm 扩展';
    END IF;
END
$$;

CREATE SCHEMA IF NOT EXISTS ai_search;

CREATE TABLE IF NOT EXISTS ai_search.dataset_search_documents (
    document_id BIGSERIAL PRIMARY KEY,
    dataset_id TEXT NOT NULL UNIQUE,
    dataset_name TEXT NOT NULL DEFAULT '',
    dataset_type TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    frequency TEXT NOT NULL DEFAULT '',
    data_category TEXT NOT NULL DEFAULT '',
    access_method TEXT NOT NULL DEFAULT '',
    storage_table_name TEXT NOT NULL DEFAULT '',
    source_created_at TIMESTAMPTZ,
    source_updated_at TIMESTAMPTZ,
    search_vector TSVECTOR NOT NULL,
    embedding JSONB
);

-- 已配置服务器使用 halfvec(2048)；只有旧 JSONB 空列才允许转换，避免覆盖向量。
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')
       AND EXISTS (
           SELECT 1 FROM information_schema.columns
           WHERE table_schema = 'ai_search'
             AND table_name = 'dataset_search_documents'
             AND column_name = 'embedding'
             AND udt_name = 'jsonb'
       ) THEN
        EXECUTE 'ALTER TABLE ai_search.dataset_search_documents
                 ALTER COLUMN embedding TYPE halfvec(2048)
                 USING NULL::halfvec(2048)';
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_dataset_search_documents_search_vector
    ON ai_search.dataset_search_documents USING GIN (search_vector);
CREATE INDEX IF NOT EXISTS idx_dataset_search_documents_dataset_id_trgm
    ON ai_search.dataset_search_documents USING GIN (dataset_id gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_dataset_search_documents_dataset_name_trgm
    ON ai_search.dataset_search_documents USING GIN (dataset_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_dataset_search_documents_data_category_trgm
    ON ai_search.dataset_search_documents USING GIN (data_category gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_dataset_search_documents_provider
    ON ai_search.dataset_search_documents (provider);

-- 向量索引由服务器初始化流程创建；这里仅在扩展可用时确保索引存在。
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_dataset_search_documents_embedding_hnsw
                 ON ai_search.dataset_search_documents USING HNSW (embedding halfvec_cosine_ops)
                 WHERE embedding IS NOT NULL';
    END IF;
END
$$;
