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
        self.assertEqual("To investigate low price", app.metric[2].label)
        category_names = app.table[0].value["Category"].tolist()
        self.assertIn("To investigate low price", category_names)
        self.assertIn("Asking below estimate", category_names)
        self.assertIn("Slightly expensive", category_names)

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

    def test_seller_can_get_released_model_estimate(self):
        app = AppTest.from_file(str(APP), default_timeout=20).run()
        app.radio[0].set_value("Estimate my flat").run(timeout=20)
        app.text_input[2].set_value("338A")
        app.text_input[3].set_value("ANCHORVALE CRES")
        app.selectbox[0].set_value("4 ROOM")
        app.text_input[1].set_value("93")
        app.selectbox[1].set_value("13 TO 15")
        app.button[0].click().run(timeout=20)
        self.assertEqual([], list(app.exception))
        self.assertEqual("Estimated selling price", app.metric[0].label)
        self.assertTrue(app.metric[0].value.startswith("S$"))
        self.assertTrue(app.metric[1].value.startswith("S$"))
        self.assertTrue(app.metric[2].value.startswith("S$"))

    def test_postal_code_autofills_address_and_suggested_size(self):
        app = AppTest.from_file(str(APP), default_timeout=20).run()
        app.radio[0].set_value("Estimate my flat").run(timeout=20)
        app.text_input[0].set_value("510144").run(timeout=20)
        self.assertEqual([], list(app.exception))
        self.assertEqual("144", app.text_input[2].value)
        self.assertEqual("PASIR RIS ST 11", app.text_input[3].value)
        self.assertEqual("PASIR RIS", app.text_input[4].value)
        self.assertEqual("105", app.text_input[1].value)
        self.assertEqual("4 ROOM", app.selectbox[0].value)
        self.assertIn("not this unit's confirmed size", app.info[0].value)
        app.button[0].click().run(timeout=20)
        inferred = app.session_state["seller_flat_estimate"]["input_sources"]
        self.assertIn("flat_type", inferred)
        self.assertIn("floor_area_sqm", inferred)

    def test_block_without_resales_and_without_floor_gets_estimate(self):
        app = AppTest.from_file(str(APP), default_timeout=20).run()
        app.radio[0].set_value("Estimate my flat").run(timeout=20)
        app.text_input[0].set_value("541174").run(timeout=20)
        app.button[0].click().run(timeout=20)
        self.assertEqual([], list(app.exception))
        self.assertEqual("Estimated selling price", app.metric[0].label)
        self.assertTrue(app.metric[0].value.startswith("S$"))
        self.assertEqual(0, app.session_state["seller_flat_estimate"]["block_sales_count"])
        self.assertIsNone(app.session_state["seller_flat_estimate"]["nominal_range_coverage_pct"])

    def test_postal_and_blk_prefixed_address_agree(self):
        app = AppTest.from_file(str(APP), default_timeout=20).run()
        app.radio[0].set_value("Estimate my flat").run(timeout=20)
        app.text_input[0].set_value("510144").run(timeout=20)
        app.text_input[2].set_value("Blk 144")
        app.text_input[1].set_value("")
        app.selectbox[1].set_value("07 TO 09")
        app.button[0].click().run(timeout=20)
        self.assertEqual([], list(app.exception))
        self.assertEqual("Estimated selling price", app.metric[0].label)
        self.assertEqual("144", app.session_state["seller_flat_estimate"]["subject"]["block"])

    def test_no_details_shows_broad_reference_not_model_price(self):
        app = AppTest.from_file(str(APP), default_timeout=20).run()
        app.radio[0].set_value("Estimate my flat").run(timeout=20)
        app.button[0].click().run(timeout=20)
        self.assertEqual([], list(app.exception))
        self.assertEqual("Recent-sale median", app.metric[0].label)


if __name__ == "__main__":
    unittest.main()
