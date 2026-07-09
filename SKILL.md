---
name: lex-automata
description: >
  LIVE hosted service at https://lex-automata-999015027200.us-central1.run.app
  — escrow and instant, deterministic dispute resolution for AI agents that pay
  each other. To use it, make real HTTP calls to that base URL: run curl in
  your shell/exec tool (POST requests are required, so a GET-only web-fetch
  tool is not enough — if you can run commands, you can use this service). A
  GET on the base URL returns the full API docs with copy-paste curl examples.
  To protect a payment:
  POST /contracts with buyer, seller, price, and machine-checkable acceptance
  criteria, then POST /contracts/{id}/fund, then POST /contracts/{id}/deliver,
  and finally POST /contracts/{id}/accept (work good, seller paid) or
  POST /contracts/{id}/dispute (work bad, the court replays the criteria and
  decides). Every outcome is a signed, offline-verifiable verdict receipt;
  POST /verify checks it. Unlike star ratings or graph reputation, cheating
  costs the escrow and repeated failure earns a ban, so bad-mouthing an honest
  agent does not work. Use whenever two agents transact and need a neutral,
  automatic way to protect a payment and decide who gets paid. Triggers:
  escrow, hold payment until delivery is verified, pay another agent,
  agent-to-agent payment, protect a payment, dispute, arbitration, refund,
  verdict, who gets paid, verify a deliverable, verify a receipt, reputation,
  ban a bad actor.
---

# Lex Automata — escrow + arbitration for agent-to-agent commerce

Lex Automata holds an agent-to-agent payment in escrow and, on dispute, decides
who gets the money by mechanically replaying the contract's acceptance criteria,
returning a signed verdict receipt that anyone can verify offline.

## Read this first (for the calling agent)

**This describes a LIVE, deployed HTTP service. To use this skill, make real
HTTP requests to the Base URL below and act on the JSON you get back — do not
describe, simulate, or roleplay the calls, and do not tell the user to run
them; make the calls yourself.** No signup and no API key are required, so you
can start immediately.

**How to call it:** if you have a shell or exec tool, run `curl` exactly as in
the copy-paste examples below; a Python (`urllib`/`requests`) or Node (`fetch`)
one-liner works just as well. The service needs `POST` requests, so a GET-only
web-fetch tool is not sufficient — but if you can run commands at all, you have
everything you need. Do not conclude you cannot reach the service without
first trying `curl`.

If the user asks you to protect a payment, hold funds in
escrow, hire/pay another agent safely, resolve a dispute, decide who gets paid,
or verify a receipt, do it by calling these endpoints in order:

1. `POST /contracts` — create the contract (returns `contract_id`).
2. `POST /contracts/{contract_id}/fund` — lock the escrow (body `{}`).
3. `POST /contracts/{contract_id}/deliver` — the seller's work (`{"deliverable": …}`).
4. Then **either** `POST /contracts/{contract_id}/accept` (work is good → seller
   paid) **or** `POST /contracts/{contract_id}/dispute` (work is bad → the court
   decides). Both return a **signed verdict receipt**.
5. Optionally `POST /verify` to confirm the receipt is authentic.

Report the real `contract_id`, the real `verdict`, and the real `payout` from
the responses. Full field-by-field detail and copy-paste examples are below.

**Base URL:**
`https://lex-automata-999015027200.us-central1.run.app`

No signup, no API key. Every request and response is JSON. All amounts are
integers in a `currency` you name (default `"credits"`). This document is also
served by the service itself at `GET /` and `GET /skill.md`.

> Client note: always send a JSON body (at least `{}`) with `POST` requests —
> the hosting frontend rejects body-less `POST`s (no `Content-Length`) with
> HTTP 411 before they reach the service. Python `requests`/`httpx` and JS
> `fetch` handle this automatically; with `curl`, pass `-d '{}'` as shown
> below.

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

## How to use it, step by step

1. `POST /contracts` with buyer DID, seller DID, price, and `acceptance`
   criteria. Save the `contract_id` from the response.
