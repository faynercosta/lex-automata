# Court of NANDA — live arena

A ludic, real-time 2D visualization of the Lex Automata court. Every action on
screen is a **real HTTPS call to the deployed service** — agents open contracts,
lock escrow, deliver work, dispute, and the automaton judge renders signed
verdicts, all live.

## Two pieces

- **`index.html`** — the browser arena. Open it (or host it) and press a
  scenario. Buyer/seller characters walk to the courthouse, coins fly into
  escrow, the gavel bangs, verdicts stream in, receipts are filed and verified
  offline, and each agent's reputation stars + standing badge update — up to the
  `BANNED` seller getting barred behind jail bars. It talks to the live court at
  `https://lex-automata-999015027200.us-central1.run.app` (CORS-enabled).

- **`nanda_agents.py`** — the same five scenarios as scriptable NANDA agents
  (each a `did:nanda:*` citizen), for reproducible/automated runs and trace
  capture:

  ```bash
  python nanda_agents.py                                   # narrated run vs live court
  python nanda_agents.py <base-url> --trace run.jsonl      # + JSONL event trace
  ```

## Scenarios

| Scenario | What it demonstrates |
|---|---|
| 🤝 Honest deal | fair delivery → `release`, seller paid |
| 📉 Bad delivery | under-delivery → `refund` at **Tier-0** (deterministic replay) |
| 🙅 Frivolous buyer | disputing good work → buyer loses, penalty lands on the buyer |
| 🧠 Semantic jury | a subjective criterion falls through to the **Tier-1 LLM jury** |
| ⛔ The ban | three lost disputes → `BANNED` → court refuses new work (HTTP 400) |

## Run the arena locally

```bash
python -m http.server 8124 --directory .
# open http://localhost:8124
```

No build step; it's a single self-contained HTML file.
