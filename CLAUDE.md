# Project notes for Claude

iPhone in-store pickup stock tracker. Owner gets phone push alerts and checks a
home-screen dashboard. Keep it free to run and dependency-light.

## Architecture
- `checker/check_stock.py`: Python standard library only (no pip installs). Calls
  `https://www.apple.com/shop/retail/pickup-message?pl=true&parts.N=...&location=ZIP`
  (verified working Oct 2026; `/shop/fulfillment-messages` now returns HTTP 541),
  parses `body.stores[].partsAvailability[part].pickupDisplay` ("available"/"unavailable"),
  filters stores by `storedistance` (miles) <= `radius_miles`,
  writes `status.json`, and POSTs to ntfy for stores that newly became available
  (compares against the previous status.json).
- `.github/workflows/check-stock.yml`: runs every 5 min. Reads the previous status
  from the `status` branch, runs the checker, force-pushes a single-commit `status`
  branch containing only `status.json` (keeps main history clean).
- `docs/`: static PWA served by GitHub Pages from `main:/docs`. `index.html` fetches
  `raw.githubusercontent.com/<user>/<repo>/status/status.json`, deriving user/repo
  from the Pages URL (`?src=` overrides, `status.json` fallback for local testing).
- `config.json`: models (`label`, `part`), `zip`, `radius_miles`, `max_stores`,
  optional `ntfy_topic`. Env vars `APPLE_ZIP` / `NTFY_TOPIC` (repo secrets) override.
- Tracking: iPhone Duo 256GB unlocked, Night Sky `MK1F4LL/A`, Star White `MK1E4LL/A`.
  `tests/fixtures/live_sample_nyc.json` is a trimmed real response for reference.

## status.json shape
`{checked_at, last_success_at, error, models:[{part, label, any_available,
stores:[{id, name, city, distance, available, quote}]}]}`
On failure the checker keeps the previous `models` and sets `error`.

## Conventions
- Run `python -m unittest discover tests` before committing; add a test for any
  parsing or alert-logic change. Tests are offline (fixtures built in the test file).
- Dashboard: single HTML file, no build step, no frameworks, works in light and dark
  mode and at phone width. Bump `CACHE` in `docs/sw.js` when changing shell files.
- Alerts must stay transition-based (no repeat pushes while still in stock).
