# SPDX-License-Identifier: Apache-2.0
"""NANDA agents that exercise the live Lex Automata court.

Each agent is a NANDA citizen identified by a ``did:nanda:*`` DID. The agents
transact through the deployed court exactly as the SKILL.md prescribes, and the
harness narrates what happens and writes a JSONL event trace that the arena UI
(or a paper's analysis) can replay.

Run against the live service::

    python nanda_agents.py
    python nanda_agents.py https://lex-automata-999015027200.us-central1.run.app --trace run.jsonl

Uses only the Python standard library so a stock agent can copy the pattern.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any

DEFAULT_BASE = "https://lex-automata-999015027200.us-central1.run.app"


# ---------------------------------------------------------------------------
# Thin court client (stdlib only)
# ---------------------------------------------------------------------------


class Court:
    """HTTP client for one Lex Automata deployment.

    Example::

        court = Court("https://lex-automata-...run.app")
        court.health()["status"]  # -> "ok"
    """

    def __init__(self, base: str, on_event: Any = None) -> None:
        self.base = base.rstrip("/")
        self._on_event = on_event

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        if method == "POST" and body is None:
            body = {}  # frontends 411 on body-less POSTs
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 - trusted URL
                return json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return {"_http_error": exc.code, "detail": json.loads(exc.read() or b"{}").get("detail")}

    # -- lifecycle verbs ----------------------------------------------------

    def health(self) -> Any:
        """Return court liveness + public key. Example:: court.health()."""
        return self._call("GET", "/health")

    def create(self, buyer: str, seller: str, price: int, acceptance: dict[str, Any]) -> Any:
        """Create a contract of record. Example:: court.create(b, s, 50, {})."""
        return self._call(
            "POST",
            "/contracts",
            {"buyer": buyer, "seller": seller, "price": price, "acceptance": acceptance},
        )

    def fund(self, cid: str) -> Any:
        """Lock escrow. Example:: court.fund(cid)."""
        return self._call("POST", f"/contracts/{cid}/fund")

    def deliver(self, cid: str, deliverable: Any, evidence: dict[str, Any] | None = None) -> Any:
        """Commit the seller's deliverable. Example:: court.deliver(cid, {'rows': 5})."""
        return self._call(
            "POST", f"/contracts/{cid}/deliver", {"deliverable": deliverable, "evidence": evidence or {}}
        )

    def accept(self, cid: str) -> Any:
        """Buyer accepts; escrow releases. Example:: court.accept(cid)."""
        return self._call("POST", f"/contracts/{cid}/accept")

    def dispute(self, cid: str, reason: str) -> Any:
        """Buyer disputes; court adjudicates. Example:: court.dispute(cid, 'short')."""
        return self._call("POST", f"/contracts/{cid}/dispute", {"reason": reason})

    def reputation(self, did: str) -> Any:
        """Fetch an agent's receipt-derived reputation. Example:: court.reputation(did)."""
        return self._call("GET", f"/agents/{did}/reputation")

    def verify(self, receipt: dict[str, Any]) -> Any:
        """Statelessly verify a receipt. Example:: court.verify(receipt)."""
        return self._call("POST", "/verify", {"receipt": receipt})


# ---------------------------------------------------------------------------
# Event stream (narration + JSONL trace + optional UI callback)
# ---------------------------------------------------------------------------

_EVENTS: list[dict[str, Any]] = []


def emit(kind: str, **fields: Any) -> None:
    """Record one arena event (printed, stored, and forwarded to any UI hook)."""
    ev = {"t": round(time.time(), 3), "kind": kind, **fields}
    _EVENTS.append(ev)
    icon = {
        "scene": "🎬",
        "contract": "📜",
        "fund": "🪙",
        "deliver": "📦",
        "accept": "🤝",
        "dispute": "⚖️",
        "verdict": "🔨",
        "receipt": "🧾",
        "reputation": "⭐",
        "banned": "⛔",
        "verify": "🔎",
    }.get(kind, "•")
    msg = fields.get("msg", "")
    print(f"  {icon} {msg}")


# ---------------------------------------------------------------------------
# Agent personas
# ---------------------------------------------------------------------------


def _did(role: str) -> str:
    return f"did:nanda:{role}-{uuid.uuid4().hex[:6]}"


@dataclass
class Agent:
    """A NANDA citizen agent with a DID and a running verdict record.

    Example::

        seller = Agent("Honest Hana", "seller")
    """

    name: str
    role: str
    did: str = field(default="")

    def __post_init__(self) -> None:
        if not self.did:
            self.did = _did(self.role)


