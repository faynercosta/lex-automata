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
    GET  /activity                      the court's own recent activity log
    GET  /healthz                       liveness

Run locally::

    pip install -r requirements.txt
    uvicorn lex_automata.app:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import os
import time
from collections import deque
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from lex_automata.core import (
    Court,
    CourtError,
    SigningKey,
    deterministic_mock_juror,
    verify_receipt,
)
from lex_automata.llm_juror import select_juror

app = FastAPI(
    title="Lex Automata",
    version="1.0.0",
    description="Escrow, deterministic-first arbitration, and verifiable verdict "
    "receipts for the Internet of AI Agents (NANDA).",
)

# The API is public and unauthenticated by design, so browser-based agents and
# visualizers (e.g. the Court of NANDA arena) can call it cross-origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# One court instance per process. A fixed seed (LEX_COURT_SEED) keeps the
# court's public key — and therefore its receipts' verifiability — stable
# across restarts and across the two redundant deployments.
_SEED = os.environ.get("LEX_COURT_SEED", "lex-automata-demo-court-seed").encode()
# Tier-0 stays deterministic; the Tier-1 (semantic) jury uses a real LLM when
# LEX_JUROR=openai + OPENAI_API_KEY are set, else the deterministic mock.
_court = Court(
    signing_key=SigningKey.generate(seed=_SEED),
    juror=select_juror(deterministic_mock_juror),
)


# ------------------------- server-side activity log ------------------------
# A bounded, in-memory record of what the court itself did, exposed at
# GET /activity so an operator (or the arena's "Court log" tab) can watch the
# court's own view of events — distinct from any single client's log. Purely
# observational: it never affects adjudication, receipts, or reputation.
_ACTIVITY: deque[dict[str, Any]] = deque(maxlen=250)
_ACTIVITY_SEQ = 0


def _record(event: str, **fields: Any) -> None:
    """Append one server-side activity record (most-recent-last).

    Example::

        _record("create", contract_id="lex-abc", price=50)
    """
    global _ACTIVITY_SEQ
    _ACTIVITY_SEQ += 1
    _ACTIVITY.append({"seq": _ACTIVITY_SEQ, "ts": round(time.time(), 3), "event": event, **fields})


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


# ------------------------------ endpoints ----------------------------------


# The agent-facing skill spec ships inside the image (see Dockerfile), so the
# service root is self-documenting: any agent that lands on the base URL gets
# the complete instructions for using the court.
_SKILL_PATH = Path(__file__).resolve().parent.parent / "SKILL.md"


