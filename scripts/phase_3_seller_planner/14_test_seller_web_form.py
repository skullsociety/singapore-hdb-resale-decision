"""HTTP checks for the seller page and the step 13 calculator."""
import http.client
import importlib.util
import json
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("local_form", ROOT / "scripts/phase_2_buyer_planner" / "12_buyer_web_form.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class SellerWebFormTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = app.make_server(ROOT, 0)
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
        result = response.status, response.read()
        connection.close()
        return result

    def test_seller_page_and_assets(self):
        for route, marker in (("/seller", b"HDB Seller Proceeds Planner"),
                              ("/seller.js", b"/seller/calculate"),
                              ("/seller.css", b".seller-note"),
                              ("/seller-notes", b"CPF refund")):
            status, body = self.request("GET", route)
            self.assertEqual(status, 200)
            self.assertIn(marker, body)

    def test_seller_calculation_from_example(self):
        example = json.loads((ROOT / "data/reference/13_seller_inputs.example.json").read_text(encoding="utf-8"))
        payload = json.dumps(example).encode("utf-8")
        status, body = self.request("POST", "/seller/calculate", payload,
                                    {"Content-Type": "application/json",
                                     "Origin": f"http://127.0.0.1:{self.server.server_port}"})
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertEqual(result["scenarios"]["expected"]["estimated_cash_proceeds_sgd"], 236000)
        self.assertEqual(result["scenarios"]["expected"]["cpf_refund_from_sale_sgd"], 150000)

    def test_bad_input_and_foreign_origin(self):
        status, body = self.request("POST", "/seller/calculate", b"{}",
                                    {"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        self.assertIn(b"expected_sale_price_sgd", body)
        status, _ = self.request("POST", "/seller/calculate", b"{}",
                                 {"Content-Type": "application/json",
                                  "Origin": "https://example.org"})
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
