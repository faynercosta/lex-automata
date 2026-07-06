# SPDX-License-Identifier: Apache-2.0
"""Lex Automata HTTP API — a thin FastAPI wrapper over ``core.Court``.

Six endpoints, no signup wall, so a stock agent can drive the whole lifecycle
from the SKILL.md alone:

    POST /contracts                     create a contract of record
    POST /contracts/{id}/fund           fund escrow
    POST /contracts/{id}/deliver        submit deliverable + evidence
    POST /contracts/{id}/accept         release escrow to seller
    POST /contracts/{id}/dispute        run tiered adjudication, settle
    GET  /verdicts/{contract_id}        (also GET /contracts/{id})
    GET  /agents/{did}/receipts         portable verdict history
    POST /verify                        verify a verdict receipt (stateless)
    GET  /healthz                       liveness

Run locally::

    pip install -r requirements.txt
    uvicorn lex_automata.app:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from lex_automata.core import Court, CourtError, SigningKey, verify_receipt

app = FastAPI(
    title="Lex Automata",
    version="1.0.0",
    description="Escrow, deterministic-first arbitration, and verifiable verdict "
    "receipts for the Internet of AI Agents (NANDA).",
)

# One court instance per process. A fixed seed (LEX_COURT_SEED) keeps the
# court's public key — and therefore its receipts' verifiability — stable
# across restarts and across the two redundant deployments.
_SEED = os.environ.get("LEX_COURT_SEED", "lex-automata-demo-court-seed").encode()
_court = Court(signing_key=SigningKey.generate(seed=_SEED))


# --------------------------- request models --------------------------------


class CreateContract(BaseModel):
    """Body for ``POST /contracts``.

    Example::

        CreateContract(buyer="did:nanda:b", seller="did:nanda:s", price=50,
                       deadline_tick=1000, acceptance={})
    """

    buyer: str
    seller: str
    price: int = Field(ge=0)
    currency: str = "credits"
    deadline_tick: int = 1000
    acceptance: dict[str, Any] = Field(default_factory=dict)
    dispute_bond: int = 0


class Deliver(BaseModel):
    """Body for ``POST /contracts/{id}/deliver``.

    Example::

        Deliver(deliverable={"rows": 5}, evidence={"log": "..."})
    """

    deliverable: Any = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class Dispute(BaseModel):
    """Body for ``POST /contracts/{id}/dispute``.

    Example::

        Dispute(reason="rows look short")
    """

    reason: str = ""


class VerifyBody(BaseModel):
    """Body for ``POST /verify``.

    Example::

        VerifyBody(receipt={...})
    """

    receipt: dict[str, Any]


# ------------------------------ endpoints ----------------------------------


# The agent-facing skill spec ships inside the image (see Dockerfile), so the
# service root is self-documenting: any agent that lands on the base URL gets
# the complete instructions for using the court.
_SKILL_PATH = Path(__file__).resolve().parent.parent / "SKILL.md"


@app.get("/", response_class=PlainTextResponse)
def root() -> str:
    """Serve the agent-facing SKILL.md at the service root.

    Example::

        GET / -> the full SKILL.md (text/markdown-ish plain text)
    """
    if _SKILL_PATH.exists():
        return _SKILL_PATH.read_text(encoding="utf-8")
    return (
        "Lex Automata — escrow + deterministic-first arbitration for agent-to-agent "
        "commerce. See /healthz for liveness; full skill spec: SKILL.md in the "
        "source repository."
    )


@app.get("/health")
@app.get("/healthz")
def healthz() -> dict[str, Any]:
    """Liveness probe with the court's public key.

    Served at BOTH ``/health`` and ``/healthz``: Google's frontend reserves
    ``/healthz`` on run.app domains (it 404s before reaching the container),
    so ``/health`` is the canonical documented path; ``/healthz`` still works
    on hosts that don't reserve it.

    Example::

        GET /health -> {"status": "ok", "court_public_key": "..."}
    """
    return {"status": "ok", "court_public_key": _court._key.public_b64}


@app.post("/contracts")
def create_contract(body: CreateContract) -> dict[str, Any]:
    """Create a contract of record; returns the id and canonical hash.

    Example::

        POST /contracts {"buyer":"b","seller":"s","price":50,
                         "acceptance":{"assertions":[{"path":"rows","op":"gte","value":3}]}}
    """
    try:
        c = _court.create_contract(
            buyer=body.buyer,
            seller=body.seller,
            price=body.price,
            currency=body.currency,
            deadline_tick=body.deadline_tick,
            acceptance=body.acceptance,
            dispute_bond=body.dispute_bond,
        )
    except CourtError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "contract_id": c.contract_id,
        "contract_hash": c.contract_hash,
        "status": c.status.value,
    }


@app.post("/contracts/{contract_id}/fund")
def fund(contract_id: str) -> dict[str, Any]:
    """Fund escrow for a created contract.

    Example::

        POST /contracts/lex-abc/fund
    """
    return _guard(lambda: _court.fund(contract_id), lambda c: {"status": c.status.value})


@app.post("/contracts/{contract_id}/deliver")
def deliver(contract_id: str, body: Deliver) -> dict[str, Any]:
    """Submit the deliverable and commit its evidence hash.

    Example::

        POST /contracts/lex-abc/deliver {"deliverable":{"rows":5},"evidence":{}}
    """
    return _guard(
        lambda: _court.deliver(contract_id, body.deliverable, body.evidence),
        lambda c: {"status": c.status.value, "evidence_hash": c.evidence_hash},
    )


@app.post("/contracts/{contract_id}/accept")
def accept(contract_id: str) -> dict[str, Any]:
    """Accept delivery; escrow releases to the seller. Returns the receipt.

    Example::

        POST /contracts/lex-abc/accept
    """
    try:
        return _court.accept(contract_id)
    except CourtError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/contracts/{contract_id}/dispute")
def dispute(contract_id: str, body: Dispute) -> dict[str, Any]:
    """Dispute delivery; run tiered adjudication and return the signed receipt.

    Example::

        POST /contracts/lex-abc/dispute {"reason":"rows look short"}
    """
    try:
        return _court.dispute(contract_id, reason=body.reason)
    except CourtError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/contracts/{contract_id}")
def get_contract(contract_id: str) -> dict[str, Any]:
    """Fetch a contract's current state.

    Example::

        GET /contracts/lex-abc
    """
    try:
        c = _court.get_contract(contract_id)
    except CourtError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        **c.to_public(),
        "status": c.status.value,
        "contract_hash": c.contract_hash,
        "evidence_hash": c.evidence_hash,
    }


@app.get("/agents/{did}/receipts")
def agent_receipts(did: str) -> dict[str, Any]:
    """Return an agent's portable verdict history.

    Example::

        GET /agents/did:nanda:seller/receipts
    """
    receipts = _court.receipts_for(did)
    return {"agent": did, "count": len(receipts), "receipts": receipts}


@app.get("/agents/{did}/reputation")
def agent_reputation(did: str) -> dict[str, Any]:
    """Return an agent's deterministic, receipt-derived reputation and standing.

    Example::

        GET /agents/did:nanda:seller/reputation
        -> {"standing": "GOOD", "as_seller": {"score": 0.8, ...}, "as_buyer": {...}}
    """
    return _court.reputation_of(did)


@app.post("/verify")
def verify(body: VerifyBody) -> dict[str, Any]:
    """Statelessly verify a verdict receipt's signature and hash binding.

    Example::

        POST /verify {"receipt": {...}}
    """
    return {"valid": verify_receipt(body.receipt)}


# ------------------------------ helpers ------------------------------------


def _guard(action: Any, shape: Any) -> dict[str, Any]:
    """Run a court action, mapping ``CourtError`` to a 409 and shaping the result.

    Example::

        _guard(lambda: court.fund(cid), lambda c: {"status": c.status.value})
    """
    try:
        c = action()
    except CourtError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return shape(c)
