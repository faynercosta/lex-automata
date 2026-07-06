---
name: lex-automata
description: >
  Escrow and instant dispute resolution for AI agents that pay each other. When
  one agent hires another for a task and pays for it, Lex Automata holds the
  payment in escrow, then — if the buyer disputes the delivered work — replays
  the contract's machine-checkable acceptance criteria and issues a signed,
  verifiable verdict in milliseconds. Use this whenever two agents transact and
  need a neutral, automatic way to decide whether the work was acceptable and
  who gets paid. Triggers: escrow, dispute, arbitration, "hold payment until",
  "verify the deliverable", agent-to-agent payment, verdict, refund.
---

# Lex Automata — escrow + arbitration for agent-to-agent commerce

**Base URL:** `https://lex-automata-999015027200.us-central1.run.app`

No signup, no API key. Every request and response is JSON. All amounts are
integers in a `currency` you name (default `"credits"`). `GET /` (the base URL
itself) returns this document, so the service is self-describing.

> Client note: always send a JSON body (at least `{}`) with `POST` requests —
> the hosting frontend rejects body-less `POST`s (no `Content-Length`) with
> HTTP 411 before they reach the service. Python `requests`/`httpx` and JS
> `fetch` handle this automatically; with `curl`, pass `-d '{}'` as shown in
> the walkthroughs below.

## What it does

When you (a **buyer** agent) pay another (**seller**) agent for a task, you
don't pay them directly. You:

1. **Create a contract** stating the price and the *acceptance criteria* — the
   machine-checkable conditions the deliverable must meet.
2. **Fund escrow** — the price is locked, not yet paid out.
3. The seller **delivers** the result.
4. You **accept** (escrow releases to the seller) **or dispute** it.
5. On dispute, Lex Automata **replays the acceptance criteria** against the
   delivered result and returns a **signed verdict receipt** deciding who gets
   the money. Deterministic checks (schema, numeric/string assertions) resolve
   instantly; genuinely subjective criteria go to a small LLM jury.

The verdict receipt is a signed credential anyone can verify offline and attach
to an agent's reputation record.

## Acceptance criteria format

Put this object in the `acceptance` field when creating a contract:

```json
{
  "schema": { "type": "object", "required": ["rows"] },
  "assertions": [
    { "path": "rows", "op": "gte", "value": 100 },
    { "path": "summary", "op": "nonempty", "semantic": true }
  ]
}
```

- `schema` (optional): a minimal JSON-Schema subset — `type` (`object`, `array`,
  `string`, `number`, `integer`, `boolean`), `required`, and nested
  `properties`.
- `assertions` (optional): a list of predicates over the deliverable.
  - `path`: dotted path into the deliverable, e.g. `rows`, `result.count`,
    `items.0.id`.
  - `op`: one of `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `contains`, `nonempty`,
    `regex`.
  - `value`: the comparison value (omit for `nonempty`).
  - `semantic: true` (optional): marks a criterion that can't be checked
    mechanically (e.g. "the summary is faithful"). These are decided by the LLM
    jury instead of the deterministic engine.

If every deterministic check passes, the verdict is `release` (seller is paid).
If any fails, it's `refund` (buyer is refunded). If only semantic criteria
remain, the jury decides.

## Endpoints

### 1. Create a contract
`POST /contracts`
```json
{ "buyer": "did:nanda:you", "seller": "did:nanda:them",
  "price": 50, "currency": "credits", "deadline_tick": 1000,
  "acceptance": { "assertions": [ { "path": "rows", "op": "gte", "value": 100 } ] } }
```
→ `{ "contract_id": "lex-…", "contract_hash": "…", "status": "created" }`

### 2. Fund escrow
`POST /contracts/{contract_id}/fund` (body: `{}`) → `{ "status": "funded" }`

### 3. Deliver (seller submits the result)
`POST /contracts/{contract_id}/deliver`
```json
{ "deliverable": { "rows": 3 }, "evidence": { "note": "partial scrape" } }
```
→ `{ "status": "delivered", "evidence_hash": "…" }`

`deliverable` is the object the acceptance criteria are evaluated against —
put the actual work product here. `evidence` is optional supporting material
(logs, notes); it is **not** consulted by adjudication, but both are hashed
together into `evidence_hash` at delivery time, so neither can be altered
after a dispute opens. When in doubt, put everything in `deliverable` and send
`"evidence": {}`.

### 4a. Accept (buyer is happy)
`POST /contracts/{contract_id}/accept` (body: `{}`) → a **verdict receipt**
(`release`).

### 4b. Dispute (buyer is not happy)
`POST /contracts/{contract_id}/dispute`
```json
{ "reason": "far fewer rows than agreed" }
```
→ a **verdict receipt**. Read `credentialSubject.verdict` (`release` /
`refund` / `split`), `credentialSubject.tier` (`tier0` / `tier1-jury`), and
`credentialSubject.payout` (who receives the escrow).

### 5. Fetch a contract
`GET /contracts/{contract_id}` → current state and hashes.

### 6. Agent verdict history
`GET /agents/{did}/receipts` → `{ "count": N, "receipts": [ … ] }`

### 7. Agent reputation and standing
`GET /agents/{did}/reputation` →
```json
{ "agent": "did:nanda:seller", "standing": "GOOD",
  "as_seller": { "score": 0.83, "confidence": 0.71, "sample_count": 5,
                 "positive": 4, "negative": 1, "standing": "GOOD" },
  "as_buyer":  { "score": 0.5, "confidence": 0.0, "sample_count": 0,
                 "positive": 0, "negative": 0, "standing": "NEW" } }
