# SPDX-License-Identifier: Apache-2.0
"""HTTP-level tests for the Lex Automata FastAPI app.

Runs in any environment with FastAPI installed (`pip install -r requirements.txt`)
via Starlette's TestClient — no network or running server needed. Skipped
automatically where FastAPI is unavailable.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from lex_automata.app import app  # noqa: E402

client = TestClient(app)

_counter = 0


def _ids() -> tuple[str, str]:
    """Return a fresh (buyer, seller) pair so reputation never leaks across tests."""
    global _counter
    _counter += 1
    return f"did:nanda:b{_counter}", f"did:nanda:s{_counter}"


def _new_contract(price: int, acceptance: dict, buyer: str = "", seller: str = "") -> str:
    if not buyer or not seller:
        buyer, seller = _ids()
    r = client.post(
        "/contracts",
        json={"buyer": buyer, "seller": seller, "price": price, "acceptance": acceptance},
    )
    assert r.status_code == 200, r.text
    return r.json()["contract_id"]


def test_health() -> None:
    # /health is canonical (Google's frontend reserves /healthz on run.app);
    # /healthz stays as an alias for hosts that don't reserve it.
    for path in ("/health", "/healthz"):
        r = client.get(path)
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


def test_root_serves_skill_spec() -> None:
    """The service root is self-documenting: GET / returns the SKILL.md text."""
    r = client.get("/")
    assert r.status_code == 200
    assert "Lex Automata" in r.text
    # An agent landing here must find the endpoint map without any other doc.
    assert "/contracts" in r.text


def test_skill_md_alias() -> None:
    """/skill.md serves the same document as / (the hackathon-suggested path)."""
    r = client.get("/skill.md")
    assert r.status_code == 200
    assert r.text == client.get("/").text


def test_get_on_post_endpoints_returns_usage_hint() -> None:
    """Bare GETs on the POST endpoints must 200 with pointers, never 405.

    The skills-registry reachability probe (and any curious agent) issues plain
    GETs against every listed endpoint URL; a 405 shows up as 'unreachable'.
    """
    r = client.get("/contracts")
    assert r.status_code == 200
    assert r.json()["how_to_create"]["method"] == "POST"
    r = client.get("/verify")
    assert r.status_code == 200
    assert r.json()["how_to_verify"]["method"] == "POST"


def test_happy_path_accept() -> None:
    cid = _new_contract(50, {"assertions": [{"path": "rows", "op": "gte", "value": 3}]})
    assert client.post(f"/contracts/{cid}/fund").json()["status"] == "funded"
    client.post(f"/contracts/{cid}/deliver", json={"deliverable": {"rows": 5}, "evidence": {}})
    receipt = client.post(f"/contracts/{cid}/accept").json()
    assert receipt["credentialSubject"]["verdict"] == "release"
    assert client.post("/verify", json={"receipt": receipt}).json()["valid"] is True


def test_verify_accepts_bare_receipt_and_rejects_garbage() -> None:
    """Agent-generated clients often POST the receipt unwrapped; both shapes work."""
    cid = _new_contract(10, {"assertions": [{"path": "ok", "op": "eq", "value": True}]})
    client.post(f"/contracts/{cid}/fund")
    client.post(f"/contracts/{cid}/deliver", json={"deliverable": {"ok": True}, "evidence": {}})
    receipt = client.post(f"/contracts/{cid}/accept").json()
    assert client.post("/verify", json=receipt).json()["valid"] is True
    r = client.post("/verify", json={"something": "else"})
    assert r.status_code == 422


def test_dispute_tier0_refund() -> None:
    cid = _new_contract(40, {"assertions": [{"path": "rows", "op": "gte", "value": 100}]})
    client.post(f"/contracts/{cid}/fund")
    client.post(f"/contracts/{cid}/deliver", json={"deliverable": {"rows": 2}, "evidence": {}})
    receipt = client.post(f"/contracts/{cid}/dispute", json={"reason": "short"}).json()
    assert receipt["credentialSubject"]["verdict"] == "refund"
    assert receipt["credentialSubject"]["tier"] == "tier0"


def test_semantic_goes_to_jury() -> None:
    cid = _new_contract(
        20, {"assertions": [{"path": "summary", "op": "nonempty", "semantic": True}]}
    )
    client.post(f"/contracts/{cid}/fund")
    client.post(
        f"/contracts/{cid}/deliver", json={"deliverable": {"summary": "hi"}, "evidence": {}}
    )
    receipt = client.post(f"/contracts/{cid}/dispute", json={"reason": "quality"}).json()
    assert receipt["credentialSubject"]["tier"] == "tier1-jury"


def test_out_of_order_returns_409() -> None:
    cid = _new_contract(10, {})
    # deliver before funding
    r = client.post(f"/contracts/{cid}/deliver", json={"deliverable": {}, "evidence": {}})
    assert r.status_code == 409


def test_unknown_contract_404() -> None:
    assert client.get("/contracts/does-not-exist").status_code == 404


def test_agent_receipt_history() -> None:
    buyer, seller = _ids()
    cid = _new_contract(
        10, {"assertions": [{"path": "ok", "op": "eq", "value": True}]}, buyer=buyer, seller=seller
    )
    client.post(f"/contracts/{cid}/fund")
    client.post(f"/contracts/{cid}/deliver", json={"deliverable": {"ok": True}, "evidence": {}})
    client.post(f"/contracts/{cid}/accept")
    r = client.get(f"/agents/{seller}/receipts")
    assert r.json()["count"] >= 1


def test_reputation_bans_repeat_loser_over_http() -> None:
    buyer, seller = _ids()
    # Seller loses three deterministic disputes -> BANNED.
    for _ in range(3):
        cid = _new_contract(
            10, {"assertions": [{"path": "rows", "op": "gte", "value": 100}]},
            buyer=buyer, seller=seller,
        )
        client.post(f"/contracts/{cid}/fund")
        client.post(f"/contracts/{cid}/deliver", json={"deliverable": {"rows": 1}, "evidence": {}})
        client.post(f"/contracts/{cid}/dispute", json={"reason": "short"})
    rep = client.get(f"/agents/{seller}/reputation").json()
    assert rep["standing"] == "BANNED", rep
    # A banned seller is refused new work (HTTP 400).
    r = client.post(
        "/contracts",
        json={"buyer": buyer, "seller": seller, "price": 10, "acceptance": {}},
    )
    assert r.status_code == 400
    assert "BANNED" in r.json()["detail"]
