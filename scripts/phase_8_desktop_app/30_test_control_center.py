"""Focused tests for the desktop workflow definitions and subprocess runner."""

import importlib.util
import queue
import sys
import tempfile
import time
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("30_control_center.py")
SPEC = importlib.util.spec_from_file_location("control_center", MODULE_PATH)
CONTROL_CENTER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CONTROL_CENTER
SPEC.loader.exec_module(CONTROL_CENTER)


class ControlCenterTests(unittest.TestCase):
    def test_workflow_catalog_uses_token_only_for_onemap(self):
        root = Path(__file__).resolve().parents[2]
        workflows = CONTROL_CENTER.build_workflows(root, force_download=True)
        self.assertIn("-ForceDownload", workflows["data_gov"].commands[0].argv)
        self.assertTrue(workflows["onemap"].commands[0].requires_onemap_token)
        self.assertEqual(9, len(workflows["official_refresh"].commands))
        self.assertEqual(5, len(workflows["dashboard_data"].commands))

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
