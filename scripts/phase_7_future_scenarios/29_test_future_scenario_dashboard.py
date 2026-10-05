"""Smoke test the Phase 7 page in the local Streamlit dashboard."""

import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest


APP = Path(__file__).resolve().parents[2] / "dashboard" / "phase_5" / "24_streamlit_app.py"


class FutureScenarioDashboardTests(unittest.TestCase):
    def test_future_scenario_page_opens(self):
        app = AppTest.from_file(str(APP), default_timeout=30).run()
        app.radio[0].set_value("Future-price scenarios").run(timeout=30)
        self.assertEqual([], list(app.exception))
        self.assertEqual("Future-price scenario explorer", app.header[0].value)
        self.assertIn("Historical backtest", [element.value for element in app.subheader])
        self.assertIn("Annual market growth (%)", [item.label for item in app.number_input])
        self.assertIn("Loan interest rate (%)", [item.label for item in app.number_input])
        self.assertIn("Lines to display", [item.label for item in app.selectbox])
        self.assertIn(
            "How the shaded range is calculated",
            [item.label for item in app.expander],
        )


if __name__ == "__main__":
    unittest.main()
