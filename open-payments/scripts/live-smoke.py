#!/usr/bin/env python3
"""Operator-run Test Wallet acceptance. No secrets are written to the state file."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler
import uuid


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "prepare", "demo"])
    parser.add_argument("--state", type=Path, default=Path("/tmp/trust-op-smoke-state.json"))
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--manual-distribution", action="store_true", help="Use only isolated smoke accounts that Trust does not automatically distribute")
    args = parser.parse_args()
    base = os.environ.get("OP_SMOKE_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
    api_key = os.environ["INTERNAL_API_KEY"]
    opener = build_opener(NoRedirect())
    if args.command == "demo" and not args.manual_distribution:
        raise RuntimeError("Use --manual-distribution only for isolated smoke accounts without automatic backend payouts")

    def call(method, path, payload=None, key=None):
        data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        request = Request(base + path, data=data, method=method)
        request.add_header("X-Internal-Api-Key", api_key)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if key:
            request.add_header("Idempotency-Key", key)
        try:
            with opener.open(request, timeout=20) as response:
                return json.load(response)
        except HTTPError as error:
            # Error envelopes contain only the public contract, not request headers.
            raise RuntimeError(f"{method} {path}: HTTP {error.code}") from None
        except (URLError, TimeoutError):
            raise RuntimeError("Connection failed; rerun with the SAME --state file") from None

    if call("GET", "/v1/health")["status"] != "ok":
        raise RuntimeError("Service health is degraded")
    wallet = call("GET", "/v1/wallet-addresses?" + urlencode({"url": os.environ["OP_CLIENT_WALLET_ADDRESS"]}))
    print("Client wallet resolved:", wallet["url"])
    if args.command == "check":
        return
    if args.state.exists():
        state = json.loads(args.state.read_text())
    else:
        state = {"run_id": str(uuid.uuid4())}
        # Store intent before any POST so a network failure cannot create a new attempt.
        fd = os.open(args.state, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump(state, output)
    run_id = state["run_id"]
    pool = call("GET", "/v1/admin/pool-wallets")["items"]
    pending = False
    for item in pool:
        if item["status"] in ("unauthorized", "revoked"):
            item = call("POST", f'/v1/admin/pool-wallets/{item["id"]}/authorize', {}, f'smoke:{run_id}:authorize:{item["id"]}')
        if item["status"] == "awaiting_authorization":
            print("Approve with the correct Test Wallet user:", item["authorization_url"])
            pending = True
    if pending:
        print("After consent, rerun the same command with the same state file.")
        sys.exit(2)
    if args.command == "prepare":
        print("Pool authorization is ready.")
        return
    lender = os.environ["DEMO_LENDER_WALLET_ADDRESS"]
    merchant = os.environ["DEMO_MERCHANT_WALLET_ADDRESS"]
    income = os.environ["DEMO_TRUST_INCOME_WALLET_ADDRESS"]
    resolved = call("GET", "/v1/wallet-addresses?" + urlencode({"url": merchant}))
    asset, scale = resolved["asset_code"], resolved["asset_scale"]
    credit = int(os.environ.get("DEMO_CREDIT_VALUE", "10000"))
    sale = int(os.environ.get("DEMO_SALE_VALUE", "4800"))
    if credit <= 0 or sale <= 0:
        raise RuntimeError("Demo amounts must be positive integer minor units")

    def amount(value):
        return {"value": str(value), "asset_code": asset, "asset_scale": scale}

    account = call("POST", "/v1/collection-accounts", {"external_ref": f"merchant:smoke:{run_id}", "display_name": "Trust acceptance demo", "asset_code": asset}, f"smoke:{run_id}:account")
    print("Demo QR:", account["qr_payload"])
    return_url = os.environ.get("ALLOWED_RETURN_URL_PREFIXES", "").split(",")[0].strip()
    if not return_url:
        public = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
        if not public:
            raise RuntimeError("Set PUBLIC_BASE_URL to this demo session's HTTPS URL")
        return_url = public + "/v1/health"
    payment = call("POST", "/v1/payments", {"sender_wallet_address": lender, "receiver_wallet_address": merchant, "receive_amount": amount(credit), "external_ref": f"loan:smoke:{run_id}:disb", "return_url": return_url}, f"smoke:{run_id}:credit")
    payment = call("GET", f'/v1/payments/{payment["id"]}')
    if payment["status"] == "awaiting_authorization":
        print("Approve lender consent:", payment["authorization_url"])
        print("Then rerun demo with the same state file.")
        sys.exit(2)

    def await_resource(fetch, label, require_webhook=False):
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            resource = fetch()
            if resource and resource["status"] in ("failed", "rejected", "expired"):
                raise RuntimeError(f"{label}: {resource['status']} {resource.get('failure_code', '')}; inspect before creating any NEW intent")
            if resource and resource["status"] == "completed":
                if not require_webhook or resource.get("webhook_delivered_at"):
                    return resource
            time.sleep(2)
        raise RuntimeError(f"{label}: still pending; rerun with the SAME state file, never a new payment key")

    await_resource(lambda: call("GET", f'/v1/payments/{payment["id"]}'), "credit")
    sim = call("POST", "/v1/sim/puntored/qr-payments", {"collection_account_id": account["id"], "amount": amount(sale), "payer_bank": "Acceptance demo"}, f"smoke:{run_id}:sale")

    def fetch_sim():
        after = 0
        for _ in range(100):
            page = call("GET", f"/v1/sim/puntored/qr-payments?after={after}&limit=200")
            for item in page["items"]:
                if item["id"] == sim["id"]:
                    return item
            if not page["items"]:
                return None
            after = page["next_after"]
        raise RuntimeError("Reconciliation scan limit reached")

    await_resource(fetch_sim, "sale and Trust webhook", require_webhook=True)
    split = [("lender", lender, sale * 20 // 100), ("trust", income, sale * 5 // 100)]
    split.append(("merchant", merchant, sale - sum(value for _, _, value in split)))
    if any(value <= 0 for _, _, value in split):
        raise RuntimeError("Sale too small for the demo split; choose a new demo intent deliberately")
    for label, receiver, value in split:
        transfer = call("POST", "/v1/transfers", {"collection_account_id": account["id"], "receiver_wallet_address": receiver, "debit_amount": amount(value), "external_ref": f"payout:smoke:{run_id}:{label}"}, f"smoke:{run_id}:payout:{label}")
        await_resource(lambda: call("GET", f'/v1/transfers/{transfer["id"]}'), f"distribution to {label}")
    print("PASS: credit -> QR sale -> acknowledged webhook -> three completed transfers")


if __name__ == "__main__":
    try:
        main()
    except (KeyError, ValueError, RuntimeError, OSError) as error:
        print(f"Acceptance incomplete: {error}", file=sys.stderr)
        sys.exit(1)
