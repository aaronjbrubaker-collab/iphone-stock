#!/usr/bin/env python3
"""
iPhone pickup-stock checker.

Asks Apple's store pickup endpoint which Apple Stores within a radius of a ZIP
code have each configured iPhone model available, writes a status.json snapshot for the
dashboard, and sends a phone push (via ntfy) whenever a model becomes
available at a store where it wasn't available on the previous check.

Standard library only, so it runs anywhere Python 3.9+ is installed.

Usage:
  python checker/check_stock.py --config config.json --prev prev_status.json --out status.json
  python checker/check_stock.py --fixture tests/fixtures/sample.json --dry-run   # offline test

Environment variables:
  APPLE_ZIP   ZIP code to search around (overrides config "zip")
  NTFY_TOPIC  ntfy topic name to push alerts to (overrides config "ntfy_topic")
  NTFY_SERVER ntfy server base URL (default https://ntfy.sh)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

APPLE_URL = "https://www.apple.com/shop/retail/pickup-message"
BUY_URL = "https://www.apple.com/shop/buy-iphone"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/18.0 Safari/605.1.15"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BUY_URL,
}


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

def build_url(parts: list[str], zip_code: str) -> str:
    params: list[tuple[str, str]] = [("pl", "true")]
    for i, part in enumerate(parts):
        params.append((f"parts.{i}", part))
    params.append(("location", zip_code))
    return f"{APPLE_URL}?{urllib.parse.urlencode(params)}"


def fetch_apple(parts: list[str], zip_code: str, retries: int = 2, timeout: int = 20) -> dict:
    url = build_url(parts, zip_code)
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"Apple request failed: {last_err}")


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def store_miles(store: dict) -> float | None:
    """Distance in miles from Apple's numeric field, or parsed from '4.2 mi'."""
    d = store.get("storedistance")
    if isinstance(d, (int, float)):
        return float(d)
    text = str(store.get("storeDistanceWithUnit") or "").split()
    try:
        return float(text[0]) if text else None
    except ValueError:
        return None


def parse_response(data: dict, models: list[dict], max_stores: int,
                   radius_miles: float | None = None) -> list[dict]:
    """Turn Apple's response into one entry per configured model.

    Handles the current retail/pickup-message shape (body.stores) and the older
    fulfillment-messages shape (body.content.pickupMessage.stores).
    """
    body = data.get("body") or {}
    legacy = (body.get("content") or {}).get("pickupMessage") or {}
    error = body.get("errorMessage") or legacy.get("errorMessage")
    if error:
        raise RuntimeError(f"Apple returned an error: {error}")
    stores = body.get("stores") or legacy.get("stores") or []
    if radius_miles:
        stores = [s for s in stores if (store_miles(s) is None or store_miles(s) <= radius_miles)]
    stores = stores[:max_stores]

    results = []
    for model in models:
        part = model["part"]
        store_rows = []
        for store in stores:
            avail = (store.get("partsAvailability") or {}).get(part)
            if not avail:
                continue
            display = (avail.get("pickupDisplay") or "").lower()
            store_rows.append({
                "id": str(store.get("storeNumber", "")),
                "name": store.get("storeName", "Apple Store"),
                "city": ", ".join(x for x in (store.get("city"), store.get("state")) if x),
                "distance": store.get("storeDistanceWithUnit", ""),
                "available": display == "available",
                "quote": avail.get("pickupSearchQuote") or "",
            })
        results.append({
            "part": part,
            "label": model.get("label", part),
            "any_available": any(s["available"] for s in store_rows),
            "stores": store_rows,
        })
    return results


# --------------------------------------------------------------------------
# Change detection + alerts
# --------------------------------------------------------------------------

def available_set(status: dict) -> set[tuple[str, str]]:
    out = set()
    for m in status.get("models", []) or []:
        for s in m.get("stores", []) or []:
            if s.get("available"):
                out.add((m.get("part", ""), s.get("id", "")))
    return out


