"""Checks for comparable pricing and its month boundary."""

from __future__ import annotations

import importlib.util
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str, filename: str):
    path = ROOT / "scripts/phase_1_resale_model" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


baseline = load_script("baseline_under_test", "04_build_baselines.py")
trainer = load_script("trainer_under_test", "06_train_resale_models.py")


def sale(key: str, month: date, price: float) -> dict:
    return {
        "transaction_id": key, "transaction_month": month, "resale_price": price,
        "town": "T", "flat_type": "4 ROOM", "flat_model": "M", "block_id": "B",
        "floor_area_sqm": 90.0, "storey_midpoint": 6.0,
        "latitude": 1.3, "longitude": 103.8,
    }


class ComparableBaselineTests(unittest.TestCase):
    def test_recent_sale_has_more_weight_without_using_same_month(self) -> None:
        earlier = [sale("old", date(2024, 1, 1), 500_000), sale("recent", date(2024, 9, 1), 600_000)]
        self.assertEqual(baseline.median_prediction(earlier)[0], 550_000)
        self.assertEqual(baseline.recency_weighted_prediction(date(2024, 10, 1), earlier)[0], 600_000)
        with self.assertRaises(ValueError):
            baseline.recency_weighted_prediction(date(2024, 9, 1), earlier)

    def test_training_targets_cannot_use_each_other_in_same_month(self) -> None:
        records = [
            sale("old", date(2024, 1, 1), 500_000),
            sale("recent", date(2024, 9, 1), 600_000),
            sale("first", date(2024, 10, 1), 650_000),
            sale("second", date(2024, 10, 1), 999_000),
        ]
        anchors = trainer.historical_training_anchors(
            ROOT, records, "BASELINE_COMPARABLE_RECENCY_WEIGHTED_V1",
        )
        self.assertNotIn("old", anchors)
        self.assertEqual(anchors["first"], 600_000)
        self.assertEqual(anchors["second"], 600_000)

    def test_baseline_choice_uses_validation_only(self) -> None:
        plain, weighted = baseline.COMPARABLE_MODEL_IDS
        metrics = [
            {"model_id": plain, "dataset_split": "validation", "mean_absolute_error": 50},
            {"model_id": weighted, "dataset_split": "validation", "mean_absolute_error": 40},
            {"model_id": plain, "dataset_split": "test", "mean_absolute_error": 1},
            {"model_id": weighted, "dataset_split": "test", "mean_absolute_error": 100},
        ]
        self.assertEqual(baseline.select_comparable_model(metrics), weighted)


if __name__ == "__main__":
    unittest.main()
