"""The registration owns brand, model and, when the sheet says so, the regime.

`aircrafts.extra` is the model code. `aircraft_models.extra` is the brand code.
`aircraft_sheets.extra` is JSON and may carry `regime`. A tail with no model link
does not invent one. A tail whose model is not an active catalog code is refused.
A chosen regime that is not the sheet's regime is refused. An empty regime is
filled from the sheet. RAV 135 is never assumed from certification text.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

MODEL_INACTIVE = "La matrícula no tiene un modelo activo."
REGIME_MISMATCH = "Esta matrícula no está habilitada para ese régimen."


def sheet_regime(extra: str | None) -> str:
    if not extra or not str(extra).strip():
        return ""
    try:
        raw = json.loads(extra)
    except json.JSONDecodeError:
        return ""
    if not isinstance(raw, dict):
        return ""
    regime = raw.get("regime")
    return regime.strip() if isinstance(regime, str) else ""


def align_aircraft_link(
    payload: dict,
    *,
    aircraft_key: str | None,
    brand_key: str | None,
    model_key: str | None,
    regime_key: str | None,
    fleet: Mapping[str, str | None],
    models: Mapping[str, str | None],
    sheets: Mapping[str, str | None],
) -> tuple[str, str] | None:
    """Write the tail's brand and model. Return `(field, message)` or None.

    The payload is changed only when the link is valid. A regime that does not
    match the sheet is refused and nothing else is written.
    """
    if not aircraft_key:
        return None
    aircraft = payload.get(aircraft_key)
    if not isinstance(aircraft, str) or not aircraft.strip() or aircraft not in fleet:
        return None
    model = (fleet.get(aircraft) or "").strip()
    if model and model not in models:
        return (aircraft_key, MODEL_INACTIVE)
    brand = (models.get(model) or "").strip() if model else ""
    wanted = sheet_regime(sheets.get(aircraft))
    current = payload.get(regime_key) if regime_key else None
    current_text = current.strip() if isinstance(current, str) else ""
    if regime_key and wanted and current_text and current_text != wanted:
        return (regime_key, REGIME_MISMATCH)
    if brand_key:
        if brand:
            payload[brand_key] = brand
        else:
            payload.pop(brand_key, None)
    if model_key:
        if model:
            payload[model_key] = model
        else:
            payload.pop(model_key, None)
    if regime_key and wanted and not current_text:
        payload[regime_key] = wanted
    return None
