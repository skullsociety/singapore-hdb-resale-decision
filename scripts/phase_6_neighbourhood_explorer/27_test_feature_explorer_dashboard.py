"""Smoke test the Phase 6 explorer page in the local dashboard."""

import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest


APP = Path(__file__).resolve().parents[2] / "dashboard" / "phase_5" / "24_streamlit_app.py"


class FeatureExplorerDashboardTests(unittest.TestCase):
    def test_feature_explorer_opens_without_exception(self):
        app = AppTest.from_file(str(APP), default_timeout=30).run()
        app.radio[0].set_value("Neighbourhood and features").run(timeout=30)
        self.assertEqual([], list(app.exception))
        self.assertEqual("Neighbourhood and feature explorer", app.header[0].value)
        self.assertEqual(["From year", "To year"], [item.label for item in app.selectbox[:2]])
        self.assertEqual("Map and scatter transaction dates", app.date_input[0].label)
        self.assertIn(
            "Selected comparison group",
            [element.value for element in app.subheader],
        )
        self.assertIn(
            "Average cost per sqm by flat type",
            [element.value for element in app.subheader],
        )
        self.assertTrue(any(
            "deterministic display sample of up to 8,000" in element.value
            for element in app.caption
        ))
        self.assertEqual(500, len(app.dataframe[-1].value))


if __name__ == "__main__":
    unittest.main()
