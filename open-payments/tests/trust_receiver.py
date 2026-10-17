"""Loopback-only FastAPI contract fixture, not the production Trust backend."""
import hashlib
import hmac
import json
import os
import re
import sqlite3
import sys
import time

from fastapi import FastAPI, HTTPException, Request
import uvicorn

app = FastAPI()
secret = os.environ["TRUST_FIXTURE_SECRET"].encode()
database = os.environ["TRUST_FIXTURE_DB"]
with sqlite3.connect(database) as connection:
    connection.execute("""CREATE TABLE IF NOT EXISTS events (
        event_id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL,
        reference TEXT NOT NULL,
        raw_body BLOB NOT NULL,
        UNIQUE(account_id, reference)
    )""")


@app.get("/ready")
def ready():
    return {"status": "ok"}


@app.post("/api/v1/webhooks/puntored/payments")
async def receive(request: Request):
    raw = await request.body()
    if len(raw) > 65536:
        raise HTTPException(413)
    signature = request.headers.get("X-Puntored-Signature", "")
    match = re.fullmatch(r"t=([0-9]+),v1=([0-9a-f]{64})", signature)
    if not match or abs(time.time() - int(match[1])) > 300:
        raise HTTPException(401)
    expected = hmac.new(secret, match[1].encode() + b"." + raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, match[2]):
        raise HTTPException(401)
    try:
        event = json.loads(raw)
        data = event["data"]
        with sqlite3.connect(database) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO events VALUES (?, ?, ?, ?)",
                (event["event_id"], data["collection_account_id"], data["bre_b_reference"], raw),
            )
    except (KeyError, ValueError):
        raise HTTPException(400) from None
    return {"received": True}


@app.get("/events")
def events():
    with sqlite3.connect(database) as connection:
        rows = connection.execute("SELECT raw_body FROM events ORDER BY rowid").fetchall()
    return {"items": [json.loads(row[0]) for row in rows]}


if __name__ == "__main__":
    uvicorn.run(app, fd=int(sys.argv[1]), access_log=False, log_level="error")