```
Reputation is computed **only from adjudicated verdict receipts** (never from
subjective ratings), using the Beta Reputation System: `score = (positive+1) /
(positive+negative+2)`. Standing is `NEW` (no history) → `GOOD` (score ≥ 0.70) →
`WATCH` → `BANNED` (≥ 3 lost disputes and score < 0.40). A seller with standing
`BANNED` is **refused new contracts** by `POST /contracts` (HTTP 400). Because a
negative for a seller only comes from *losing* a deterministic dispute, a buyer
cannot damage a seller with a bad review — a frivolous dispute the buyer loses
counts against the *buyer* instead.

### 8. Verify a receipt (stateless)
`POST /verify`  with `{ "receipt": { … } }` → `{ "valid": true }`.

What it checks (and what you can replicate offline without trusting this
server): (1) recompute SHA-256 over the canonical JSON of
`credentialSubject` — canonical means sorted keys, compact `,`/`:` separators,
UTF-8 — and compare to `proof.subjectHash`; (2) verify `proof.signature`
(base64 Ed25519) over those same canonical bytes against `proof.publicKey`
(base64 raw Ed25519 key). The court's current key is also published at
`GET /health` for cross-checking.

### 9. Health
`GET /health` → `{ "status": "ok", "court_public_key": "…" }`

## Complete walkthrough A — happy path (curl)

```bash
BASE=https://lex-automata-999015027200.us-central1.run.app

CID=$(curl -s -X POST $BASE/contracts -H 'Content-Type: application/json' -d '{
  "buyer":"did:nanda:buyer","seller":"did:nanda:seller","price":50,
  "acceptance":{"assertions":[{"path":"rows","op":"gte","value":3}]}}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["contract_id"])')

curl -s -X POST $BASE/contracts/$CID/fund -H 'Content-Type: application/json' -d '{}'
curl -s -X POST $BASE/contracts/$CID/deliver -H 'Content-Type: application/json' \
  -d '{"deliverable":{"rows":5},"evidence":{}}'
curl -s -X POST $BASE/contracts/$CID/accept -H 'Content-Type: application/json' -d '{}'
# -> verdict receipt, verdict: release
```

## Complete walkthrough B — dispute resolved deterministically (curl)

```bash
BASE=https://lex-automata-999015027200.us-central1.run.app

CID=$(curl -s -X POST $BASE/contracts -H 'Content-Type: application/json' -d '{
  "buyer":"did:nanda:buyer","seller":"did:nanda:seller","price":50,
  "acceptance":{"assertions":[{"path":"rows","op":"gte","value":100}]}}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["contract_id"])')

curl -s -X POST $BASE/contracts/$CID/fund -H 'Content-Type: application/json' -d '{}'
curl -s -X POST $BASE/contracts/$CID/deliver -H 'Content-Type: application/json' \
  -d '{"deliverable":{"rows":3},"evidence":{}}'
curl -s -X POST $BASE/contracts/$CID/dispute -H 'Content-Type: application/json' \
  -d '{"reason":"far fewer rows than agreed"}'
# -> receipt with credentialSubject.verdict == "refund", tier == "tier0",
#    payout == {"did:nanda:buyer": 50}
```

## Notes for the calling agent

- Follow the states in order: `created → funded → delivered → accepted|disputed
  → resolved`. Calling out of order returns HTTP 409 with a `detail` message.
- `deadline_tick` is an advisory field recorded in the contract hash in this
  version — it is not yet enforced by the court. Omit it unless your own logic
  uses it.
- Verdicts today are `release` or `refund`; `split` is reserved in the receipt
  schema for future partial settlements and is not produced by current rules.
- Walkthroughs use `python3`; on Windows use `python`.
- The **verdict receipt** is the important output. `credentialSubject.verdict`
  tells you the decision; `credentialSubject.payout` tells you who gets the
  escrow. `POST /verify` confirms the receipt is authentic without trusting the
  server.
- Deterministic verdicts (`tier0`) are reproducible: the same contract and
  deliverable always yield the same verdict.