def new_availability(prev: dict, models: list[dict]) -> dict[str, list[dict]]:
    """Return {part: [store rows]} for stores that just became available."""
    before = available_set(prev)
    fresh: dict[str, list[dict]] = {}
    for m in models:
        for s in m["stores"]:
            if s["available"] and (m["part"], s["id"]) not in before:
                fresh.setdefault(m["part"], []).append(s)
    return fresh


def _ascii(text: str) -> str:
    return text.encode("ascii", "ignore").decode("ascii")


def send_ntfy(topic: str, title: str, message: str, server: str, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] push -> {title}\n{message}\n")
        return
    req = urllib.request.Request(
        f"{server.rstrip('/')}/{urllib.parse.quote(topic)}",
        data=message.encode("utf-8"),
        method="POST",
        headers={
            "Title": _ascii(title),
            "Priority": "high",
            "Tags": "iphone,white_check_mark",
            "Click": BUY_URL,
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def build_alerts(fresh: dict[str, list[dict]], models: list[dict]) -> list[tuple[str, str]]:
    labels = {m["part"]: m["label"] for m in models}
    alerts = []
    for part, stores in fresh.items():
        title = f"In stock: {labels.get(part, part)}"
        lines = []
        for s in stores:
            where = f"{s['name']}" + (f" ({s['distance']})" if s["distance"] else "")
            lines.append(f"{where}: {s['quote'] or 'Available for pickup'}")
        alerts.append((title, "\n".join(lines)))
    return alerts


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def load_json(path: str | None, default):
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check Apple Store pickup stock for iPhones.")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--prev", default=None, help="Previous status.json (for change detection)")
    ap.add_argument("--out", default="status.json")
    ap.add_argument("--fixture", default=None, help="Read Apple response from a file instead of the network")
    ap.add_argument("--dry-run", action="store_true", help="Print alerts instead of sending them")
    args = ap.parse_args(argv)

    config = load_json(args.config, {})
    models = [m for m in config.get("models", []) if m.get("part") and "REPLACE" not in m["part"]]
    zip_code = os.environ.get("APPLE_ZIP") or str(config.get("zip", ""))
    max_stores = int(config.get("max_stores", 12))
    radius = config.get("radius_miles")
    radius = float(radius) if radius else None
    topic = (os.environ.get("NTFY_TOPIC") or str(config.get("ntfy_topic", ""))).strip()
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
    prev = load_json(args.prev, {})

    status = {
        "radius_miles": radius,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "error": None,
        "models": [],
    }

    try:
        if not models:
            raise RuntimeError("No models configured. Add part numbers to config.json.")
        if not args.fixture and not zip_code:
            raise RuntimeError("No ZIP code. Set the APPLE_ZIP secret or 'zip' in config.json.")
        data = load_json(args.fixture, None) if args.fixture else fetch_apple(
            [m["part"] for m in models], zip_code
        )
        if data is None:
            raise RuntimeError(f"Could not read fixture {args.fixture}")
        status["models"] = parse_response(data, models, max_stores, radius)
    except Exception as e:  # keep the last good data visible on the dashboard
        status["error"] = str(e)
        status["models"] = prev.get("models", [])
        status["last_success_at"] = prev.get("last_success_at") or prev.get("checked_at")
        print(f"ERROR: {e}", file=sys.stderr)
    else:
        status["last_success_at"] = status["checked_at"]
        fresh = new_availability(prev, status["models"])
        for title, message in build_alerts(fresh, status["models"]):
            if topic or args.dry_run:
                try:
                    send_ntfy(topic, title, message, server, args.dry_run)
                except Exception as e:
                    print(f"WARN: push failed: {e}", file=sys.stderr)
            print(f"ALERT: {title}")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2)

    for m in status["models"]:
        n = sum(s["available"] for s in m["stores"])
        print(f"{m['label']}: available at {n}/{len(m['stores'])} stores")
    return 0


if __name__ == "__main__":
    sys.exit(main())