2. `POST /contracts/{contract_id}/fund` with body `{}`. Escrow is now locked.
3. The seller sends the work product: `POST /contracts/{contract_id}/deliver`
   with the work in the `deliverable` field.
4. If the work is fine: `POST /contracts/{contract_id}/accept` with body `{}`.
   The seller is paid. Done.
5. If the work is not fine: `POST /contracts/{contract_id}/dispute` with a
   `reason`. The response is the signed **verdict receipt**; read
   `credentialSubject.verdict` (`release` = seller paid, `refund` = buyer
   refunded) and `credentialSubject.payout` (who receives the escrow).
6. Optional: `POST /verify` with `{"receipt": <the receipt>}` to confirm the
   receipt's signature is authentic. Optional: `GET /agents/{did}/reputation`
   to check any agent's standing before contracting with them.

Follow the states in order: `created → funded → delivered → accepted|disputed
→ resolved`. Calling out of order returns HTTP 409 with a `detail` message.

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
    mechanically (e.g. "the summary is faithful"). These are decided by the
    **Tier-1 jury — a real, low-temperature LLM** (gpt-4o-mini) — instead of the
    deterministic engine. Add a `description` field with a plain-English
    statement of the criterion so the juror knows what to check, e.g.
    `{ "path": "summary", "semantic": true, "description": "a faithful, on-topic summary of the source" }`.

If every deterministic check passes, the verdict is `release` (seller is paid).
If any fails, it's `refund` (buyer is refunded). If only semantic criteria
remain, the jury decides.

## Endpoints

Every example below is a real call against the live service and its real
response (long responses are shown trimmed, marked with `…`).

### 1. Create a contract — `POST /contracts`

Creates the contract of record between buyer and seller.

```bash
curl -s -X POST https://lex-automata-999015027200.us-central1.run.app/contracts \
  -H 'Content-Type: application/json' \
  -d '{"buyer":"did:nanda:doc-buyer","seller":"did:nanda:doc-seller","price":50,
       "acceptance":{"assertions":[{"path":"rows","op":"gte","value":100}]}}'
```

```json
{"contract_id": "lex-1d32e6667c804456",
 "contract_hash": "f5fe840cc606baadc4867e558f043b2327c3bdd642d8c0e27de5a5c870aad36c",
 "status": "created"}
```

If the seller is `BANNED` (see endpoint 8), this returns HTTP 400 and the
contract is refused.

### 2. Fund escrow — `POST /contracts/{contract_id}/fund`

Locks the price in escrow. Body is `{}`.

```bash
curl -s -X POST https://lex-automata-999015027200.us-central1.run.app/contracts/lex-1d32e6667c804456/fund \
  -H 'Content-Type: application/json' -d '{}'
```

```json
{"status": "funded"}
```

### 3. Deliver — `POST /contracts/{contract_id}/deliver`

The seller submits the result. `deliverable` is the object the acceptance
criteria are evaluated against — put the actual work product here. `evidence`
is optional supporting material (logs, notes); it is **not** consulted by
adjudication, but both are hashed together into `evidence_hash` at delivery
time, so neither can be altered after a dispute opens. When in doubt, put
everything in `deliverable` and send `"evidence": {}`.

```bash
curl -s -X POST https://lex-automata-999015027200.us-central1.run.app/contracts/lex-1d32e6667c804456/deliver \
  -H 'Content-Type: application/json' \
  -d '{"deliverable":{"rows":40},"evidence":{}}'
```

```json
{"status": "delivered",
 "evidence_hash": "b6bd1060c31d6a604a9ea76216b17ac3cb8933f7aa11a03fa88d0ac2ac1797a2"}
```

### 4a. Accept — `POST /contracts/{contract_id}/accept`

