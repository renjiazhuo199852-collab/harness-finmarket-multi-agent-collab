import importlib.util
from pathlib import Path


_MODULE_PATH = Path(__file__).parents[1] / "backend" / "ai_search" / "public_response.py"
_SPEC = importlib.util.spec_from_file_location("provider_public_response", _MODULE_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
build_evidence_response = _MODULE.build_evidence_response


def test_evidence_response_preserves_only_business_and_provenance_fields():
    result = build_evidence_response(
        {
            "adapter": "news_articles",
            "execution": {
                "status": "resolved",
                "rows": [
                    {
                        "data": {"title": "EURUSD outlook", "content": "..."},
                        "metadata": {
                            "article_id": "n-1",
                            "publish_time": "2026-08-17T00:00:00+00:00",
                            "source": "LSEG",
                        },
                    }
                ],
                "row_count": 1,
                "dataset_id": "LSEG_NEWS",
                "storage_table_name": "news_articles",
            },
        }
    )

    assert result["status"] == "success"
    assert result["schema_version"] == "fx-evidence.v1"
    assert result["data"][0]["data"]["title"] == "EURUSD outlook"
    assert result["data"][0]["metadata"]["publish_time"] == "2026-08-17T00:00:00+00:00"
    assert result["meta"]["dataset_id"] == "LSEG_NEWS"
