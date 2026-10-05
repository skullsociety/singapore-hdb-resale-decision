"""Smoke test the Streamlit dashboard against the built local views."""

import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest


APP = Path(__file__).resolve().parents[2] / "dashboard" / "phase_5" / "24_streamlit_app.py"


class DashboardSmokeTests(unittest.TestCase):
    def test_dashboard_opens_without_exception(self):
        app = AppTest.from_file(str(APP), default_timeout=20).run()
        self.assertEqual([], list(app.exception))
        self.assertEqual("Singapore HDB resale decision dashboard", app.title[0].value)

    def test_overview_uses_readable_categories_and_flat_type_summary(self):
        app = AppTest.from_file(str(APP), default_timeout=20).run()
        self.assertEqual([], list(app.exception))
        markdown_values = [element.value for element in app.markdown]
        subheaders = [element.value for element in app.subheader]
        self.assertIn("**What each category means**", markdown_values)
        self.assertIn("Listings by flat type", subheaders)

    def test_listing_details_page_opens_with_comparable_explanation(self):
        app = AppTest.from_file(str(APP), default_timeout=20).run()
        app.radio[0].set_value("Listing details").run(timeout=20)
        self.assertEqual([], list(app.exception))
        self.assertIn("Supporting comparable sales", [element.value for element in app.subheader])
        self.assertIn("How comparable sales are selected", [element.label for element in app.expander])

    def test_quality_page_explains_checks_and_pending_status(self):
        app = AppTest.from_file(str(APP), default_timeout=20).run()
        app.radio[0].set_value("Data and model quality").run(timeout=20)
        self.assertEqual([], list(app.exception))
        captions = [element.value for element in app.caption]
        self.assertTrue(any("Pending means" in caption for caption in captions))

    def test_comparison_report_uses_neutral_labels(self):
        app = AppTest.from_file(str(APP), default_timeout=20).run()
        app.radio[0].set_value("Comparison report").run(timeout=20)
        self.assertEqual([], list(app.exception))
        self.assertEqual("Create a comparison report", app.header[0].value)
        self.assertEqual("Report reference (optional)", app.text_input[0].label)
        self.assertEqual("Comparison notes (optional)", app.text_area[0].label)

    def test_buy_and_sell_planning_page_opens(self):
        app = AppTest.from_file(str(APP), default_timeout=20).run()
        app.radio[0].set_value("Buy and sell planning").run(timeout=20)
        self.assertEqual([], list(app.exception))
        self.assertEqual("Buy and sell planning", app.header[0].value)


if __name__ == "__main__":
    unittest.main()
