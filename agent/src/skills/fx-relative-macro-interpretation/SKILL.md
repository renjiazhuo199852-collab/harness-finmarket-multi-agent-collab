---
name: fx-relative-macro-interpretation
description: Interpret deterministic base-versus-quote macro signals for FX while respecting missing forecasts and conditional inflation effects.
category: analysis
---
# FX Relative Macro Interpretation

Interpret the requested canonical pair as base currency relative to quote currency. Use the supplied scorecard; do not recalculate or invent missing values.

- Rates and yields use the reported base-minus-quote comparison (the scorecard names both legs).
- Growth and labor use actual-versus-forecast only when forecast exists; unemployment surprise has inverse economic direction.
- Inflation surprise is conditional: it may support the currency through policy expectations or weaken it through stagflation/risk effects. Explain the mechanism instead of assigning an automatic sign.
- If both currency legs are not available, the dimension is indeterminate; do not substitute EU/US data for another pair.
- Match information to horizon: structural or slow data cannot provide a standalone 4H trigger.

Return a relative state and reliability, not a trade recommendation.

Adapt the explanation to data completeness. With complete bilateral inputs, compare all available dimensions. With partial inputs, retain supported dimension-level findings and mark the aggregate state indeterminate when the missing leg could change the sign. With insufficient evidence, report what exact base/quote series or forecast is needed and which conclusion it blocks.

For each finding use the sequence **observation → interpretation → horizon relevance** and cite the supporting evidence IDs. The user's goal may prioritize a dimension, but it cannot change a scorecard relationship or reliability grade.
