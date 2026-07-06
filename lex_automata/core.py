# SPDX-License-Identifier: Apache-2.0
"""Lex Automata core: contracts, escrow, tiered adjudication, verdict receipts.

Framework-free so it is unit-testable without a web server. The FastAPI layer
in ``app.py`` is a thin HTTP wrapper over these classes.

The design in one paragraph: two agents sign a **Contract of Record** carrying
machine-checkable acceptance criteria; the buyer funds **escrow**; the seller
**delivers** an artifact plus evidence whose hashes are committed at delivery
time; on **dispute**, a **Tier-0 deterministic adjudicator** replays the
acceptance criteria against the committed deliverable and renders a verdict in
milliseconds with zero variance; only genuinely semantic criteria fall through
to a **Tier-1 LLM jury** (median of N, temperature 0, deterministic mock
fallback). Every verdict is emitted as a signed, replayable **Verdict Receipt**
(a W3C-VC-shaped JSON-LD credential) that writes back into the parties' records
and can feed a NANDA AgentFacts ``trust_certifications`` list or any Trust-layer
plugin. This is the missing enforcement institution: it turns reputation
(gossip) and payment (coin) into an enforceable judgment (law), exactly as the
medieval Lex Mercatoria did for cross-border trade.

Example::

    court = Court(signing_key=SigningKey.generate())
    c = court.create_contract(
        buyer="did:nanda:buyer", seller="did:nanda:seller",
        price=50, currency="credits", deadline_tick=1000,
        acceptance={"schema": {"type": "object", "required": ["rows"]},
                    "assertions": [{"path": "rows", "op": "gte", "value": 3}]},
    )
    court.fund(c.contract_id)
    court.deliver(c.contract_id, deliverable={"rows": 5}, evidence={})
    receipt = court.dispute(c.contract_id, reason="rows look short")
    assert receipt["credentialSubject"]["verdict"] in ("release", "refund", "split")
"""

from __future__ import annotations

import base64
import enum
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

try:  # real ed25519 when available; deterministic HMAC stub otherwise
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )

    _HAVE_ED25519 = True
except Exception:  # pragma: no cover - exercised only on minimal installs
    _HAVE_ED25519 = False


# ---------------------------------------------------------------------------
# Canonicalization + signing
# ---------------------------------------------------------------------------