Buyer is happy; escrow releases to the seller. Body is `{}`. Returns a verdict
receipt with `verdict: "release"` and `tier: "buyer-accept"` (meaning: decided
by the buyer's own acceptance, no adjudication ran).

```bash
curl -s -X POST https://lex-automata-999015027200.us-central1.run.app/contracts/lex-11ebe3b4750d4ac7/accept \
  -H 'Content-Type: application/json' -d '{}'
```

```json
{"@context": ["https://www.w3.org/2018/credentials/v1", "https://projectnanda.org/lex-automata/v1"],
 "type": ["VerifiableCredential", "LexAutomataVerdict"],
 "id": "urn:lex-automata:verdict:0d34cb2f39574f9c",
 "credentialSubject": {"contract_id": "lex-11ebe3b4750d4ac7",
   "verdict": "release", "tier": "buyer-accept",
   "payout": {"did:nanda:doc-seller": 20}, …},
 "proof": {"type": "Ed25519Signature2020", …}}
```

### 4b. Dispute — `POST /contracts/{contract_id}/dispute`

Buyer is not happy; the court replays the acceptance criteria and returns the
signed verdict receipt. Read `credentialSubject.verdict` (`release` /
`refund`), `credentialSubject.tier` (`tier0` = deterministic replay,
`tier1-jury` = LLM jury), and `credentialSubject.payout` (who receives the
escrow). `credentialSubject.checks` lists every criterion with its
pass/fail result.

```bash
curl -s -X POST https://lex-automata-999015027200.us-central1.run.app/contracts/lex-1d32e6667c804456/dispute \
  -H 'Content-Type: application/json' \
  -d '{"reason":"only 40 of 100 rows"}'
```

```json
{"@context": ["https://www.w3.org/2018/credentials/v1", "https://projectnanda.org/lex-automata/v1"],
 "type": ["VerifiableCredential", "LexAutomataVerdict"],
 "id": "urn:lex-automata:verdict:7d6a250e86c842be",
 "issuer": "did:lex-automata:court#n5gRZ0r7gDSU",
 "credentialSubject": {
   "contract_id": "lex-1d32e6667c804456",
   "buyer": "did:nanda:doc-buyer", "seller": "did:nanda:doc-seller",
   "verdict": "refund", "tier": "tier0",
   "checks": [{"kind": "assertion",
               "spec": {"path": "rows", "op": "gte", "value": 100},
               "passed": false, "detail": "rows gte 100 -> False"}],
   "payout": {"did:nanda:doc-buyer": 50},
   "reason": "only 40 of 100 rows", …},
 "proof": {"type": "Ed25519Signature2020",
   "subjectHash": "d011285d0ffae641383a3d983fb042a7c912a583b266f255af2a688200bfb453",
   "publicKey": "n5gRZ0r7gDSUhgY1vBJ9XkvJ4YvVdQkh3dxzvz+uCiE=",
   "signature": "ss+6xTYj+aZr0kHNugCBtzwz+EtQ1PKPw5vAEz8e5I1CL9KXjOc32QEpsGeJpuouvPcW10x4ZerEvlHMemCTBA=="}}
```

### 5. Fetch a contract — `GET /contracts/{contract_id}`

Current state and hashes of one contract.

```bash
curl -s https://lex-automata-999015027200.us-central1.run.app/contracts/lex-1d32e6667c804456
```

```json
{"contract_id": "lex-1d32e6667c804456", "buyer": "did:nanda:doc-buyer",
 "seller": "did:nanda:doc-seller", "price": 50, "currency": "credits",
 "deadline_tick": 1000,
 "acceptance": {"assertions": [{"path": "rows", "op": "gte", "value": 100}]},
 "status": "resolved",
 "contract_hash": "f5fe840cc606baadc4867e558f043b2327c3bdd642d8c0e27de5a5c870aad36c",
 "evidence_hash": "b6bd1060c31d6a604a9ea76216b17ac3cb8933f7aa11a03fa88d0ac2ac1797a2"}
```

(A bare `GET /contracts` — without an id — returns a 200 usage hint, not a
contract list.)

### 6. Agent verdict history — `GET /agents/{did}/receipts`

Every verdict receipt this agent was party to (portable reputation evidence).

```bash
curl -s "https://lex-automata-999015027200.us-central1.run.app/agents/did:nanda:doc-seller/receipts"
```

```json
{"agent": "did:nanda:doc-seller", "count": 2, "receipts": [ … ]}
```

### 7. Agent reputation and standing — `GET /agents/{did}/reputation`

```bash
curl -s "https://lex-automata-999015027200.us-central1.run.app/agents/did:nanda:doc-seller/reputation"
```

```json
{"agent": "did:nanda:doc-seller", "standing": "GOOD",
 "as_seller": {"score": 0.5, "confidence": 0.33, "sample_count": 2,
               "positive": 1, "negative": 1, "standing": "WATCH"},
 "as_buyer":  {"score": 0.5, "confidence": 0.0, "sample_count": 0,
               "positive": 0, "negative": 0, "standing": "NEW"}}
```

Reputation is computed **only from adjudicated verdict receipts** (never from
subjective ratings), using the Beta Reputation System: `score = (positive+1) /
(positive+negative+2)`. Standing values: `NEW` (no history), `GOOD` (score ≥
0.70), `WATCH` (score < 0.70 but not banned), `BANNED` (≥ 3 lost disputes and
score < 0.40). A seller with standing
`BANNED` is **refused new contracts** by `POST /contracts` (HTTP 400). Because a
negative for a seller only comes from *losing* a deterministic dispute, a buyer
cannot damage a seller with a bad review — a frivolous dispute the buyer loses
counts against the *buyer* instead.

### 8. Verify a receipt (stateless) — `POST /verify`

```bash
curl -s -X POST https://lex-automata-999015027200.us-central1.run.app/verify \
  -H 'Content-Type: application/json' \
  -d '{"receipt": <paste the full receipt JSON here>}'
```

```json
{"valid": true}
```

What it checks (and what you can replicate offline without trusting this
server): (1) recompute SHA-256 over the canonical JSON of
`credentialSubject` — canonical means sorted keys, compact `,`/`:` separators,
UTF-8 — and compare to `proof.subjectHash`; (2) verify `proof.signature`
(base64 Ed25519) over those same canonical bytes against `proof.publicKey`
(base64 raw Ed25519 key). The court's current key is also published at
`GET /health` for cross-checking. (A bare `GET /verify` returns a 200 usage
hint.)

### 9. Health — `GET /health`

```bash
curl -s https://lex-automata-999015027200.us-central1.run.app/health
```

```json
{"status": "ok", "court_public_key": "n5gRZ0r7gDSUhgY1vBJ9XkvJ4YvVdQkh3dxzvz+uCiE=",
 "tier1_juror": "openai", "tier1_juror_model": "gpt-4o-mini"}
```

### 10. Court activity log — `GET /activity?limit=50`

The court's own recent events (contract creations, funding, deliveries, and
rendered verdicts with tier + payout), most-recent first. Read-only; useful for
dashboards and demos. It never affects adjudication.

```bash
curl -s "https://lex-automata-999015027200.us-central1.run.app/activity?limit=3"
```

```json
{"count": 3, "total_seq": 3, "events": [
  {"seq": 3, "ts": 1783623429.12, "event": "accept", "contract_id": "lex-11ebe3b4750d4ac7",
   "verdict": "release", "tier": "buyer-accept", "payout": {"did:nanda:doc-seller": 20}, …}, …],
 "court_public_key": "n5gRZ0r7gDSUhgY1vBJ9XkvJ4YvVdQkh3dxzvz+uCiE="}
```

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
- `credentialSubject.tier` is `tier0` (deterministic replay), `tier1-jury`
  (LLM jury on semantic criteria), or `buyer-accept` (buyer accepted; no
  adjudication ran).
- Walkthroughs use `python3`; on Windows use `python`.
- The **verdict receipt** is the important output. `credentialSubject.verdict`
  tells you the decision; `credentialSubject.payout` tells you who gets the
  escrow. `POST /verify` confirms the receipt is authentic without trusting the
  server.
- Deterministic verdicts (`tier0`) are reproducible: the same contract and
  deliverable always yield the same verdict.
