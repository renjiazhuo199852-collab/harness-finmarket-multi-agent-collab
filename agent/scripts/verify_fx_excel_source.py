"""Read-only verification of an FX database-export workbook."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parents[1]
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from src.fx_debate.context import build_evidence_context  # noqa: E402
from src.fx_debate.evidence_factory import FxEvidenceFactory  # noqa: E402
from src.fx_debate.evidence_sources import ExcelFxEvidenceSource  # noqa: E402
from src.fx_debate.models import RunOptions  # noqa: E402
from src.fx_debate.request_adapter import (  # noqa: E402
    FxPairDebateRequest,
    adapt_fx_pair_debate_request,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--xlsx", required=True)
    parser.add_argument(
        "--symbol",
        default="EURUSD",
        help="货币对，例如 EURUSD、GBP/USD、USDJPY；默认 EURUSD",
    )
    parser.add_argument("--as-of", required=True)
    args = parser.parse_args()
    as_of = datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
    if as_of.tzinfo is None:
        parser.error("--as-of must include a timezone")
    request = adapt_fx_pair_debate_request(
        FxPairDebateRequest(
            target=args.symbol,
            timeframe="2 weeks; 4H/1D",
            goal="verify synthetic FX evidence source",
        )
    ).resolved_request
    context = build_evidence_context(request, RunOptions(as_of=as_of))
    bundle = FxEvidenceFactory().build(context, ExcelFxEvidenceSource(args.xlsx))
    print(
        json.dumps(
            {
                "source": bundle.source_name,
                "as_of": bundle.as_of.isoformat(),
                "manifest": bundle.manifest.model_dump(mode="json"),
                "technical": {
                    key: {
                        "state": value.state,
                        "bar_count": value.bar_count,
                    }
                    for key, value in bundle.technical_regime.timeframes.items()
                },
                "macro_signals": len(bundle.relative_macro_scorecard.signals),
                "story_clusters": len(bundle.story_clusters),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
