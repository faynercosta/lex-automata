# SPDX-License-Identifier: Apache-2.0
"""Tests for the deterministic Beta-reputation layer."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lex_automata.core import Court, CourtError, SigningKey  # noqa: E402
from lex_automata.reputation import RoleStats, compute_reputation  # noqa: E402


def _court(enforce: bool = True) -> Court:
    return Court(
        signing_key=SigningKey.generate(seed=b"rep-seed"), clock=lambda: 5,
        enforce_reputation=enforce,
    )


def _resolved(court: Court, seller: str, deliver: dict, acceptance: dict, dispute: bool) -> None:
    c = court.create_contract("buyer", seller, 10, "credits", 100, acceptance)
    court.fund(c.contract_id)
    court.deliver(c.contract_id, deliver, {})
    if dispute:
        court.dispute(c.contract_id)
    else:
        court.accept(c.contract_id)


# --- Beta math -------------------------------------------------------------


def test_no_history_is_neutral_low_confidence() -> None:
    rs = RoleStats(0, 0)
    assert rs.score == 0.5
    assert rs.confidence == 0.0
    assert rs.standing() == "NEW"


def test_beta_score_matches_laplace_rule() -> None:
    # 4 positive, 1 negative -> (4+1)/(4+1+2) = 5/7
    rs = RoleStats(4, 1)
    assert abs(rs.score - 5 / 7) < 1e-9
    assert rs.sample_count == 5
    assert rs.standing() == "GOOD"  # 0.714 >= 0.70


def test_confidence_grows_with_evidence() -> None:
    assert RoleStats(1, 0).confidence < RoleStats(10, 0).confidence


def test_standing_ladder() -> None:
    assert RoleStats(10, 0).standing() == "GOOD"
    assert RoleStats(2, 2).standing() == "WATCH"      # score 0.5
    assert RoleStats(0, 5).standing() == "BANNED"     # score ~0.14, 5 losses
    assert RoleStats(0, 1).standing() == "WATCH"      # one loss must NOT ban


def test_one_unlucky_loss_does_not_ban() -> None:
    # Cold-start protection: a single lost dispute keeps you in WATCH, not BANNED.
    assert RoleStats(0, 1).standing() != "BANNED"
    assert RoleStats(1, 2).standing() != "BANNED"


# --- End-to-end reputation from receipts -----------------------------------


def test_completed_deals_build_seller_reputation() -> None:
    court = _court()
    for _ in range(3):
        _resolved(court, "seller", {"ok": True},
                  {"assertions": [{"path": "ok", "op": "eq", "value": True}]}, dispute=False)
    rep = court.reputation_of("seller")
    assert rep["as_seller"]["positive"] == 3
    assert rep["as_seller"]["standing"] == "GOOD"


def test_lost_disputes_ban_seller_and_block_new_contracts() -> None:
    court = _court(enforce=True)
    # Seller loses 3 deterministic disputes (delivers junk vs a strict rule).
    for _ in range(3):
        _resolved(court, "bad-seller", {"rows": 1},
                  {"assertions": [{"path": "rows", "op": "gte", "value": 100}]}, dispute=True)
    rep = court.reputation_of("bad-seller")
    assert rep["standing"] == "BANNED", rep
    # A banned seller can no longer be hired.
    try:
        court.create_contract("buyer", "bad-seller", 10, "credits", 100, {})
        raise AssertionError("expected the banned seller to be refused")
    except CourtError as exc:
        assert "BANNED" in str(exc)


def test_bad_mouthing_resistance() -> None:
    # A buyer cannot lower a seller's score by disputing good work: the seller
    # WINS the deterministic dispute, so it counts as a positive for the seller
    # and a negative for the frivolous buyer.
    court = _court()
    _resolved(court, "honest-seller", {"rows": 500},
              {"assertions": [{"path": "rows", "op": "gte", "value": 100}]}, dispute=True)
    seller_rep = court.reputation_of("honest-seller")
    buyer_rep = court.reputation_of("buyer")
    assert seller_rep["as_seller"]["negative"] == 0
    assert seller_rep["as_seller"]["positive"] == 1
    assert buyer_rep["as_buyer"]["negative"] == 1  # the frivolous disputer pays


def test_ballot_stuffing_requires_real_completed_deals() -> None:
    # There is no way to earn a positive without a *resolved* contract. An
    # unfunded/undelivered contract contributes nothing to reputation.
    court = _court()
    court.create_contract("buyer", "seller", 10, "credits", 100, {})  # created only
    rep = court.reputation_of("seller")
    assert rep["as_seller"]["sample_count"] == 0
    assert rep["standing"] == "NEW"


def test_reputation_is_deterministic() -> None:
    def run() -> dict:
        court = _court()
        for _ in range(2):
            _resolved(court, "s", {"ok": True},
                      {"assertions": [{"path": "ok", "op": "eq", "value": True}]}, dispute=False)
        _resolved(court, "s", {"ok": False},
                  {"assertions": [{"path": "ok", "op": "eq", "value": True}]}, dispute=True)
        return court.reputation_of("s")

    assert run() == run()


def test_compute_reputation_pure_function() -> None:
    # The pure function agrees with the court's view.
    court = _court()
    _resolved(court, "s", {"ok": True},
              {"assertions": [{"path": "ok", "op": "eq", "value": True}]}, dispute=False)
    receipts = court.receipts_for("s")
    assert compute_reputation(receipts, "s") == court.reputation_of("s")
