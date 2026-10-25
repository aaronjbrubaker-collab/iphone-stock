# Project notes for Claude

iPhone in-store pickup stock tracker. Owner gets phone push alerts and checks a
home-screen dashboard. Keep it free to run and dependency-light.

## Architecture
- `checker/check_stock.py`: Python standard library only (no pip installs). Calls
  `https://www.apple.com/shop/retail/pickup-message?pl=true&parts.N=...&location=ZIP`
  (verified working Oct 2026; `/shop/fulfillment-messages` now returns HTTP 541),
  parses `body.stores[].partsAvailability[part].pickupDisplay` ("available"/"unavailable"),
  filters stores by `storedistance` (miles) <= `radius_miles`,
  writes `status.json` + `history.json` (restock "in" / sell-out "out" events, last 500),
  and POSTs an urgent ntfy push (Click = that color's Apple buy page) for stores that
  newly became available (compares against the previous status.json).
  `--test-alert` sends a test push and exits.
- `.github/workflows/check-stock.yml`: runs every 5 min. Reads the previous status
  from the `status` branch, runs the checker, force-pushes a single-commit `status`
  branch containing `status.json` + `history.json` (keeps main history clean).
- `.github/workflows/test-alert.yml`: manual "Send test alert" workflow.
- `docs/`: static PWA "Duo Watch" served by GitHub Pages from `main:/docs`. Tabs:
  Stock (store cards, one row per color), Recent changes, Patterns (restocks by hour /
  store). Fetches `status.json` + `history.json` from
  `raw.githubusercontent.com/<user>/<repo>/status/`, deriving user/repo from the Pages
  URL (`?src=<url>` sets the folder, `./` fallback for local testing). Has a
  "Send test push" box; the ntfy topic is stored in the viewer's localStorage only.
- `config.json`: models (`label`, `color`, `part`, `url`), `zip`, `radius_miles`, `max_stores`,
  optional `ntfy_topic`. Env vars `APPLE_ZIP` / `NTFY_TOPIC` (repo secrets) override.
- Tracking: iPhone Duo 256GB unlocked, Night Sky `MK1F4LL/A`, Star White `MK1E4LL/A`.
  `tests/fixtures/live_sample_nyc.json` is a trimmed real response for reference.

## status.json shape
`{radius_miles, checked_at, last_success_at, error, models:[{part, label, color, url,
any_available, stores:[{id, name, city, distance, miles, available, quote, address,
phone, store_url, maps_url}]}]}`. The public repo must never contain the ZIP or topic.
On failure the checker keeps the previous `models` and sets `error`.

## Conventions
- Run `python -m unittest discover tests` before committing; add a test for any
  parsing or alert-logic change. Tests are offline (fixtures built in the test file).
- Dashboard: single HTML file, no build step, no frameworks, works in light and dark
  mode and at phone width. Bump `CACHE` in `docs/sw.js` when changing shell files.
- Alerts must stay transition-based (no repeat pushes while still in stock).
- No auto-checkout: purchases stay manual (owner taps the alert, buys with Apple Pay).
