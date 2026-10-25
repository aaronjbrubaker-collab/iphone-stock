"""Offline tests: run with  python -m unittest discover tests"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "checker"))
import check_stock as cs  # noqa: E402

MODELS = [
    {"label": "Test Phone Black", "part": "TEST1LL/A"},
    {"label": "Test Phone White", "part": "TEST2LL/A"},
]


def store(num, name, city, dist, a1, a2):
    def avail(flag):
        return {
            "pickupDisplay": "available" if flag else "unavailable",
            "pickupSearchQuote": "Available Today" if flag else "Currently unavailable",
        }
    return {
        "storeNumber": num, "storeName": name, "city": city, "state": "NJ",
        "storedistance": float(dist.split()[0]), "storeDistanceWithUnit": dist,
        "partsAvailability": {"TEST1LL/A": avail(a1), "TEST2LL/A": avail(a2)},
    }


def response(*stores):
    # Current shape of /shop/retail/pickup-message (verified live Oct 2026)
    return {"head": {"status": "200"}, "body": {"stores": list(stores)}}


def legacy_response(*stores):
    # Older /shop/fulfillment-messages shape
    return {"body": {"content": {"pickupMessage": {"stores": list(stores)}}}}


SOLD_OUT = response(
    store("R001", "Market Street", "Springfield", "4.2 mi", False, False),
    store("R002", "Riverside Mall", "Shelbyville", "11.0 mi", False, False),
)
RESTOCK = response(
    store("R001", "Market Street", "Springfield", "4.2 mi", True, False),
    store("R002", "Riverside Mall", "Shelbyville", "11.0 mi", False, False),
)


class ParseTests(unittest.TestCase):
    def test_parse_marks_availability(self):
        models = cs.parse_response(RESTOCK, MODELS, max_stores=8)
        self.assertTrue(models[0]["any_available"])
        self.assertFalse(models[1]["any_available"])
        self.assertEqual(models[0]["stores"][0]["quote"], "Available Today")

    def test_max_stores_limits_rows(self):
        models = cs.parse_response(RESTOCK, MODELS, max_stores=1)
        self.assertEqual(len(models[0]["stores"]), 1)

    def test_radius_filters_far_stores(self):
        models = cs.parse_response(RESTOCK, MODELS, max_stores=12, radius_miles=10)
        self.assertEqual([st["id"] for st in models[0]["stores"]], ["R001"])

    def test_legacy_shape_still_parses(self):
        data = legacy_response(store("R001", "Market Street", "Springfield", "4.2 mi", True, False))
        self.assertTrue(cs.parse_response(data, MODELS, 8)[0]["any_available"])

    def test_city_includes_state(self):
        self.assertEqual(cs.parse_response(RESTOCK, MODELS, 8)[0]["stores"][0]["city"], "Springfield, NJ")

    def test_apple_error_raises(self):
        bad = {"body": {"content": {"pickupMessage": {"errorMessage": "Invalid part"}}}}
        with self.assertRaises(RuntimeError):
            cs.parse_response(bad, MODELS, 8)

    def test_url_contains_parts_and_zip(self):
        url = cs.build_url(["A1/A", "B2/A"], "12345")
        self.assertIn("parts.0=A1%2FA", url)
        self.assertIn("parts.1=B2%2FA", url)
        self.assertIn("location=12345", url)
        self.assertIn("/shop/retail/pickup-message?", url)


class AlertTests(unittest.TestCase):
    def test_alert_only_on_transition(self):
        prev = {"models": cs.parse_response(SOLD_OUT, MODELS, 8)}
        now = cs.parse_response(RESTOCK, MODELS, 8)
        fresh = cs.new_availability(prev, now)
        self.assertEqual(list(fresh), ["TEST1LL/A"])
        self.assertEqual(fresh["TEST1LL/A"][0]["id"], "R001")

    def test_no_repeat_alert_when_still_available(self):
        prev = {"models": cs.parse_response(RESTOCK, MODELS, 8)}
        now = cs.parse_response(RESTOCK, MODELS, 8)
        self.assertEqual(cs.new_availability(prev, now), {})

    def test_alert_text(self):
        prev = {"models": cs.parse_response(SOLD_OUT, MODELS, 8)}
        now = cs.parse_response(RESTOCK, MODELS, 8)
        alerts = cs.build_alerts(cs.new_availability(prev, now), now)
        self.assertEqual(alerts[0][0], "In stock: Test Phone Black")
        self.assertIn("Market Street (4.2 mi): Available Today", alerts[0][1])


class EndToEndTests(unittest.TestCase):
    def run_main(self, tmp, fixture, prev=None):
        cfg = os.path.join(tmp, "config.json")
        fx = os.path.join(tmp, "fixture.json")
        out = os.path.join(tmp, "status.json")
        with open(cfg, "w") as f:
            json.dump({"zip": "00000", "max_stores": 8, "models": MODELS}, f)
        with open(fx, "w") as f:
            json.dump(fixture, f)
        argv = ["--config", cfg, "--fixture", fx, "--out", out, "--dry-run"]
        if prev:
            argv += ["--prev", prev]
        self.assertEqual(cs.main(argv), 0)
        with open(out) as f:
            return json.load(f), out

    def test_two_checks_produce_one_alert(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, out1 = self.run_main(tmp, SOLD_OUT)
            self.assertIsNone(first["error"])
            prev = os.path.join(tmp, "prev.json")
            os.replace(out1, prev)
            second, _ = self.run_main(tmp, RESTOCK, prev=prev)
            self.assertTrue(second["models"][0]["any_available"])

    def test_failure_keeps_last_good_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, out1 = self.run_main(tmp, RESTOCK)
            prev = os.path.join(tmp, "prev.json")
            os.replace(out1, prev)
            bad = {"body": {"content": {"pickupMessage": {"errorMessage": "Blocked"}}}}
            second, _ = self.run_main(tmp, bad, prev=prev)
            self.assertIn("Blocked", second["error"])
            self.assertEqual(second["models"], first["models"])
            self.assertEqual(second["last_success_at"], first["checked_at"])


if __name__ == "__main__":
    unittest.main()
