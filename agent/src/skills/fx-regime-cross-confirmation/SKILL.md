---
name: fx-regime-cross-confirmation
description: Explain the relationship between deterministic 1D/4H FX technical regimes and the relative macro state.
category: analysis
---
# FX Regime Cross-Confirmation

Use the technical regime exactly as returned by the Tool. Indicators are evidence derived from one bar family, not independent votes.

- 1D describes the primary regime; 4H describes tactical confirmation.
- `aligned_up/down`: macro and technical states agree.
- `macro_leads`: macro is directional while price is range, transition, or indeterminate.
- `price_leads`: price is directional while macro is balanced or indeterminate.
- `diverging`: directional macro and technical states disagree.
- `indeterminate`: missing 4H history, abnormal quote, or insufficient inputs prevent classification.

Do not calculate new indicators, infer volume participation for spot FX, or output a final trade action.

Explain 1D and 4H separately before classifying their relationship with macro. A missing or indeterminate timeframe is a limitation, not a neutral vote. State whether the observed price regime confirms, leads, lags, or diverges from the macro background, and explain what new observation would change that classification.
