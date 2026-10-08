"""Checks both automatic release outcomes and baseline listing valuation."""

from __future__ import annotations

import importlib.util
import json
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


calibration = load_script("release_calibration_test", ROOT / "scripts/phase_1_resale_model/09_calibrate_accepted_blend.py")
listings = load_script("listing_valuation_test", ROOT / "scripts/phase_4_market_watch/17_19_build_listing_analysis.py")


class ReleaseSelectionTests(unittest.TestCase):
    def test_ml_wins_only_when_it_beats_baseline_on_both_splits(self) -> None:
        model_id, baseline_id = "ML_TEST", "BASELINE_TEST"
        keys = [("validation", "V"), ("test", "T")]
        baseline = {key: {"predicted_price": 90, "actual_price": 100} for key in keys}
        model = {key: {"predicted_price": 95, "actual_price": 100} for key in keys}
        gate = calibration.comparable_release_gate(model_id, model, baseline, baseline_id)
        self.assertTrue(gate["passed"])
        self.assertEqual(calibration.choose_release_method(gate, model_id, baseline_id)[0], model_id)

        model[("test", "T")]["predicted_price"] = 80
        gate = calibration.comparable_release_gate(model_id, model, baseline, baseline_id)
        self.assertFalse(gate["passed"])
        self.assertEqual(calibration.choose_release_method(gate, model_id, baseline_id)[0], baseline_id)

    def test_listing_uses_baseline_without_loading_ml_artifact(self) -> None:
        month = date.today().replace(day=1)
        release = {
            "status": "national_candidate", "model_id": "BASELINE_COMPARABLE_SALES_V1",
            "comparable_method": "BASELINE_COMPARABLE_SALES_V1", "model_name": "comparable_sales_v1",
            "model_artifact": None, "feature_columns": [], "source_build_run_id": "RUN_TEST",
            "residual_offsets_sgd": {"95": [-50_000, 50_000], "97.5": [-70_000, 70_000]},
        }
        baseline = SimpleNamespace(
            ComparableHistory=lambda: SimpleNamespace(add=lambda records: None),
            select_comparables=lambda source, history: ([{
                "transaction_id": "OLD", "block_id": "B1", "transaction_month": date(2024, 1, 1),
                "resale_price": 550_000, "floor_area_sqm": 90.0, "storey_midpoint": 8.0,
                "storey_range": "07 TO 09",
            }], "town_flat_type_fallback"),
            comparable_price_prediction=lambda method, target_month, records: (600_000, 550_000, 650_000),
            distance_m=lambda source, record: 0,
        )
        trainer = SimpleNamespace(
            COMPARABLE_FEATURE="comparable_price_sgd",
            baseline_aware_features=lambda *args: self.fail("ML features should not be built"),
            predict_with_artifact=lambda *args: self.fail("ML artifact should not be loaded"),
        )
        block = {"block_id": "B1", "block": "1", "street": "TEST ROAD", "town": "TEST",
                 "year_completed": 2000}
        feature = {"listing_key": "L1", "source_site": "TEST", "run_id": "R1", "listing_id": "L1",
                   "match_status": "matched", "inferred_flat_type": "4 ROOM", "block_id": "B1",
                   "floor_area_sqm": 90.0, "asking_price_sgd": 630_000}
        sale = {"block_id": "B1", "town": "TEST", "flat_type": "4 ROOM", "transaction_month": date(2024, 1, 1),
                "dataset_split": "train", "resale_price": 550_000}
        scenario = {"scenario": "middle", "storey_range": "07 TO 09", "storey_midpoint": 8,
                    "flat_model": "Model A", "lease_commence_year": 2000}

        def fake_module(path, name):
            return trainer if path.name == "06_train_resale_models.py" else baseline

        with (patch.object(listings, "load_module", side_effect=fake_module),
              patch.object(listings, "scenario_inputs", return_value=([scenario], {"lease_commence_year": 2000})),
              patch.object(Path, "read_text", return_value=json.dumps(release))):
            result = listings.valuation_rows(
                ROOT, [feature], {"B1": block}, [sale],
                {"pipeline_run_id": "RUN_TEST", "latest_month": month},
            )[0]
        self.assertEqual(result["model_id"], release["model_id"])
        self.assertEqual(result["valuation_status"], "estimated")
        self.assertEqual(result["point_estimate_sgd"], 600_000)
        self.assertEqual(result["asking_premium_discount_sgd"], 30_000)


if __name__ == "__main__":
    unittest.main()