# ---------------------------------------------------------------------------
# Scenarios — each returns the receipt(s) it produced
# ---------------------------------------------------------------------------


def scene_honest_deal(court: Court) -> None:
    """A fair job: seller delivers what was promised and is paid in full."""
    buyer, seller = Agent("Buyer Bo", "buyer"), Agent("Honest Hana", "seller")
    emit("scene", msg=f"HONEST DEAL — {buyer.name} hires {seller.name} to scrape >= 3 rows for 50 credits")
    acc = {"schema": {"type": "object", "required": ["rows"]},
           "assertions": [{"path": "rows", "op": "gte", "value": 3}]}
    c = court.create(buyer.did, seller.did, 50, acc)
    cid = c["contract_id"]
    emit("contract", msg=f"contract {cid[:12]} sealed (hash {c['contract_hash'][:10]})", cid=cid,
         buyer=buyer.did, seller=seller.did, price=50)
    court.fund(cid)
    emit("fund", msg="Buyer Bo locks 50 credits in escrow", cid=cid, amount=50)
    court.deliver(cid, {"rows": 7}, {"note": "clean scrape"})
    emit("deliver", msg="Honest Hana delivers {rows: 7}", cid=cid, deliverable={"rows": 7})
    receipt = court.accept(cid)
    _settle(court, receipt, buyer, seller, msg="Buyer Bo is happy and ACCEPTS")


def scene_bad_delivery(court: Court) -> None:
    """Deterministic dispute: seller under-delivers, Tier-0 refunds the buyer."""
    buyer, seller = Agent("Buyer Bo", "buyer"), Agent("Corner-Cutter Cid", "seller")
    emit("scene", msg=f"BAD DELIVERY — {buyer.name} needs >= 100 rows; {seller.name} cuts corners")
    acc = {"schema": {"type": "object", "required": ["rows"]},
           "assertions": [{"path": "rows", "op": "gte", "value": 100}]}
    cid = court.create(buyer.did, seller.did, 50, acc)["contract_id"]
    emit("contract", msg=f"contract {cid[:12]} sealed (needs rows>=100)", cid=cid,
         buyer=buyer.did, seller=seller.did, price=50)
    court.fund(cid)
    emit("fund", msg="Buyer Bo locks 50 credits in escrow", cid=cid, amount=50)
    court.deliver(cid, {"rows": 12}, {"note": "site blocked us"})
    emit("deliver", msg="Corner-Cutter Cid delivers only {rows: 12}", cid=cid, deliverable={"rows": 12})
    receipt = court.dispute(cid, "far fewer rows than agreed")
    _settle(court, receipt, buyer, seller, msg="Buyer Bo DISPUTES — the court replays the criteria")


def scene_frivolous_buyer(court: Court) -> None:
    """A buyer disputes good work, loses, and the penalty lands on the buyer."""
    buyer, seller = Agent("Frivolous Fritz", "buyer"), Agent("Honest Hana", "seller")
    emit("scene", msg=f"FRIVOLOUS DISPUTE — {buyer.name} disputes perfectly good work")
    acc = {"assertions": [{"path": "rows", "op": "gte", "value": 5}]}
    cid = court.create(buyer.did, seller.did, 30, acc)["contract_id"]
    emit("contract", msg=f"contract {cid[:12]} sealed (needs rows>=5)", cid=cid,
         buyer=buyer.did, seller=seller.did, price=30)
    court.fund(cid)
    emit("fund", msg="Frivolous Fritz locks 30 credits in escrow", cid=cid, amount=30)
    court.deliver(cid, {"rows": 9}, {})
    emit("deliver", msg="Honest Hana delivers a solid {rows: 9}", cid=cid, deliverable={"rows": 9})
    receipt = court.dispute(cid, "I just don't want to pay")
    _settle(court, receipt, buyer, seller, msg="Fritz DISPUTES — but the criteria pass, so he loses")


def scene_semantic_jury(court: Court) -> None:
    """A subjective criterion falls through Tier-0 to the Tier-1 jury."""
    buyer, seller = Agent("Buyer Bo", "buyer"), Agent("Wordsmith Wren", "seller")
    emit("scene", msg=f"SEMANTIC CASE — '{'the summary is faithful'}' can't be machine-checked")
    acc = {"schema": {"type": "object", "required": ["summary"]},
           "assertions": [{"path": "summary", "op": "nonempty", "semantic": True}]}
    cid = court.create(buyer.did, seller.did, 40, acc)["contract_id"]
    emit("contract", msg=f"contract {cid[:12]} sealed (semantic: faithful summary)", cid=cid,
         buyer=buyer.did, seller=seller.did, price=40)
    court.fund(cid)
    emit("fund", msg="Buyer Bo locks 40 credits in escrow", cid=cid, amount=40)
    court.deliver(cid, {"summary": "A concise, on-topic summary of the source."}, {})
    emit("deliver", msg="Wordsmith Wren delivers a summary", cid=cid, deliverable={"summary": "..."})
    receipt = court.dispute(cid, "is this faithful?")
    _settle(court, receipt, buyer, seller, msg="Dispute goes to the Tier-1 JURY (semantic)")


