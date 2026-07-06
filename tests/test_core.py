# SPDX-License-Identifier: Apache-2.0
"""Tests for the Lex Automata core (stdlib + cryptography only)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lex_automata.core import (  # noqa: E402
    Court,
    SigningKey,
    Status,
    adjudicate_tier0,
    verify_receipt,
)


def _court() -> Court:
    return Court(signing_key=SigningKey.generate(seed=b"court-seed-abc"), clock=lambda: 7)


def test_happy_path_accept_releases_to_seller() -> None:
    court = _court()
    c = court.create_contract(
        "buyer", "seller", 50, "credits", 1000,
        {"schema": {"type": "object", "required": ["rows"]},
         "assertions": [{"path": "rows", "op": "gte", "value": 3}]},
    )
    court.fund(c.contract_id)
    court.deliver(c.contract_id, {"rows": 5}, {"log": "ok"})
    receipt = court.accept(c.contract_id)
    assert receipt["credentialSubject"]["verdict"] == "release"
    assert receipt["credentialSubject"]["payout"] == {"seller": 50}
    assert court.get_contract(c.contract_id).status == Status.RESOLVED


def test_tier0_refund_on_failed_assertion() -> None:
    court = _court()
    c = court.create_contract(
        "buyer", "seller", 40, "credits", 1000,
        {"assertions": [{"path": "rows", "op": "gte", "value": 10}]},
    )
    court.fund(c.contract_id)
    court.deliver(c.contract_id, {"rows": 2}, {})
    receipt = court.dispute(c.contract_id, reason="too few rows")
    assert receipt["credentialSubject"]["verdict"] == "refund"
    assert receipt["credentialSubject"]["tier"] == "tier0"
    assert receipt["credentialSubject"]["payout"] == {"buyer": 40}


def test_tier0_release_when_all_checks_pass() -> None:
    court = _court()
    c = court.create_contract(
        "buyer", "seller", 30, "credits", 1000,
        {"schema": {"type": "object", "required": ["url"],
                    "properties": {"url": {"type": "string"}}},
         "assertions": [{"path": "url", "op": "regex", "value": "^https://"}]},
    )
    court.fund(c.contract_id)
    court.deliver(c.contract_id, {"url": "https://example.com/data"}, {})
    receipt = court.dispute(c.contract_id)
    assert receipt["credentialSubject"]["verdict"] == "release"
    assert receipt["credentialSubject"]["tier"] == "tier0"


def test_tier0_is_deterministic_across_runs() -> None:
    def run() -> str:
        court = _court()
        c = court.create_contract(
            "b", "s", 10, "credits", 100,
            {"assertions": [{"path": "n", "op": "eq", "value": 42}]},
        )
        court.fund(c.contract_id)
        court.deliver(c.contract_id, {"n": 42}, {})
        r = adjudicate_tier0(court.get_contract(c.contract_id))
        return r.verdict or ""

    assert run() == run() == "release"


def test_semantic_criterion_falls_through_to_jury() -> None:
    court = _court()
    c = court.create_contract(
        "buyer", "seller", 20, "credits", 1000,
        {"assertions": [
            {"path": "summary", "op": "nonempty", "semantic": True},
        ]},
    )
    court.fund(c.contract_id)
    court.deliver(c.contract_id, {"summary": "a faithful summary"}, {})
    receipt = court.dispute(c.contract_id)
    assert receipt["credentialSubject"]["tier"] == "tier1-jury"
    assert receipt["credentialSubject"]["jury"]["total_votes"] == 3
    assert receipt["credentialSubject"]["verdict"] in ("release", "refund")


def test_mixed_deterministic_fail_short_circuits_before_jury() -> None:
    court = _court()
    c = court.create_contract(
        "buyer", "seller", 20, "credits", 1000,
        {"assertions": [
            {"path": "rows", "op": "gte", "value": 100},
            {"path": "summary", "op": "nonempty", "semantic": True},
        ]},
    )
    court.fund(c.contract_id)
    court.deliver(c.contract_id, {"rows": 1, "summary": "x"}, {})
    receipt = court.dispute(c.contract_id)
    assert receipt["credentialSubject"]["tier"] == "tier0"
    assert receipt["credentialSubject"]["verdict"] == "refund"


def test_receipt_is_signed_and_verifies() -> None:
    court = _court()
    c = court.create_contract("b", "s", 10, "credits", 100,
                              {"assertions": [{"path": "ok", "op": "eq", "value": True}]})
    court.fund(c.contract_id)
    court.deliver(c.contract_id, {"ok": True}, {})
    receipt = court.dispute(c.contract_id)
    assert verify_receipt(receipt)
    assert court.verify_receipt(receipt)


def test_tampered_receipt_fails_verification() -> None:
    court = _court()
    c = court.create_contract("b", "s", 10, "credits", 100,
                              {"assertions": [{"path": "ok", "op": "eq", "value": True}]})
    court.fund(c.contract_id)
    court.deliver(c.contract_id, {"ok": True}, {})
    receipt = court.dispute(c.contract_id)
    receipt["credentialSubject"]["verdict"] = "refund"  # tamper
    assert not verify_receipt(receipt)


def test_evidence_hash_committed_at_delivery() -> None:
    court = _court()
    c = court.create_contract("b", "s", 10, "credits", 100, {})
    court.fund(c.contract_id)
    court.deliver(c.contract_id, {"x": 1}, {"proof": "abc"})
    h1 = court.get_contract(c.contract_id).evidence_hash
    assert h1 is not None and len(h1) == 64


def test_receipts_for_agent_history() -> None:
    court = _court()
    for _ in range(3):
        c = court.create_contract("buyer", "seller", 10, "credits", 100,
                                  {"assertions": [{"path": "ok", "op": "eq", "value": True}]})
        court.fund(c.contract_id)
        court.deliver(c.contract_id, {"ok": True}, {})
        court.dispute(c.contract_id)
    assert len(court.receipts_for("seller")) == 3
    assert len(court.receipts_for("buyer")) == 3


def test_bad_state_transitions_raise() -> None:
    court = _court()
    c = court.create_contract("b", "s", 10, "credits", 100, {})
    try:
        court.deliver(c.contract_id, {}, {})
        raise AssertionError("expected CourtError")
    except Exception as exc:
        assert "state" in str(exc)


def test_release_refund_payout_conserves_escrow() -> None:
    court = Court(signing_key=SigningKey.generate(seed=b"s2"), clock=lambda: 1)
    c = court.create_contract("b", "s", 11, "credits", 100, {})
    court.fund(c.contract_id)
    court.deliver(c.contract_id, {"x": 1}, {})
    receipt = court.accept(c.contract_id)
    payout = receipt["credentialSubject"]["payout"]
    assert sum(payout.values()) == 11
