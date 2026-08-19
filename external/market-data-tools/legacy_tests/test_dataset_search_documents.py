"""数据集目录 AI 检索文档构建逻辑测试。"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from build_dataset_search_documents import text_value  # noqa: E402
from generate_dataset_embeddings import dataset_embedding_text  # noqa: E402


def test_dataset_document_empty_values_are_safe_for_search_text() -> None:
    """目录中的 NULL 字段应转换为空字符串，不影响整行生成。"""

    assert text_value(None) == ""
    assert text_value(" LSEG ") == "LSEG"


def test_dataset_search_table_does_not_use_instrument_fields() -> None:
    """数据集文档的设计必须与金融工具文档保持数据边界。"""

    source = (Path(PROJECT_ROOT) / "sql" / "002_create_dataset_search_documents.sql").read_text(encoding="utf-8")

    assert "dataset_search_documents" in source
    # 说明性注释可以提到边界字段，但 CREATE TABLE 中不能把它们定义成列。
    assert "\n    instrument_id " not in source
    assert "\n    canonical_symbol " not in source


def test_dataset_embedding_text_excludes_storage_table_name() -> None:
    """数据集语义向量应使用目录语义，不把物理表名作为意图输入。"""

    text = dataset_embedding_text(
        (
            "LSEG_SPOT_PRICE",
            "LSEG Spot Price Snapshot",
            "market_data",
            "LSEG",
            "Latest current spot price",
            "realtime",
            "Spot_Price",
            "get_fx_spot()",
        )
    )

    assert "data_category: Spot_Price" in text
    assert "latest_prices" not in text
    assert "provider: LSEG" not in text
    assert "access_method: get_fx_spot()" not in text
