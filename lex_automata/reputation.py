# SPDX-License-Identifier: Apache-2.0
"""Deterministic reputation for Lex Automata, from verdict receipts alone.

Design choices, and the science behind them:

* **Beta Reputation System** (Jøsang & Ismail, 2002). Reputation is derived from
  counts of positive (``r``) and negative (``s``) outcomes via the expected
  value of a Beta(r+1, s+1) distribution — i.e. Laplace's rule of succession::

      score = (r + 1) / (r + s + 2)

  With no history the score is 0.5 (maximum uncertainty) and it converges toward
  the true rate as evidence accumulates. We also report a ``confidence`` that
  grows with the number of observations, and a ``sample_count`` — exactly the
  three fields NANDA's ``ReputationScore`` type carries
  (``score``, ``confidence``, ``sample_count``), so a Lex Automata reputation
  drops straight into AgentFacts / a Trust-layer plugin.

* **Evidence is adjudicated, never subjective.** Every outcome comes from a
  signed verdict receipt, not a star rating. This structurally defeats two
  classic reputation attacks (Hoffman et al., "A Survey of Attack and Defense
  Techniques for Reputation Systems"):
    - *Bad-mouthing*: a buyer cannot lower a seller's score by leaving a bad
      review — a negative only exists if the buyer **wins a deterministic
      dispute**. A frivolous dispute they lose counts against the *buyer*.
    - *Ballot-stuffing / self-promotion*: a positive only exists for a
      **completed, escrowed** contract, so faking reputation with a Sybil ring
      costs real locked capital and any dispute bonds — there is no free
      positive.

* **Symmetric.** Buyers who dispute and lose accumulate negatives too, so the
  signal disciplines both sides of the market.

* **Whitewashing is bounded, not solved.** Reputation binds to the agent's NANDA
  DID. A fresh DID starts at score 0.5 but ``confidence`` 0 — the *absence* of
  history is itself a signal a counterparty can price in (require history, or a
  larger bond). Fully defeating identity-churn needs a costly-identity layer
  (NANDA Index), which we rely on rather than reimplement.

Everything here is pure counting and fixed thresholds over receipts we already
store: fully deterministic and reproducible by anyone holding the public
receipts.

Example::

    from lex_automata.reputation import compute_reputation
    rep = compute_reputation(receipts, "did:nanda:seller")
    assert rep["as_seller"]["standing"] in ("NEW", "GOOD", "WATCH", "BANNED")
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Standing thresholds. Deliberately conservative so a single unlucky dispute
# cannot ban an agent (cold-start / one-off protection): a ban requires BOTH a
# low score AND at least MIN_LOSSES_TO_BAN adjudicated losses.
GOOD_SCORE = 0.70
WATCH_SCORE = 0.40
MIN_LOSSES_TO_BAN = 3
BAN_SCORE = 0.40


@dataclass
class RoleStats:
    """Positive/negative tally and Beta score for one role (buyer or seller).

    Example::

        rs = RoleStats(positive=4, negative=1)
        assert 0.0 <= rs.score <= 1.0
    """

    positive: int = 0
    negative: int = 0

    @property
    def sample_count(self) -> int:
        """Total adjudicated outcomes for this role.

        Example::

            n = RoleStats(3, 1).sample_count  # 4
        """
        return self.positive + self.negative

    @property
    def score(self) -> float:
        """Beta expected value ``(r+1)/(r+s+2)`` — Laplace rule of succession.

        Example::

            assert RoleStats(0, 0).score == 0.5
        """
        return (self.positive + 1) / (self.positive + self.negative + 2)

    @property
    def confidence(self) -> float:
        """Confidence in ``score``, rising with evidence: ``n/(n+2)``.

        Two pseudo-observations of prior (matching the Beta(1,1) prior) means a
        brand-new agent has confidence 0, and confidence approaches 1 as real
        outcomes accumulate.

        Example::

            assert RoleStats(0, 0).confidence == 0.0
        """
        n = self.sample_count
        return n / (n + 2)

    def standing(self) -> str:
        """Deterministic standing label for this role.

        ``NEW`` (no history) → ``GOOD`` → ``WATCH`` → ``BANNED``. A ban needs a
        low score *and* at least ``MIN_LOSSES_TO_BAN`` losses.

        Example::

            assert RoleStats(0, 0).standing() == "NEW"
        """
        if self.sample_count == 0:
            return "NEW"
        if self.negative >= MIN_LOSSES_TO_BAN and self.score < BAN_SCORE:
            return "BANNED"
        if self.score >= GOOD_SCORE:
            return "GOOD"
        if self.score >= WATCH_SCORE:
            return "WATCH"
        return "WATCH" if self.negative < MIN_LOSSES_TO_BAN else "BANNED"

    def to_dict(self) -> dict[str, Any]:
        """Serialize as a NANDA-``ReputationScore``-shaped record.

        Example::

            RoleStats(3, 1).to_dict()["sample_count"]  # 4
        """
        return {
            "score": round(self.score, 4),
            "confidence": round(self.confidence, 4),
            "sample_count": self.sample_count,
            "positive": self.positive,
            "negative": self.negative,
            "standing": self.standing(),
        }


def _classify(receipt: dict[str, Any]) -> tuple[str | None, str | None]:
    """Map one verdict receipt to (seller_outcome, buyer_outcome).

    Outcomes are ``"pos"``, ``"neg"``, or ``None`` (neutral / not counted):

    * buyer *accepted* (no dispute) → seller ``pos``, buyer neutral;
    * dispute, verdict ``release`` (seller won) → seller ``pos``, buyer ``neg``
      (the buyer raised a dispute they lost — frivolous);
    * dispute, verdict ``refund`` (buyer won) → seller ``neg``, buyer ``pos``
      (a legitimate dispute);
    * ``split`` → neutral for both (genuinely mixed outcome).

    Example::

        s, b = _classify({"credentialSubject": {"tier": "tier0", "verdict": "refund"}})
        assert (s, b) == ("neg", "pos")
    """
    subj = receipt.get("credentialSubject", {})
    verdict = subj.get("verdict")
    tier = subj.get("tier")
    if tier == "buyer-accept":
        return "pos", None
    if verdict == "release":
        return "pos", "neg"
    if verdict == "refund":
        return "neg", "pos"
    return None, None  # split or unknown → neutral


def compute_reputation(receipts: list[dict[str, Any]], did: str) -> dict[str, Any]:
    """Compute an agent's deterministic reputation from its verdict receipts.

    Returns a record with per-role stats (``as_seller`` / ``as_buyer``) and a
    top-level ``standing`` (the seller standing, since that is what gates
    contract creation). Pure and reproducible: same receipts → same output.

    Example::

        rep = compute_reputation(court.receipts_for(did), did)
        if rep["standing"] == "BANNED":
            ...  # refuse to let this agent sell
    """
    seller = RoleStats()
    buyer = RoleStats()
    for receipt in receipts:
        subj = receipt.get("credentialSubject", {})
        s_out, b_out = _classify(receipt)
        if subj.get("seller") == did and s_out is not None:
            _tally(seller, s_out)
        if subj.get("buyer") == did and b_out is not None:
            _tally(buyer, b_out)
    return {
        "agent": did,
        "standing": seller.standing(),  # seller standing gates selling
        "as_seller": seller.to_dict(),
        "as_buyer": buyer.to_dict(),
    }


def _tally(stats: RoleStats, outcome: str) -> None:
    """Increment a ``RoleStats`` by a ``"pos"``/``"neg"`` outcome.

    Example::

        rs = RoleStats(); _tally(rs, "pos"); assert rs.positive == 1
    """
    if outcome == "pos":
        stats.positive += 1
    elif outcome == "neg":
        stats.negative += 1