def canonical(obj: Any) -> bytes:
    """Deterministic JSON encoding used for every hash and signature.

    Example::

        assert canonical({"b": 1, "a": 2}) == b'{"a":2,"b":1}'
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def sha256_hex(obj: Any) -> str:
    """Hex SHA-256 of the canonical encoding of *obj*.

    Example::

        h = sha256_hex({"rows": 5})
    """
    return hashlib.sha256(canonical(obj)).hexdigest()


class SigningKey:
    """Ed25519 signer with a stable base64 public key (HMAC stub if no libsodium).

    Example::

        key = SigningKey.generate()
        sig = key.sign(b"payload")
        assert SigningKey.verify(key.public_b64, b"payload", sig)
    """

    def __init__(self, seed: bytes) -> None:
        self._seed = seed
        if _HAVE_ED25519:
            self._sk = Ed25519PrivateKey.from_private_bytes(seed)
            pub = self._sk.public_key().public_bytes_raw()
        else:  # pragma: no cover
            self._sk = None
            pub = hashlib.sha256(b"pub:" + seed).digest()
        self.public_b64 = base64.b64encode(pub).decode()

    @classmethod
    def generate(cls, seed: bytes | None = None) -> "SigningKey":
        """Create a signer, optionally from a fixed 32-byte seed (for tests).

        Example::

            key = SigningKey.generate(seed=b"0" * 32)
        """
        if seed is None:
            seed = uuid.uuid4().bytes + uuid.uuid4().bytes
        return cls(seed[:32].ljust(32, b"\0"))

    def sign(self, payload: bytes) -> str:
        """Return a base64 signature over *payload*.

        Example::

            sig = key.sign(b"hello")
        """
        if _HAVE_ED25519:
            return base64.b64encode(self._sk.sign(payload)).decode()
        return base64.b64encode(  # pragma: no cover
            hashlib.sha256(self._seed + payload).digest()
        ).decode()

    @staticmethod
    def verify(public_b64: str, payload: bytes, sig_b64: str) -> bool:
        """Verify a base64 signature against a base64 public key.

        Example::

            ok = SigningKey.verify(key.public_b64, b"hello", sig)
        """
        try:
            sig = base64.b64decode(sig_b64)
            pub = base64.b64decode(public_b64)
        except Exception:
            return False
        if _HAVE_ED25519:
            try:
                Ed25519PublicKey.from_public_bytes(pub).verify(sig, payload)
                return True
            except Exception:
                return False
        return True  # pragma: no cover - stub path


# ---------------------------------------------------------------------------
# Domain model
# ---------------------------------------------------------------------------


class Status(str, enum.Enum):
    """Lifecycle states of a contract.

    Example::

        assert Status.FUNDED.value == "funded"
    """

    CREATED = "created"
    FUNDED = "funded"
    DELIVERED = "delivered"
    ACCEPTED = "accepted"
    DISPUTED = "disputed"
    RESOLVED = "resolved"


@dataclass
class Contract:
    """A signed contract of record between two agents.

    Example::

        c = Contract(contract_id="c1", buyer="b", seller="s", price=10,
                     currency="credits", deadline_tick=100, acceptance={})
    """

    contract_id: str
    buyer: str
    seller: str
    price: int
    currency: str
    deadline_tick: int
    acceptance: dict[str, Any]
    status: Status = Status.CREATED
    created_at: float = field(default_factory=lambda: time.time())
    contract_hash: str = ""
    deliverable: Any = None
    evidence_hash: str | None = None
    delivered_at_tick: int | None = None
    dispute_bond: int = 0

    def to_public(self) -> dict[str, Any]:
        """Serialize the fields safe to hash and return over the wire.

        Example::

            pub = contract.to_public()
        """
        return {
            "contract_id": self.contract_id,
            "buyer": self.buyer,
            "seller": self.seller,
            "price": self.price,
            "currency": self.currency,
            "deadline_tick": self.deadline_tick,
            "acceptance": self.acceptance,
        }


# ---------------------------------------------------------------------------
# Tier-0 deterministic adjudicator
# ---------------------------------------------------------------------------

_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
    "contains": lambda a, b: b in a,
    "nonempty": lambda a, _b: bool(a),
    "regex": lambda a, b: __import__("re").search(b, str(a)) is not None,
}


def _get_path(obj: Any, path: str) -> Any:
    """Resolve a dotted path (``a.b.0.c``) into a nested structure.

    Example::

        assert _get_path({"a": {"b": [7]}}, "a.b.0") == 7
    """
    cur = obj
    for part in path.split("."):
        if part == "":
            continue
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur[part]
        else:
            raise KeyError(path)
    return cur


@dataclass
class Tier0Result:
    """Outcome of deterministic adjudication.

    Example::

        r = Tier0Result(decided=True, verdict="release", checks=[])
    """

    decided: bool
    verdict: str | None
    checks: list[dict[str, Any]]
    residual: list[dict[str, Any]] = field(default_factory=list)


def adjudicate_tier0(contract: Contract) -> Tier0Result:
    """Replay a contract's acceptance criteria against its committed deliverable.

    Two criterion families:

    * **schema** — a minimal JSON-Schema subset (``type``, ``required``,
      ``properties``) checked structurally, fully deterministic;
    * **assertions** — a list of ``{path, op, value}`` predicates over the
      deliverable, evaluated with the ``_OPS`` table.

    A criterion tagged ``"semantic": true`` is *not* decided here; it is
    returned in ``residual`` for the Tier-1 jury. If every deterministic check
    passes, the verdict is ``release`` (pay the seller); if any fails, it is
    ``refund`` (return escrow to the buyer). When only semantic criteria remain,
    ``decided`` is ``False``.

    Example::

        res = adjudicate_tier0(contract)
        if res.decided:
            settle(res.verdict)
    """
    checks: list[dict[str, Any]] = []
    residual: list[dict[str, Any]] = []
    deliverable = contract.deliverable
    acc = contract.acceptance or {}

    passed = True

    schema = acc.get("schema")
    if schema is not None:
        ok, detail = _check_schema(deliverable, schema)
        checks.append({"kind": "schema", "passed": ok, "detail": detail})
        passed = passed and ok

    for a in acc.get("assertions", []):
        if a.get("semantic"):
            residual.append(a)
            continue
        ok, detail = _check_assertion(deliverable, a)
        checks.append({"kind": "assertion", "spec": a, "passed": ok, "detail": detail})
        passed = passed and ok

    if residual and all(c["passed"] for c in checks):
        # Deterministic part is clean but semantic criteria remain undecided.
        return Tier0Result(decided=False, verdict=None, checks=checks, residual=residual)

    verdict = "release" if passed else "refund"
    return Tier0Result(decided=True, verdict=verdict, checks=checks, residual=residual)


def _check_schema(obj: Any, schema: dict[str, Any]) -> tuple[bool, str]:
    """Check a minimal JSON-Schema subset. Returns (passed, detail).

    Example::

        ok, why = _check_schema({"rows": 5}, {"type": "object", "required": ["rows"]})
    """
    t = schema.get("type")
    type_map = {
        "object": dict,
        "array": list,
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
    }
    if t is not None and t in type_map and not isinstance(obj, type_map[t]):
        return False, f"expected type {t}, got {type(obj).__name__}"
    if t == "object":
        for key in schema.get("required", []):
            if not isinstance(obj, dict) or key not in obj:
                return False, f"missing required key {key!r}"
        for key, sub in (schema.get("properties") or {}).items():
            if isinstance(obj, dict) and key in obj:
                ok, detail = _check_schema(obj[key], sub)
                if not ok:
                    return False, f"{key}: {detail}"
    return True, "schema satisfied"


def _check_assertion(obj: Any, spec: dict[str, Any]) -> tuple[bool, str]:
    """Evaluate one ``{path, op, value}`` predicate. Returns (passed, detail).

    Example::

        ok, why = _check_assertion({"rows": 5}, {"path": "rows", "op": "gte", "value": 3})
    """
    op = spec.get("op")
    fn = _OPS.get(op)
    if fn is None:
        return False, f"unknown op {op!r}"
    try:
        actual = _get_path(obj, spec.get("path", ""))
    except (KeyError, IndexError, ValueError):
        return False, f"path {spec.get('path')!r} not found"
    try:
        ok = fn(actual, spec.get("value"))
    except Exception as exc:  # noqa: BLE001 - report, never crash adjudication
        return False, f"op error: {exc}"
    return bool(ok), f"{spec.get('path')} {op} {spec.get('value')!r} -> {ok}"


# ---------------------------------------------------------------------------
# Tier-1 jury (median of N, deterministic mock by default)
# ---------------------------------------------------------------------------

JurorFn = Callable[[Contract, dict[str, Any]], dict[str, Any]]


def deterministic_mock_juror(contract: Contract, criterion: dict[str, Any]) -> dict[str, Any]:
    """A deterministic stand-in juror used in CI and when no LLM is configured.

    Votes ``release`` iff a stable hash of (deliverable, criterion) is even.
    Deterministic by construction, so Tier-1 traces stay reproducible without a
    live model. Replace with a real temperature-0 LLM juror in production.

    Example::

        vote = deterministic_mock_juror(contract, {"path": "summary", "op": "faithful"})
    """
    h = int(sha256_hex([contract.deliverable, criterion]), 16)
    verdict = "release" if h % 2 == 0 else "refund"
    return {"verdict": verdict, "rationale": "deterministic mock juror", "confidence": 0.5}


def jury_vote(
    contract: Contract,
    residual: list[dict[str, Any]],
    juror: JurorFn,
    n_jurors: int = 3,
) -> dict[str, Any]:
    """Run *n_jurors* independent jurors over the residual and take the median.

    Each juror votes ``release`` / ``refund`` per residual criterion; the panel
    verdict is the majority across (juror × criterion) votes. Ties resolve to
    ``refund`` (favor the paying party when the panel is undecided).

    Example::

        panel = jury_vote(contract, residual, deterministic_mock_juror, n_jurors=3)
        assert panel["verdict"] in ("release", "refund")
    """
    votes: list[dict[str, Any]] = []
    release = 0
    for j in range(n_jurors):
        for criterion in residual:
            v = juror(contract, {**criterion, "_juror": j})
            votes.append(v)
            if v["verdict"] == "release":
                release += 1
    total = len(votes) or 1
    verdict = "release" if release * 2 > total else "refund"
    return {"verdict": verdict, "release_votes": release, "total_votes": total, "votes": votes}


# ---------------------------------------------------------------------------
# Court: the orchestrator + escrow ledger + receipt issuer
# ---------------------------------------------------------------------------


class CourtError(ValueError):
    """Raised on invalid state transitions or unknown contracts.

    Example::

        raise CourtError("contract not funded")
    """


class Court:
    """In-memory Lex Automata court: contracts, escrow, adjudication, receipts.

    A production deployment swaps the dicts for a database and
    ``deterministic_mock_juror`` for a real LLM juror, but the state machine and
    the receipt format are exactly these.

    Example::

        court = Court(signing_key=SigningKey.generate(seed=b"x" * 32))
        c = court.create_contract("buyer", "seller", 10, "credits", 100,
                                  {"assertions": [{"path": "ok", "op": "eq", "value": True}]})
        court.fund(c.contract_id)
        court.deliver(c.contract_id, {"ok": True}, {})
        receipt = court.accept(c.contract_id)
    """

    def __init__(
        self,
        signing_key: SigningKey,
        juror: JurorFn | None = None,
        n_jurors: int = 3,
        clock: Callable[[], int] | None = None,
        enforce_reputation: bool = True,
    ) -> None:
        self._key = signing_key
        self._juror = juror or deterministic_mock_juror
        self._n_jurors = n_jurors
        self._clock = clock or (lambda: int(time.time()))
        self._enforce_reputation = enforce_reputation
        self._contracts: dict[str, Contract] = {}
        self._escrow: dict[str, int] = {}
        self._receipts: dict[str, dict[str, Any]] = {}
        self._by_agent: dict[str, list[str]] = {}

    # -- contract lifecycle -------------------------------------------------

    def create_contract(
        self,
        buyer: str,
        seller: str,
        price: int,
        currency: str,
        deadline_tick: int,
        acceptance: dict[str, Any],
        dispute_bond: int = 0,
    ) -> Contract:
        """Create and hash a contract of record.

        Example::

            c = court.create_contract("b", "s", 10, "credits", 100, {})
        """
        if price < 0:
            raise CourtError("price must be non-negative")
        if self._enforce_reputation and self.reputation_of(seller)["standing"] == "BANNED":
            raise CourtError(
                f"seller {seller!r} is BANNED (too many lost disputes) and cannot take new work"
            )
        cid = "lex-" + uuid.uuid4().hex[:16]
        c = Contract(
            contract_id=cid,
            buyer=buyer,
            seller=seller,
            price=price,
            currency=currency,
            deadline_tick=deadline_tick,
            acceptance=acceptance,
            dispute_bond=dispute_bond,
        )
        c.contract_hash = sha256_hex(c.to_public())
        self._contracts[cid] = c
        return c

    def fund(self, contract_id: str) -> Contract:
        """Fund escrow for a created contract (buyer locks the price).

        Example::

            court.fund(c.contract_id)
        """
        c = self._get(contract_id)
        if c.status != Status.CREATED:
            raise CourtError(f"cannot fund a contract in state {c.status.value}")
        self._escrow[contract_id] = c.price
        c.status = Status.FUNDED
        return c

    def deliver(self, contract_id: str, deliverable: Any, evidence: dict[str, Any]) -> Contract:
        """Record the seller's deliverable and commit its evidence hash.

        The evidence hash is committed *now*, before any dispute can be opened,
        so evidence cannot be forged retroactively.

        Example::

            court.deliver(c.contract_id, {"rows": 5}, {"log": "..."})
        """
        c = self._get(contract_id)
        if c.status != Status.FUNDED:
            raise CourtError(f"cannot deliver against a contract in state {c.status.value}")
        c.deliverable = deliverable
        c.evidence_hash = sha256_hex({"deliverable": deliverable, "evidence": evidence})
        c.delivered_at_tick = self._clock()
        c.status = Status.DELIVERED
        return c

    def accept(self, contract_id: str) -> dict[str, Any]:
        """Buyer accepts delivery; escrow releases to the seller.

        Example::

            receipt = court.accept(c.contract_id)
        """
        c = self._get(contract_id)
        if c.status != Status.DELIVERED:
            raise CourtError(f"cannot accept a contract in state {c.status.value}")
        return self._settle(c, verdict="release", tier="buyer-accept", checks=[], jury=None)

    def dispute(self, contract_id: str, reason: str = "") -> dict[str, Any]:
        """Buyer disputes; run tiered adjudication and settle.

        Example::

            receipt = court.dispute(c.contract_id, reason="rows short")
        """
        c = self._get(contract_id)
        if c.status != Status.DELIVERED:
            raise CourtError(f"cannot dispute a contract in state {c.status.value}")
        c.status = Status.DISPUTED

        tier0 = adjudicate_tier0(c)
        if tier0.decided:
            return self._settle(
                c, verdict=tier0.verdict, tier="tier0", checks=tier0.checks, jury=None,
                reason=reason,
            )

        panel = jury_vote(c, tier0.residual, self._juror, self._n_jurors)
        return self._settle(
            c, verdict=panel["verdict"], tier="tier1-jury",
            checks=tier0.checks, jury=panel, reason=reason,
        )

    # -- settlement + receipts ---------------------------------------------

    def _settle(
        self,
        c: Contract,
        verdict: str,
        tier: str,
        checks: list[dict[str, Any]],
        jury: dict[str, Any] | None,
        reason: str = "",
    ) -> dict[str, Any]:
        amount = self._escrow.pop(c.contract_id, 0)
        if verdict == "release":
            payout = {c.seller: amount}
        elif verdict == "refund":
            payout = {c.buyer: amount}
        else:  # split
            half = amount // 2
            payout = {c.buyer: amount - half, c.seller: half}
        c.status = Status.RESOLVED
        receipt = self._issue_receipt(c, verdict, tier, checks, jury, payout, reason)
        self._receipts[receipt["id"]] = receipt
        for agent in (c.buyer, c.seller):
            self._by_agent.setdefault(agent, []).append(receipt["id"])
        return receipt

    def _issue_receipt(
        self,
        c: Contract,
        verdict: str,
        tier: str,
        checks: list[dict[str, Any]],
        jury: dict[str, Any] | None,
        payout: dict[str, int],
        reason: str,
    ) -> dict[str, Any]:
        """Build and sign a W3C-VC-shaped Verdict Receipt.

        The receipt is self-verifying: anyone with the court's public key can
        recompute the canonical hash of ``credentialSubject`` and check the
        signature, and can replay the Tier-0 checks from ``contract_hash`` +
        ``evidence_hash``. This is the artifact that writes back into AgentFacts
        ``trust_certifications`` / a Trust-layer plugin.

        Example::

            receipt = court._issue_receipt(c, "release", "tier0", [], None, {}, "")
        """
        subject = {
            "contract_id": c.contract_id,
            "contract_hash": c.contract_hash,
            "evidence_hash": c.evidence_hash,
            "buyer": c.buyer,
            "seller": c.seller,
            "verdict": verdict,
            "tier": tier,
            "checks": checks,
            "jury": jury,
            "payout": payout,
            "reason": reason,
            "decided_at_tick": self._clock(),
        }
        subject_hash = sha256_hex(subject)
        vc = {
            "@context": [
                "https://www.w3.org/2018/credentials/v1",
                "https://projectnanda.org/lex-automata/v1",
            ],
            "type": ["VerifiableCredential", "LexAutomataVerdict"],
            "id": "urn:lex-automata:verdict:" + uuid.uuid4().hex[:16],
            "issuer": "did:lex-automata:court#" + self._key.public_b64[:12],
            "issuanceDate_tick": self._clock(),
            "credentialSubject": subject,
            "proof": {
                "type": "Ed25519Signature2020",
                "subjectHash": subject_hash,
                "publicKey": self._key.public_b64,
                "signature": self._key.sign(canonical(subject)),
            },
        }
        return vc

    def verify_receipt(self, receipt: dict[str, Any]) -> bool:
        """Verify a receipt's signature and subject-hash binding.

        Example::

            assert court.verify_receipt(receipt)
        """
        return verify_receipt(receipt)

    # -- queries ------------------------------------------------------------

    def get_contract(self, contract_id: str) -> Contract:
        """Fetch a contract or raise ``CourtError``.

        Example::

            c = court.get_contract(cid)
        """
        return self._get(contract_id)

    def receipts_for(self, agent: str) -> list[dict[str, Any]]:
        """Return an agent's portable verdict history (the law-merchant record).

        Example::

            history = court.receipts_for("did:nanda:seller")
        """
        return [self._receipts[rid] for rid in self._by_agent.get(agent, [])]

    def reputation_of(self, agent: str) -> dict[str, Any]:
        """Compute an agent's deterministic reputation from its verdict receipts.

        Example::

            rep = court.reputation_of("did:nanda:seller")
            assert rep["standing"] in ("NEW", "GOOD", "WATCH", "BANNED")
        """
        from lex_automata.reputation import compute_reputation

        return compute_reputation(self.receipts_for(agent), agent)

    def _get(self, contract_id: str) -> Contract:
        c = self._contracts.get(contract_id)
        if c is None:
            raise CourtError(f"unknown contract {contract_id!r}")
        return c


def verify_receipt(receipt: dict[str, Any]) -> bool:
    """Standalone verifier: recompute the subject hash and check the signature.

    Usable by any third party (a Trust-layer plugin, an AgentFacts consumer)
    with no access to the court's internal state.

    Example::

        from lex_automata.core import verify_receipt
        assert verify_receipt(receipt)
    """
    try:
        subject = receipt["credentialSubject"]
        proof = receipt["proof"]
    except (KeyError, TypeError):
        return False
    if sha256_hex(subject) != proof.get("subjectHash"):
        return False
    return SigningKey.verify(proof["publicKey"], canonical(subject), proof["signature"])
