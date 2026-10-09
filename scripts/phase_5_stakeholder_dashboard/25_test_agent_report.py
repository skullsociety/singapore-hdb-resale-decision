"""Focused tests for the local Phase 5 comparison report."""

import importlib.util
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path


PATH = Path(__file__).resolve().parents[2] / "dashboard" / "phase_5" / "25_agent_report.py"
SPEC = importlib.util.spec_from_file_location("agent_report", PATH)
REPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORT)


class AgentReportTests(unittest.TestCase):
    def listing(self):
        return {
            "listing_id": "123", "title": "Test flat", "address": "1 Test Street",
            "stakeholder_category": "strong_candidate", "asking_price_sgd": 600000,
            "lower_estimate_sgd": 590000, "point_estimate_sgd": 620000,
            "upper_estimate_sgd": 650000, "asking_premium_discount_pct": -3.2,
            "inferred_flat_type": "4 ROOM", "floor_area_sqm": 90,
            "minimum_comparable_count": 5, "confidence_label": "scenario_range",
            "category_reason": "Supported price comparison.", "verify_low_price": False,
            "listing_url": "https://example.com/listing/123",
        }

    def test_report_is_printable_and_escapes_user_text(self):
        content = REPORT.build_report(
            [self.listing()], {}, report_reference="A&B", comparison_notes="<script>",
            generated_at=datetime(2026, 10, 2, tzinfo=timezone.utc),
        ).decode("utf-8")
        self.assertIn("A&amp;B", content)
        self.assertIn("&lt;script&gt;", content)
        self.assertNotIn("<script>", content)
        self.assertIn("@media print", content)
        self.assertIn("<dt>Category</dt><dd>Good price</dd>", content)

    def test_report_uses_slightly_expensive_label(self):
        listing = self.listing()
        listing["stakeholder_category"] = "negotiation_candidate"
        content = REPORT.build_report([listing], {}).decode("utf-8")
        self.assertIn("<dt>Category</dt><dd>Slightly expensive</dd>", content)

    def test_report_shows_limited_local_training_evidence(self):
        listing = self.listing()
        listing["recent_town_flat_training_sales"] = 80
        listing["valuation_note"] = "Limited local evidence; a wider range is used."
        content = REPORT.build_report([listing], {}).decode("utf-8")
        self.assertIn("<dt>Recent local training sales</dt><dd>80</dd>", content)
        self.assertIn("Limited local evidence; a wider range is used.", content)

    def test_report_limits_comparison_to_four(self):
        with self.assertRaises(ValueError):
            REPORT.build_report([self.listing()] * 5, {})

    def test_planning_upload_must_be_json_object(self):
        self.assertEqual({"status": "planning_estimate"}, REPORT.parse_planning_upload(json.dumps({"status": "planning_estimate"}).encode()))
        with self.assertRaises(ValueError):
            REPORT.parse_planning_upload(b"[]")


if __name__ == "__main__":
    unittest.main()