def scene_repeat_offender(court: Court) -> None:
    """A serial under-deliverer loses three disputes and is BANNED from new work."""
    seller = Agent("Repeat-Offender Rex", "seller")
    emit("scene", msg=f"THE BAN — {seller.name} keeps under-delivering; watch his standing fall")
    for i in (1, 2, 3):
        buyer = Agent(f"Buyer {i}", "buyer")
        acc = {"assertions": [{"path": "rows", "op": "gte", "value": 100}]}
        cid = court.create(buyer.did, seller.did, 10, acc)["contract_id"]
        court.fund(cid)
        court.deliver(cid, {"rows": 1}, {})
        receipt = court.dispute(cid, "almost nothing delivered")
        v = receipt["credentialSubject"]["verdict"]
        rep = court.reputation(seller.did)
        s = rep["as_seller"]
        emit("reputation", msg=f"loss #{i}: verdict={v} -> standing {rep['standing']} "
             f"(score {s['score']:.2f}, {s['negative']} losses)", did=seller.did,
             standing=rep["standing"], score=s["score"], negative=s["negative"])
    blocked = court.create(Agent("Buyer 4", "buyer").did, seller.did, 10, {})
    if blocked.get("_http_error") == 400:
        emit("banned", msg=f"BANNED: the court REFUSES {seller.name} a new contract (HTTP 400)",
             did=seller.did, detail=blocked.get("detail"))
    else:
        emit("banned", msg="(seller was not banned — unexpected)", did=seller.did)


def _settle(court: Court, receipt: dict[str, Any], buyer: Agent, seller: Agent, msg: str) -> None:
    """Narrate a verdict receipt: outcome, payout, reputation, and verification."""
    emit("dispute", msg=msg)
    cs = receipt["credentialSubject"]
    winner_did = next(iter(cs["payout"]), None)
    winner = buyer.name if winner_did == buyer.did else seller.name
    tier = {"tier0": "Tier-0 (deterministic replay)", "tier1-jury": "Tier-1 (LLM jury)",
            "buyer-accept": "direct acceptance"}.get(cs["tier"], cs["tier"])
    emit("verdict", msg=f"VERDICT: {cs['verdict'].upper()} via {tier} — pays {winner} {list(cs['payout'].values())[0]}",
         verdict=cs["verdict"], tier=cs["tier"], payout=cs["payout"], winner=winner_did)
    valid = court.verify(receipt).get("valid")
    emit("receipt", msg=f"receipt {receipt['id'].split(':')[-1][:10]} issued & signed — verifies offline: {valid}",
         receipt_id=receipt["id"], valid=valid)
    for who in (seller,):
        rep = court.reputation(who.did)
        s = rep["as_seller"]
        emit("reputation", msg=f"{who.name} standing: {rep['standing']} (score {s['score']:.2f})",
             did=who.did, standing=rep["standing"], score=s["score"])


SCENES = [
    ("honest_deal", scene_honest_deal),
    ("bad_delivery", scene_bad_delivery),
    ("frivolous_buyer", scene_frivolous_buyer),
    ("semantic_jury", scene_semantic_jury),
    ("repeat_offender", scene_repeat_offender),
]


def main() -> None:
    """Run every scenario against the live court and optionally write a trace."""
    try:  # emoji narration needs UTF-8; Windows consoles default to cp1252
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass
    base = DEFAULT_BASE
    trace_path = None
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--trace" and i + 1 < len(args):
            trace_path = args[i + 1]
        elif a.startswith("http"):
            base = a

    court = Court(base)
    h = court.health()
    print(f"\n=== Court of NANDA — live at {base} ===")
    print(f"court public key: {h.get('court_public_key')}\n")

    for name, fn in SCENES:
        print(f"\n--- scene: {name} ---")
        fn(court)

    if trace_path:
        with open(trace_path, "w", encoding="utf-8") as f:
            for ev in _EVENTS:
                f.write(json.dumps(ev) + "\n")
        print(f"\nwrote {len(_EVENTS)} events to {trace_path}")
    print("\n=== all scenes complete ===")


if __name__ == "__main__":
    main()
