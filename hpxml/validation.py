"""Optional local validation against NREL's ``hescore-hpxml`` translator.

The translator is the exact program the HEScore API runs server-side for
``submit_hpxml_inputs``.  Running it locally lets us confirm an HPXML document
will be accepted *before* spending an API round-trip — and is the acceptance
gate for the Phase-0 spike.  It is an optional dependency: if it is not
installed we report ``available=False`` instead of failing.

    pip install hescore-hpxml
"""
from __future__ import annotations

import io
from typing import Any, Dict


def translator_available() -> bool:
    try:
        import hescorehpxml  # noqa: F401
        return True
    except Exception:
        return False


def validate_with_translator(hpxml_str: str) -> Dict[str, Any]:
    """Translate HPXML -> HEScore inputs locally.

    Returns ``{available, ok, hescore_inputs?, error?}``.  A successful
    translation means the document satisfies the HEScore input requirements.
    """
    if not translator_available():
        return {
            "available": False,
            "ok": False,
            "error": "hescore-hpxml not installed (pip install hescore-hpxml)",
        }
    try:
        from hescorehpxml import HPXMLtoHEScoreTranslator

        translator = HPXMLtoHEScoreTranslator(io.BytesIO(hpxml_str.encode("utf-8")))
        hescore_inputs = translator.hpxml_to_hescore_dict()
        return {"available": True, "ok": True, "hescore_inputs": hescore_inputs}
    except Exception as exc:  # translator raises descriptive validation errors
        return {"available": True, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
