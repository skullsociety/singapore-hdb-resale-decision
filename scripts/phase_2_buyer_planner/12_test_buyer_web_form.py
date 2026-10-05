"""HTTP checks that the local form uses the existing Phase 2 calculator."""

import http.client
import importlib.util
import json
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("buyer_web_form", ROOT / "scripts/phase_2_buyer_planner" / "12_buyer_web_form.py")
web_form = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web_form)


class BuyerWebFormTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = web_form.make_server(ROOT, 0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        status, payload = response.status, response.read()
        connection.close()
        return status, payload

    def test_page_and_static_assets(self):
        for path, expected in (("/", b"Compare with your documents"), ("/app.js", b"/calculate"),
                               ("/validation.js", b"compareDocumentFigures"),
                               ("/app.css", b".panel"), ("/notes", b"IRAS")):
            status, body = self.request("GET", path)
            self.assertEqual(status, 200)
            self.assertIn(expected, body)

    def test_example_calculates_using_existing_planner(self):
        example = json.loads((ROOT / "data" / "reference" / "11_buyer_inputs.example.json").read_text(encoding="utf-8"))
        payload = json.dumps(example).encode("utf-8")
        headers = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.server.server_port}"}
        status, body = self.request("POST", "/calculate", payload, headers)
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertEqual(result["scenarios"]["asking"]["total_initial_cash_needed_sgd"], 132220)
        self.assertEqual(result["scenarios"]["asking"]["monthly_housing_outflow_sgd"], 2273.46)

    def test_bad_input_and_foreign_origin_are_rejected(self):
        headers = {"Content-Type": "application/json"}
        status, body = self.request("POST", "/calculate", b"{}", headers)
        self.assertEqual(status, 400)
        self.assertIn(b"loan_type", body)
        status, _ = self.request("POST", "/calculate", b"{}", headers | {"Origin": "https://example.org"})
        self.assertEqual(status, 403)

    def test_cash_only_plan_needs_no_loan_terms(self):
        inputs = {"asking_price_sgd": 300000, "hdb_value_sgd": 300000,
                  "value_status": "assumed", "loan_type": "none", "absd_rate_pct": 0,
                  "cash_available_sgd": 400000}
        status, body = self.request("POST", "/calculate", json.dumps(inputs).encode("utf-8"),
                                    {"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertEqual(result["scenarios"]["asking"]["monthly_loan_payment_sgd"], 0)


if __name__ == "__main__":
    unittest.main()
