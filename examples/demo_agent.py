# SPDX-License-Identifier: Apache-2.0
"""Two-agent demo: a buyer hires a seller, disputes, and the court settles.

Run against a live deployment::

    python examples/demo_agent.py https://lex-automata-999015027200.us-central1.run.app

Uses only the Python stdlib (urllib) so a stock agent can copy the pattern.
"""

from __future__ import annotations

import json
import sys
import urllib.request

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://localhost:8000"


def call(method: str, path: str, body: dict | None = None) -> dict:
    """Make one JSON HTTP call and return the decoded response.

    POSTs always carry a body (``{}`` when none is given): hosting frontends
    reject body-less POSTs lacking a Content-Length header with HTTP 411.

    Example::

        call("GET", "/health")
    """
    if method == "POST" and body is None:
        body = {}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as r:  # noqa: S310 - demo client, trusted URL
        return json.loads(r.read())


def main() -> None:
    """Drive the full escrow -> dispute -> verdict-receipt lifecycle."""
    print("court:", call("GET", "/health"))
    c = call(
        "POST",
        "/contracts",
        {
            "buyer": "did:nanda:buyer-01",
            "seller": "did:nanda:scraper-07",
            "price": 50,
            "acceptance": {
                "schema": {"type": "object", "required": ["rows"]},
                "assertions": [{"path": "rows", "op": "gte", "value": 100}],
            },
        },
    )
    cid = c["contract_id"]
    print("contract:", cid, c["contract_hash"][:12])
    call("POST", f"/contracts/{cid}/fund")
    call(
        "POST",
        f"/contracts/{cid}/deliver",
        {"deliverable": {"rows": 3}, "evidence": {"note": "partial scrape"}},
    )
    receipt = call(
        "POST", f"/contracts/{cid}/dispute", {"reason": "far fewer rows than agreed"}
    )
    subj = receipt["credentialSubject"]
    print("verdict:", subj["verdict"], "| tier:", subj["tier"], "| payout:", subj["payout"])
    print("receipt verifies:", call("POST", "/verify", {"receipt": receipt})["valid"])
    hist = call("GET", "/agents/did:nanda:scraper-07/receipts")
    print("seller verdict history size:", hist["count"])


if __name__ == "__main__":
    main()
