"""Focused tests for the desktop workflow definitions and subprocess runner."""

import importlib.util
import json
import queue
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace


MODULE_PATH = Path(__file__).with_name("30_control_center.py")
SPEC = importlib.util.spec_from_file_location("control_center", MODULE_PATH)
CONTROL_CENTER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CONTROL_CENTER
SPEC.loader.exec_module(CONTROL_CENTER)


class ControlCenterTests(unittest.TestCase):
    def test_model_performance_report_shows_all_methods_and_released_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reports = root / "reports" / "phase_1_resale_model"
            reports.mkdir(parents=True)
            metrics = []
            for model_id, name, value in (
                ("ML_RIDGE_RESALE_PRICE_V1", "ridge", 40_000),
                ("BASELINE_COMPARABLE_SALES_V1", "comparable", 35_000),
            ):
                for split in ("validation", "test"):
                    metrics.append({
                        "model_id": model_id, "model_name": name, "dataset_split": split,
                        "mae": value, "median_absolute_error": value / 2,
                        "mape_pct": 5.0, "rmse": value * 1.5, "r_squared": 0.8,
                    })
            (reports / "06_model_selection.json").write_text(json.dumps({
                "status": "success", "built_at_utc": "2026-10-08T10:00:00+00:00",
                "selected_model": {"model_id": "ML_RIDGE_RESALE_PRICE_V1"}, "metrics": metrics,
            }), encoding="utf-8")
            (reports / "09_blend_release.json").write_text(json.dumps({
                "status": "national_candidate", "built_at_utc": "2026-10-08T10:01:00+00:00",
                "model_id": "BASELINE_COMPARABLE_SALES_V1", "baseline_gate": {
                    "model_id": "ML_RIDGE_RESALE_PRICE_V1"},
            }), encoding="utf-8")
            release, rows = CONTROL_CENTER.model_performance_report(root)
            self.assertEqual(release["model_id"], rows[0]["model_id"])
            self.assertEqual(2, len(rows))
            self.assertTrue(rows[0]["released"])
            self.assertFalse(rows[1]["released"])
            self.assertEqual(40_000, rows[1]["test_mae"])

    def test_model_workflow_completion_opens_performance_table(self):
        events: queue.Queue[tuple[str, str]] = queue.Queue()
        events.put(("finished", "4. Run modelling pipeline completed"))
        shown = []
        statuses = []
        center = SimpleNamespace(
            runner=SimpleNamespace(events=events),
            root=SimpleNamespace(after=lambda *_: None),
            status=SimpleNamespace(set=statuses.append),
            _active_workflow_key="model_pipeline",
            _set_running=lambda *_: None,
            _write_log=lambda *_: None,
            _poll_events=lambda: None,
            _show_model_performance=lambda: shown.append(True) or {
                "model_id": "ML_CATBOOST_RESALE_PRICE_V1",
                "model_name": "catboost_resale_price_v1",
                "selection_reason": "Lower error on both periods.",
            },
        )
        CONTROL_CENTER.ControlCenter._poll_events(center)
        self.assertEqual([True], shown)
        self.assertIn("CatBoost regression", statuses[-1])
        self.assertIsNone(center._active_workflow_key)

    def test_workflow_catalog_uses_token_only_for_onemap(self):
        root = Path(__file__).resolve().parents[2]
        workflows = CONTROL_CENTER.build_workflows(root, force_download=True)
        self.assertIn("-ForceDownload", workflows["data_gov"].commands[0].argv)
        self.assertTrue(workflows["onemap"].commands[0].requires_onemap_token)
        self.assertEqual(8, len(workflows["official_refresh"].commands))
        self.assertEqual(5, len(workflows["dashboard_data"].commands))

    def test_model_pipeline_runs_diagnostics_after_training(self):
        root = Path(__file__).resolve().parents[2]
        commands = CONTROL_CENTER.build_workflows(root)["model_pipeline"].commands
        self.assertIn("06_run_resale_models.ps1", commands[2].argv[-1])
        self.assertIn("07_run_phase1_diagnostics.ps1", commands[3].argv[-1])
        self.assertIn("09_run_blend_calibration.ps1", commands[4].argv[-1])
        self.assertFalse(any("08_run_hybrid_experiment.ps1" in command.argv[-1] for command in commands))

    def test_review_button_uses_current_model_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reports = root / "reports" / "phase_1_resale_model"
            reports.mkdir(parents=True)
            selection = reports / "06_model_selection.json"
            review = reports / "07_phase1_exit_review.json"
            selection.write_text(json.dumps({
                "built_at_utc": "2026-10-07T10:00:00+00:00",
                "selected_model": {"model_id": "ML_CATBOOST_QUANTILE_P50_V1"},
            }), encoding="utf-8")
            review.write_text(json.dumps({
                "reviewed_at_utc": "2026-10-07T10:30:00+00:00",
                "selected_model_id": "ML_CATBOOST_QUANTILE_P50_V1",
            }), encoding="utf-8")
            self.assertEqual(review, CONTROL_CENTER.diagnostics_review_path(root))
            selection.write_text(json.dumps({
                "built_at_utc": "2026-10-07T11:00:00+00:00",
                "selected_model": {"model_id": "ML_CATBOOST_QUANTILE_P50_V1"},
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "earlier training run"):
                CONTROL_CENTER.diagnostics_review_path(root)

    def test_mouse_wheel_scrolls_only_upper_controls(self):
        class Widget:
            def __init__(self, master=None):
                self.master = master

        class Canvas(Widget):
            def __init__(self):
                super().__init__()
                self.moves = []

            def yview_scroll(self, steps, units):
                self.moves.append((steps, units))

        canvas = Canvas()
        controls_button = Widget(master=canvas)
        log_widget = Widget()
        hovered = [controls_button]
        center = CONTROL_CENTER.ControlCenter.__new__(CONTROL_CENTER.ControlCenter)
        center.controls_canvas = canvas
        center.root = SimpleNamespace(winfo_containing=lambda *_: hovered[0])
        event = SimpleNamespace(x_root=0, y_root=0, delta=-120)
        self.assertEqual("break", center._scroll_controls(event))
        self.assertEqual([(1, "units")], canvas.moves)
        hovered[0] = log_widget
        self.assertIsNone(center._scroll_controls(event))
        self.assertEqual([(1, "units")], canvas.moves)

    def test_runner_executes_commands_in_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = CONTROL_CENTER.CommandSpec(
                "Print marker", (sys.executable, "-c", "print('runner-ok')"),
            )
            workflow = CONTROL_CENTER.WorkflowSpec(
                "test", "Test workflow", "", (command,),
            )
            runner = CONTROL_CENTER.WorkflowRunner(root)
            runner.start(workflow)
            deadline = time.time() + 10
            while runner.running and time.time() < deadline:
                time.sleep(0.02)
            events = []
            while True:
                try:
                    events.append(runner.events.get_nowait())
                except queue.Empty:
                    break
            self.assertIn(("log", "runner-ok"), events)
            self.assertTrue(any(event == "finished" for event, _ in events))

    def test_runner_requires_token_before_starting(self):
        command = CONTROL_CENTER.CommandSpec(
            "Token task", (sys.executable, "-c", "pass"), True,
        )
        workflow = CONTROL_CENTER.WorkflowSpec("token", "Token", "", (command,))
        runner = CONTROL_CENTER.WorkflowRunner(Path.cwd())
        with self.assertRaisesRegex(ValueError, "OneMap"):
            runner.start(workflow)

    def test_onemap_help_uses_official_https_pages(self):
        self.assertEqual(
            "https://www.onemap.gov.sg/apidocs/register",
            CONTROL_CENTER.ONEMAP_REGISTRATION_URL,
        )
        self.assertEqual(
            "https://www.onemap.gov.sg/apidocs/authentication",
            CONTROL_CENTER.ONEMAP_AUTHENTICATION_URL,
        )
        self.assertEqual(
            "https://www.onemap.gov.sg/api/auth/post/getToken",
            CONTROL_CENTER.ONEMAP_TOKEN_URL,
        )

    def test_listing_source_links_use_nationwide_hdb_result_pages(self):
        self.assertEqual(
            "https://www.propertyguru.com.sg/hdb-for-sale",
            CONTROL_CENTER.PROPERTYGURU_HDB_URL,
        )
        self.assertEqual(
            "https://www.99.co/singapore/sale/hdb",
            CONTROL_CENTER.NINETY_NINE_HDB_URL,
        )
        self.assertEqual(
            "https://www.srx.com.sg/singapore-property-listings/hdb-for-sale",
            CONTROL_CENTER.SRX_HDB_URL,
        )

    def test_closing_dashboard_window_stops_dashboard_process(self):
        class FakeProcess:
            def __init__(self):
                self.return_code = None

            def poll(self):
                return self.return_code

            def wait(self, timeout=None):
                self.return_code = 0
                return self.return_code

            def terminate(self):
                self.return_code = 0

            def kill(self):
                self.return_code = 0

        events: queue.Queue[tuple[str, str]] = queue.Queue()
        dashboard = CONTROL_CENTER.DashboardProcess(Path.cwd(), events)
        dashboard.process = FakeProcess()
        browser = FakeProcess()
        dashboard.browser_process = browser

        dashboard._stop_when_window_closes(browser)

        self.assertFalse(dashboard.running)
        self.assertIsNone(dashboard.browser_process)
        event_messages = [events.get_nowait()[1] for _ in range(events.qsize())]
        self.assertIn("Dashboard window closed; dashboard stopped", event_messages)


if __name__ == "__main__":
    unittest.main()