@app.get("/", response_class=PlainTextResponse)
@app.get("/skill.md", response_class=PlainTextResponse)
def root() -> str:
    """Serve the agent-facing SKILL.md at the service root (and at /skill.md).

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


SKILL_RAW_URL = "https://raw.githubusercontent.com/faynercosta/lex-automata/main/SKILL.md"


@app.get("/agentfacts")
@app.get("/.well-known/agent-facts")
def agentfacts() -> dict[str, Any]:
    """Machine-readable capability metadata (NANDA AgentFacts style).

    Lets a discovering agent learn what this service does, how to call it, and
    how its outputs are certified — without parsing prose. The SKILL.md remains
    the canonical how-to; this is the structured summary for registries and
    routers.

    Example::

        GET /agentfacts -> {"agent_name": "lex-automata", "capabilities": ...}
    """
    base = "https://lex-automata-999015027200.us-central1.run.app"
    return {
        "@context": "https://projectnanda.org/agentfacts/v1",
        "id": "did:web:lex-automata-999015027200.us-central1.run.app",
        "agent_name": "lex-automata",
        "label": "Lex Automata — escrow + arbitration court for agent-to-agent commerce",
        "description": (
            "Holds an agent-to-agent payment in escrow against machine-checkable "
            "acceptance criteria; on dispute, deterministically replays the "
            "criteria (LLM jury for semantic criteria only) and issues a signed, "
            "offline-verifiable verdict receipt deciding who gets paid. "
            "Receipt-derived reputation; repeat losers are banned from new "
            "contracts. No signup, no API key."
        ),
        "version": app.version,
        "documentation": [f"{base}/skill.md", SKILL_RAW_URL],
        "provider": {"name": "Fayner Costa", "github": "faynercosta"},
        "endpoints": {"static": [base]},
        "capabilities": {
            "protocols": ["https+json"],
            "authentication": "none",
            "functions": [
                "escrow",
                "contract-of-record",
                "deterministic-arbitration",
                "semantic-arbitration-llm-jury",
                "signed-verdict-receipts",
                "offline-receipt-verification",
                "receipt-derived-reputation",
                "ban-enforcement",
            ],
        },
        "certification": {
            "receipt_signature_suite": "Ed25519Signature2020",
            "court_public_key": _court._key.public_b64,
            "verify_endpoint": f"{base}/verify",
        },
    }


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
    juror = "openai" if (
        os.environ.get("LEX_JUROR", "").lower() == "openai" and os.environ.get("OPENAI_API_KEY")
    ) else "deterministic-mock"
    return {
        "status": "ok",
        "court_public_key": _court._key.public_b64,
        "tier1_juror": juror,
        "tier1_juror_model": os.environ.get("LEX_JUROR_MODEL", "gpt-4o-mini") if juror == "openai" else None,
    }


@app.get("/contracts")
def contracts_index() -> dict[str, Any]:
    """Describe the collection endpoint (creation itself is via POST).

    Exists so that a bare ``GET /contracts`` (a curious agent, a link checker,
    the skills-registry reachability probe) gets a useful 200 instead of a 405.

    Example::

        GET /contracts -> {"contracts_created": 12, "how_to_create": {...}}
    """
    return {
        "service": "lex-automata",
        "contracts_created": len(_court._contracts),
        "how_to_create": {
            "method": "POST",
            "path": "/contracts",
            "example_body": {
                "buyer": "did:nanda:buyer",
                "seller": "did:nanda:seller",
                "price": 50,
                "acceptance": {"assertions": [{"path": "rows", "op": "gte", "value": 3}]},
            },
        },
        "docs": "GET / returns the full SKILL.md",
    }


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
        _record("refused", buyer=body.buyer, seller=body.seller, detail=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _record(
        "create",
        contract_id=c.contract_id,
        buyer=body.buyer,
        seller=body.seller,
        price=body.price,
        currency=body.currency,
    )
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
    res = _guard(lambda: _court.fund(contract_id), lambda c: {"status": c.status.value})
    _record("fund", contract_id=contract_id, status=res["status"])
    return res


@app.post("/contracts/{contract_id}/deliver")
def deliver(contract_id: str, body: Deliver) -> dict[str, Any]:
    """Submit the deliverable and commit its evidence hash.

    Example::

        POST /contracts/lex-abc/deliver {"deliverable":{"rows":5},"evidence":{}}
    """
    res = _guard(
        lambda: _court.deliver(contract_id, body.deliverable, body.evidence),
        lambda c: {"status": c.status.value, "evidence_hash": c.evidence_hash},
    )
    _record("deliver", contract_id=contract_id, evidence_hash=res["evidence_hash"])
    return res


@app.post("/contracts/{contract_id}/accept")
def accept(contract_id: str) -> dict[str, Any]:
    """Accept delivery; escrow releases to the seller. Returns the receipt.

    Example::

        POST /contracts/lex-abc/accept
    """
    try:
        receipt = _court.accept(contract_id)
    except CourtError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _record_verdict("accept", contract_id, receipt)
    return receipt


@app.post("/contracts/{contract_id}/dispute")
def dispute(contract_id: str, body: Dispute) -> dict[str, Any]:
    """Dispute delivery; run tiered adjudication and return the signed receipt.

    Example::

        POST /contracts/lex-abc/dispute {"reason":"rows look short"}
    """
    try:
        receipt = _court.dispute(contract_id, reason=body.reason)
    except CourtError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _record_verdict("dispute", contract_id, receipt)
    return receipt


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


@app.get("/verify")
def verify_index() -> dict[str, Any]:
    """Describe the verifier endpoint (verification itself is via POST).

    Exists so that a bare ``GET /verify`` (a curious agent, a link checker,
    the skills-registry reachability probe) gets a useful 200 instead of a 405.

    Example::

        GET /verify -> {"how_to_verify": {...}}
    """
    return {
        "service": "lex-automata",
        "how_to_verify": {
            "method": "POST",
            "path": "/verify",
            "example_body": {"receipt": {"credentialSubject": "...", "proof": "..."}},
            "returns": {"valid": True},
        },
        "docs": "GET / returns the full SKILL.md",
    }


@app.post("/verify")
def verify(body: dict[str, Any]) -> dict[str, Any]:
    """Statelessly verify a verdict receipt's signature and hash binding.

    Accepts the documented wrapper ``{"receipt": {...}}`` and, for robustness
    with agent-generated clients, a bare receipt object posted directly (any
    JSON body carrying ``credentialSubject`` + ``proof``).

    Example::

        POST /verify {"receipt": {...}}
    """
    receipt = body.get("receipt") if isinstance(body.get("receipt"), dict) else body
    if not isinstance(receipt, dict) or "proof" not in receipt:
        raise HTTPException(
            status_code=422,
            detail='Body must be {"receipt": {...}} or a bare receipt object '
            "(the signed verdict JSON returned by accept/dispute).",
        )
    return {"valid": verify_receipt(receipt)}


@app.get("/activity")
def activity(limit: int = 50) -> dict[str, Any]:
    """Return the court's own recent activity log, most-recent first.

    This is the server's view of what happened — contract creations, funding,
    deliveries, and rendered verdicts (with tier and payout) — as opposed to any
    single client's log. Observational only; it never affects adjudication.

    Example::

        GET /activity?limit=20
        -> {"count": 137, "events": [{"seq": 137, "event": "dispute",
             "verdict": "refund", "tier": "tier0", ...}, ...]}
    """
    limit = max(1, min(limit, 250))
    events = list(_ACTIVITY)[-limit:][::-1]
    return {
        "count": len(_ACTIVITY),
        "total_seq": _ACTIVITY_SEQ,
        "events": events,
        "court_public_key": _court._key.public_b64,
    }


# ------------------------------ helpers ------------------------------------


def _record_verdict(event: str, contract_id: str, receipt: dict[str, Any]) -> None:
    """Log a rendered verdict (verdict, tier, payout) to the activity feed.

    Example::

        _record_verdict("dispute", "lex-abc", receipt)
    """
    cs = receipt.get("credentialSubject", {})
    _record(
        event,
        contract_id=contract_id,
        verdict=cs.get("verdict"),
        tier=cs.get("tier"),
        payout=cs.get("payout"),
        receipt_id=receipt.get("id"),
    )


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
