---
name: fx-hypothesis-falsification
description: Build a symmetric, evidence-scoped and falsifiable FX up/down hypothesis without making a final trade decision.
category: analysis
---
# FX Hypothesis Falsification

Use the frozen Evidence Bundle only. Treat the assigned direction as a hypothesis to test, not a conclusion to defend.

1. Read the evidence manifest before forming a view. Use its status as an analysis router: `complete` permits 2–3 chains, `partial` permits only 1–2 fully traceable chains, and `insufficient_evidence` requires an empty chain list plus a minimum data-repair list. Missing 4H bars, abnormal quotes, or unavailable macro forecasts must reduce the hypothesis to `weak` or `insufficient`.
2. Select at most three independent causal chains. Each chain separates observed fact, inference, transmission mechanism, expected direction, and effective window. Mark factual prose with evidence IDs and label inference explicitly.
3. A supported hypothesis needs both a macro mechanism and market/technical confirmation from at least two evidence families.
4. State the strongest evidence against the assigned direction. Never count translations, article updates, or indicators derived from the same bars as independent confirmation.
5. Define invalidation using a measurable metric, operator, threshold, validity window, and evidence family.
6. Do not output entries, stops, targets, position sizes, probabilities, or a final trade action.

Use the research objective only to prioritize relevant dimensions and horizons. Never let a requested conclusion, desired level of detail, or narrative theme override evidence quality. Prefer a shorter closed causal chain over a longer chain with an unsupported link. Separate:

- **driver** — a relative macro mechanism capable of affecting the pair;
- **catalyst** — a dated event or story that may activate attention;
- **confirmation** — an observed price/regime response;
- **countercase** — the strongest registered fact against the assigned direction.

Before finalizing, audit every adopted evidence item for timestamp, quality status, unit, and family independence. News co-occurrence is not causation; a forecast-free release is not a surprise; several indicators from one bar set are one confirmation family.

Bull and Bear follow identical evidence, coverage, and quality rules. If the evidence cannot satisfy them, report the limitation directly.
