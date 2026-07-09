# SPDX-License-Identifier: Apache-2.0
"""Real LLM juror for the Tier-1 (semantic) arbitration path.

Tier-0 stays deterministic; only genuinely *semantic* acceptance criteria — the
ones a machine cannot check — reach Tier-1. In production we resolve those with a
real, low-temperature LLM instead of the deterministic mock. Selection is by env:

    LEX_JUROR=openai            enable the real juror (else the deterministic mock)
    LEX_JUROR_MODEL=gpt-4o-mini the model (a cheap default)
    OPENAI_API_KEY=...          the key

The juror is a drop-in ``JurorFn`` (``(contract, criterion) -> vote``). It is
deliberately conservative: any error, refusal, or unparseable reply resolves to
``refund`` (favor the paying party), matching the mock's tie-breaking rule, so a
model outage can never wrongly pay a seller.

Example::

    juror = make_openai_juror()
    vote = juror(contract, {"path": "summary", "semantic": True,
                            "description": "the summary is faithful to the source"})
"""

from __future__ import annotations

import json
import os
from typing import Any

_SYSTEM = (
    "You are a neutral, strict arbitration juror for autonomous agent-to-agent "
    "commerce. Given a contract's semantic acceptance criterion and the work a "
    "seller delivered, decide whether the delivery satisfies the criterion. "
    'Respond ONLY as compact JSON: {"verdict":"release"|"refund","rationale":'
    '"<=15 words"}. "release" means the deliverable satisfies the criterion (pay '
    'the seller). "refund" means it does not (refund the buyer). When genuinely '
    'unsure, choose "refund".'
)


def _criterion_text(criterion: dict[str, Any]) -> str:
    """Render a criterion as a human sentence for the model.

    Example::

        _criterion_text({"description": "summary is faithful"})
    """
    if criterion.get("description"):
        return str(criterion["description"])
    path = criterion.get("path", "the deliverable")
    op = criterion.get("op", "satisfies")
    val = criterion.get("value")
    return f"'{path}' {op}" + (f" {val!r}" if val is not None else "")


def make_openai_juror(model: str | None = None, temperature: float = 0.0) -> Any:
    """Build a JurorFn backed by a real OpenAI model.

    Raises ``RuntimeError`` if the ``openai`` package or key is unavailable, so
    the caller can fall back to the deterministic mock.

    Example::

        juror = make_openai_juror(model="gpt-4o-mini")
    """
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY not set")
    try:
        import truststore  # noqa: PLC0415 - optional; fixes TLS behind MITM proxies

        truststore.inject_into_ssl()
    except Exception:
        pass
    from openai import OpenAI  # noqa: PLC0415 - optional dependency

    model = model or os.environ.get("LEX_JUROR_MODEL", "gpt-4o-mini")
    client = OpenAI()

    def juror(contract: Any, criterion: dict[str, Any]) -> dict[str, Any]:
        """Ask the model to judge one semantic criterion; default to refund on error."""
        user = (
            f"Contract price: {contract.price} {contract.currency}.\n"
            f"Semantic acceptance criterion: {_criterion_text(criterion)}\n"
            f"Delivered work (JSON): {json.dumps(contract.deliverable)[:4000]}\n\n"
            "Does the delivered work satisfy the criterion?"
        )
        try:
            resp = client.chat.completions.create(
                model=model,
                temperature=temperature,
                max_tokens=120,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": user},
                ],
            )
            data = json.loads(resp.choices[0].message.content or "{}")
            verdict = "release" if str(data.get("verdict", "")).lower() == "release" else "refund"
            return {
                "verdict": verdict,
                "rationale": str(data.get("rationale", ""))[:200],
                "confidence": 0.75,
                "model": model,
            }
        except Exception as exc:  # noqa: BLE001 - never crash adjudication
            return {
                "verdict": "refund",
                "rationale": f"juror error ({type(exc).__name__}); defaulted to refund",
                "confidence": 0.0,
                "model": model,
            }

    return juror


def select_juror(default: Any) -> Any:
    """Return the real OpenAI juror when enabled + available, else *default*.

    Example::

        court = Court(signing_key=key, juror=select_juror(deterministic_mock_juror))
    """
    if os.environ.get("LEX_JUROR", "").lower() == "openai":
        try:
            return make_openai_juror()
        except Exception:
            return default
    return default
