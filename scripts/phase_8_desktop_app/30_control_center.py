"""Windows desktop control centre for the local PropertyProject workflow."""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import json
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import BooleanVar, Canvas, END, PanedWindow, StringVar, Tk, Toplevel, messagebox
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText


CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
DASHBOARD_URL = "http://127.0.0.1:8501"
ONEMAP_REGISTRATION_URL = "https://www.onemap.gov.sg/apidocs/register"
ONEMAP_AUTHENTICATION_URL = "https://www.onemap.gov.sg/apidocs/authentication"
ONEMAP_TOKEN_URL = "https://www.onemap.gov.sg/api/auth/post/getToken"
PROPERTYGURU_HDB_URL = "https://www.propertyguru.com.sg/hdb-for-sale"
NINETY_NINE_HDB_URL = "https://www.99.co/singapore/sale/hdb"
SRX_HDB_URL = "https://www.srx.com.sg/singapore-property-listings/hdb-for-sale"
MODEL_LABELS = {
    "ML_RIDGE_RESALE_PRICE_V1": "Ridge regression",
    "ML_GRADIENT_BOOSTING_RESALE_PRICE_V1": "Huber gradient boosting",
    "ML_SKLEARN_QUANTILE_P50_V1": "Histogram boosting quantiles (P50)",
    "ML_RANDOM_FOREST_SQUARED_ERROR_V1": "Random forest (squared error)",
    "ML_CATBOOST_RESALE_PRICE_V1": "CatBoost regression",
    "ML_CATBOOST_QUANTILE_P50_V1": "CatBoost quantiles (P50)",
    "BASELINE_RECENT_FLAT_TYPE_24M_V1": "Town / flat-type median",
    "BASELINE_COMPARABLE_SALES_V1": "Comparable-sales median",
    "BASELINE_COMPARABLE_RECENCY_WEIGHTED_V1": "Recency-weighted comparable median",
}


@dataclass(frozen=True)
class CommandSpec:
    label: str
    argv: tuple[str, ...]
    requires_onemap_token: bool = False


@dataclass(frozen=True)
class WorkflowSpec:
    key: str
    label: str
    description: str
    commands: tuple[CommandSpec, ...]


def is_project_root(path: Path) -> bool:
    return (path / "README.md").is_file() and (path / "scripts").is_dir()


def find_project_root() -> Path:
    """Find the source checkout when run from Python or a packaged executable."""
    candidates: list[Path] = []
    configured = os.environ.get("PROPERTY_PROJECT_ROOT")
    if configured:
        candidates.append(Path(configured))
    candidates.extend([Path.cwd(), Path(sys.executable).resolve().parent])
    if not getattr(sys, "frozen", False):
        candidates.append(Path(__file__).resolve().parents[2])

    visited: set[Path] = set()
    for candidate in candidates:
        for path in (candidate.resolve(), *candidate.resolve().parents):
            if path not in visited and is_project_root(path):
                return path
            visited.add(path)
    raise RuntimeError(
        "PropertyProject was not found. Run the app from the project folder or set "
        "PROPERTY_PROJECT_ROOT."
    )


def powershell_command(script: Path, *arguments: str) -> tuple[str, ...]:
    executable = shutil.which("powershell.exe") or shutil.which("powershell")
    if not executable:
        executable = "powershell.exe"
    return (
        executable, "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(script), *arguments,
    )


def python_command(project_root: Path, script: Path) -> tuple[str, ...]:
    python = project_root / ".venv" / "Scripts" / "python.exe"
    return str(python), str(script), "--project-root", str(project_root)


def find_chrome_executable() -> Path | None:
    """Return Chrome's executable from its standard Windows locations."""
    candidates = (
        Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    )
    return next((path for path in candidates if path.is_file()), None)


def build_workflows(project_root: Path, force_download: bool = False) -> dict[str, WorkflowSpec]:
    """Describe all workflows in one place so the interface stays declarative."""
    phase1 = project_root / "scripts" / "phase_1_resale_model"
    phase4 = project_root / "scripts" / "phase_4_market_watch"
    phase5 = project_root / "scripts" / "phase_5_stakeholder_dashboard"
    phase6 = project_root / "scripts" / "phase_6_neighbourhood_explorer"
    phase7 = project_root / "scripts" / "phase_7_future_scenarios"

    data_gov_arguments = ("-ForceDownload",) if force_download else ()
    data_gov = CommandSpec(
        "Download data.gov.sg datasets",
        powershell_command(phase1 / "01_run_extraction.ps1", *data_gov_arguments),
    )
    onemap = CommandSpec(
        "Geocode blocks and refresh locations",
        powershell_command(phase1 / "02_run_onemap_extraction.ps1"),
        requires_onemap_token=True,
    )
    build_database = CommandSpec(
        "Build the research DuckDB database",
        powershell_command(phase1 / "03_run_duckdb_build.ps1"),
    )
    model_commands = tuple(
        CommandSpec(
            label,
            powershell_command(phase1 / script),
        )
        for script, label in (
            ("04_run_baselines.ps1", "Build price baselines"),
            ("05_run_feature_price_analysis.ps1", "Analyse feature relationships"),
            ("06_run_resale_models.ps1", "Train resale-price models"),
            ("07_run_phase1_diagnostics.ps1", "Run model diagnostics"),
            ("09_run_blend_calibration.ps1", "Calibrate the selected model"),
        )
    )
    dashboard_data_commands = (
        CommandSpec(
            "Build listing matches and estimates",
            python_command(project_root, phase4 / "17_19_build_listing_analysis.py"),
        ),
        CommandSpec(
            "Build market-watch rankings",
            python_command(project_root, phase4 / "20_22_build_market_watch.py"),
        ),
        CommandSpec(
            "Build dashboard views",
            python_command(project_root, phase5 / "23_build_dashboard_views.py"),
        ),
        CommandSpec(
            "Build neighbourhood explorer data",
            powershell_command(phase6 / "26_run_feature_explorer_build.ps1"),
        ),
        CommandSpec(
            "Build future-scenario evidence",
            powershell_command(phase7 / "28_run_future_scenario_build.ps1"),
        ),
    )
    return {
        "data_gov": WorkflowSpec(
            "data_gov", "1. Refresh data.gov.sg", "Download and prepare official HDB data.",
            (data_gov,),
        ),
        "onemap": WorkflowSpec(
            "onemap", "2. Refresh OneMap and amenities",
            "Geocode blocks and refresh location datasets.", (onemap,),
        ),
        "official_refresh": WorkflowSpec(
            "official_refresh", "1–4. Run full official-data refresh",
            "Refresh official data, rebuild DuckDB, and rerun the price model.",
            (data_gov, onemap, build_database, *model_commands),
        ),
        "build_database": WorkflowSpec(
            "build_database", "3. Build database", "Rebuild DuckDB from existing extracts.",
            (build_database,),
        ),
        "model_pipeline": WorkflowSpec(
            "model_pipeline", "4. Run modelling pipeline",
            "Run baselines, feature analysis, training, diagnostics and calibration.",
            model_commands,
        ),
        "chrome_collector": WorkflowSpec(
            "chrome_collector", "5. Collect PropertyGuru HDB listings (via Chrome extension)",
            "Start the local receiver and open PropertyGuru's Singapore HDB results page in Chrome.",
            (CommandSpec(
                "Open PropertyGuru Singapore HDB collector",
                powershell_command(phase4 / "15_open_chrome_with_collector.ps1"),
            ),),
        ),
        "dashboard_data": WorkflowSpec(
            "dashboard_data", "6. Process listings and dashboard data",
            "Value the latest listing snapshot and rebuild dashboard data.",
            dashboard_data_commands,
        ),
    }


def diagnostics_review_path(project_root: Path) -> Path:
    """Return the review only when it belongs to the current model selection."""
    reports = project_root / "reports" / "phase_1_resale_model"
    review_path = reports / "07_phase1_exit_review.json"
    selection_path = reports / "06_model_selection.json"
    if not review_path.is_file():
        raise ValueError("No diagnostics review is available yet. Run Button 4 first.")
    if not selection_path.is_file():
        raise ValueError("The model selection report is missing. Run Button 4 first.")
    try:
        review = json.loads(review_path.read_text(encoding="utf-8"))
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
        selected_model_id = selection["selected_model"]["model_id"]
        reviewed_model_id = review["selected_model_id"]
        trained_at = datetime.fromisoformat(selection["built_at_utc"])
        reviewed_at = datetime.fromisoformat(review["reviewed_at_utc"])
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError("The diagnostics review could not be read. Run model diagnostics again.") from error
    if reviewed_model_id != selected_model_id or reviewed_at < trained_at:
        raise ValueError("The saved diagnostics review is from an earlier training run. Wait for Button 4's diagnostics step to finish, or run diagnostics again.")
    return review_path


def model_performance_report(project_root: Path) -> tuple[dict, list[dict]]:
    """Read the completed training and release reports for the UI table."""
    reports = project_root / "reports" / "phase_1_resale_model"
    try:
        training = json.loads((reports / "06_model_selection.json").read_text(encoding="utf-8"))
        release = json.loads((reports / "09_blend_release.json").read_text(encoding="utf-8"))
        metrics = training["metrics"]
        selected_id = release["model_id"]
        release_time = datetime.fromisoformat(release["built_at_utc"])
        training_time = datetime.fromisoformat(training["built_at_utc"])
        if training.get("status") != "success" or release.get("status") != "national_candidate":
            raise ValueError("The training or release report is incomplete")
        if release_time < training_time:
            raise ValueError("The release report predates the latest model training")
        if release.get("baseline_gate", {}).get("model_id") != training["selected_model"]["model_id"]:
            raise ValueError("The release report does not match the latest selected model")
        by_model: dict[str, dict] = {}
        for metric in metrics:
            by_model.setdefault(metric["model_id"], {})[metric["dataset_split"]] = metric
        rows = []
        for model_id, splits in by_model.items():
            if "validation" not in splits or "test" not in splits:
                continue
            test = splits["test"]
            rows.append({
                "model_id": model_id,
                "name": MODEL_LABELS.get(model_id, test["model_name"]),
                "type": "Baseline" if model_id.startswith("BASELINE_") else "ML",
                "validation_mae": float(splits["validation"]["mae"]),
                "test_mae": float(test["mae"]),
                "test_median_ae": float(test["median_absolute_error"]),
                "test_mape_pct": float(test["mape_pct"]),
                "test_rmse": float(test["rmse"]),
                "test_r_squared": float(test["r_squared"]),
                "released": model_id == selected_id,
            })
        if not rows or not any(row["released"] for row in rows):
            raise ValueError("The released method has no matching performance row")
        rows.sort(key=lambda row: (row["test_mae"], row["name"]))
        return release, rows
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Cannot show model results: {error}") from error


class WorkflowRunner:
    """Run commands sequentially on a worker thread and stream events to the UI."""

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[str] | None = None
        self._cancel = threading.Event()
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, workflow: WorkflowSpec, onemap_token: str = "") -> None:
        if self.running:
            raise RuntimeError("Another workflow is already running")
        if any(command.requires_onemap_token for command in workflow.commands) and not onemap_token.strip():
            raise ValueError("Enter a OneMap access token for this workflow")
        self._cancel.clear()
        self._thread = threading.Thread(
            target=self._run, args=(workflow, onemap_token.strip()), daemon=True,
        )
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()
        with self._lock:
            if self._process and self._process.poll() is None:
                self._process.terminate()

    def _run(self, workflow: WorkflowSpec, onemap_token: str) -> None:
        self.events.put(("started", workflow.label))
        environment = os.environ.copy()
        if onemap_token:
            environment["ONEMAP_TOKEN"] = onemap_token
        try:
            for index, command in enumerate(workflow.commands, start=1):
                if self._cancel.is_set():
                    raise InterruptedError("Workflow cancelled")
                self.events.put((
                    "step", f"[{index}/{len(workflow.commands)}] {command.label}",
                ))
                with self._lock:
                    self._process = subprocess.Popen(
                        command.argv,
                        cwd=self.project_root,
                        env=environment,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        bufsize=1,
                        creationflags=CREATE_NO_WINDOW,
                    )
                assert self._process.stdout is not None
                try:
                    for line in self._process.stdout:
                        self.events.put(("log", line.rstrip()))
                        if self._cancel.is_set():
                            self._process.terminate()
                            break
                finally:
                    self._process.stdout.close()
                return_code = self._process.wait()
                if self._cancel.is_set():
                    raise InterruptedError("Workflow cancelled")
                if return_code != 0:
                    raise RuntimeError(
                        f"{command.label} failed with exit code {return_code}"
                    )
            self.events.put(("finished", f"{workflow.label} completed"))
        except Exception as error:
            self.events.put(("failed", str(error)))
        finally:
            with self._lock:
                self._process = None
            environment.pop("ONEMAP_TOKEN", None)


class DashboardProcess:
    def __init__(self, project_root: Path, events: queue.Queue[tuple[str, str]]):
        self.project_root = project_root
        self.events = events
        self.process: subprocess.Popen[str] | None = None
        self.browser_process: subprocess.Popen[bytes] | None = None
        self.log_handle = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self) -> None:
        if self.running:
            if self.browser_process is None or self.browser_process.poll() is not None:
                self._open_dashboard_window()
            self.events.put(("dashboard", "Dashboard is already running"))
            return
        python = self.project_root / ".venv" / "Scripts" / "python.exe"
        app = self.project_root / "dashboard" / "phase_5" / "24_streamlit_app.py"
        if not python.is_file():
            raise FileNotFoundError("Python environment is missing. Build the database first.")
        log_directory = self.project_root / ".local" / "control_center"
        log_directory.mkdir(parents=True, exist_ok=True)
        self.log_handle = (log_directory / "dashboard.log").open("a", encoding="utf-8")
        self.process = subprocess.Popen(
            [
                str(python), "-m", "streamlit", "run", str(app),
                "--server.address", "127.0.0.1", "--server.port", "8501",
                "--browser.gatherUsageStats", "false",
            ],
            cwd=self.project_root,
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW,
        )
        threading.Thread(target=self._wait_until_ready, daemon=True).start()

    def _wait_until_ready(self) -> None:
        for _ in range(40):
            if not self.running:
                self.events.put(("failed", "Dashboard stopped before it became ready"))
                return
            try:
                with urllib.request.urlopen(DASHBOARD_URL, timeout=1):
                    try:
                        self._open_dashboard_window()
                    except FileNotFoundError as error:
                        self.stop()
                        self.events.put(("failed", str(error)))
                        return
                    self.events.put((
                        "dashboard",
                        "Dashboard opened in its own Chrome window. Closing that window stops the dashboard.",
                    ))
                    return
            except OSError:
                time.sleep(0.25)
        self.events.put(("failed", "Dashboard did not become ready; check .local/control_center/dashboard.log"))

    def _open_dashboard_window(self) -> None:
        """Open a separately tracked Chrome window so its close event can stop Streamlit."""
        if self.browser_process is not None and self.browser_process.poll() is None:
            return
        chrome = find_chrome_executable()
        if chrome is None:
            raise FileNotFoundError("Google Chrome was not found. Install Chrome to open the dashboard.")
        profile_directory = self.project_root / ".local" / "control_center" / "dashboard_chrome_profile"
        profile_directory.mkdir(parents=True, exist_ok=True)
        browser = subprocess.Popen(
            [
                str(chrome), f"--app={DASHBOARD_URL}", f"--user-data-dir={profile_directory}",
                "--no-first-run", "--no-default-browser-check",
            ],
            cwd=self.project_root,
            creationflags=CREATE_NO_WINDOW,
        )
        self.browser_process = browser
        threading.Thread(target=self._stop_when_window_closes, args=(browser,), daemon=True).start()

    def _stop_when_window_closes(self, browser: subprocess.Popen[bytes]) -> None:
        browser.wait()
        with self._lock:
            if self.browser_process is not browser:
                return
            self.browser_process = None
        if self.running:
            self.stop()
            self.events.put(("dashboard", "Dashboard window closed; dashboard stopped"))

    @staticmethod
    def _terminate(process: subprocess.Popen[object] | None) -> None:
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

    def stop(self) -> None:
        with self._lock:
            dashboard = self.process
            browser = self.browser_process
            was_running = dashboard is not None and dashboard.poll() is None
            self.process = None
            self.browser_process = None
        self._terminate(browser)
        self._terminate(dashboard)
        if was_running:
            self.events.put(("dashboard", "Dashboard stopped"))
        if self.log_handle:
            self.log_handle.close()
            self.log_handle = None


class ControlCenter:
    def __init__(self, root: Tk, project_root: Path):
        self.root = root
        self.project_root = project_root
        self.force_download = BooleanVar(value=False)
        self.onemap_email = StringVar()
        self.onemap_password = StringVar()
        self.onemap_token = StringVar()
        self.status = StringVar(value="Ready")
        self.runner = WorkflowRunner(project_root)
        self.dashboard = DashboardProcess(project_root, self.runner.events)
        self.workflow_buttons: list[ttk.Button] = []
        self._active_workflow_key: str | None = None
        self._performance_window: Toplevel | None = None
        self._build_window()
        self._refresh_file_status()
        self.root.after(100, self._poll_events)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _build_window(self) -> None:
        self.root.title("PropertyProject Control Center")
        window_height = max(480, min(720, self.root.winfo_screenheight() - 80))
        self.root.geometry(f"1000x{window_height}")
        self.root.minsize(850, 480)
        container = ttk.Frame(self.root, padding=16)
        container.pack(fill="both", expand=True)
        self.content_panes = PanedWindow(
            container, orient="vertical", sashwidth=8, sashpad=2,
            sashrelief="raised", relief="flat", borderwidth=0,
            showhandle=True, cursor="sb_v_double_arrow",
        )
        self.content_panes.pack(fill="both", expand=True)
        controls_pane = ttk.Frame(self.content_panes)
        activity_pane = ttk.Frame(self.content_panes)
        self.content_panes.add(controls_pane, minsize=190, stretch="always")
        self.content_panes.add(activity_pane, minsize=145, stretch="always")
        self.root.after_idle(self._position_activity_divider)

        self.controls_canvas = Canvas(
            controls_pane, highlightthickness=0, borderwidth=0,
            background=self.root.cget("background"),
        )
        controls_scrollbar = ttk.Scrollbar(
            controls_pane, orient="vertical", command=self.controls_canvas.yview,
        )
        self.controls_canvas.configure(yscrollcommand=controls_scrollbar.set)
        controls_scrollbar.pack(side="right", fill="y")
        self.controls_canvas.pack(side="left", fill="both", expand=True)
        controls_content = ttk.Frame(self.controls_canvas)
        controls_window = self.controls_canvas.create_window(
            (0, 0), window=controls_content, anchor="nw",
        )
        controls_content.bind(
            "<Configure>",
            lambda _event: self.controls_canvas.configure(scrollregion=self.controls_canvas.bbox("all")),
        )
        self.controls_canvas.bind(
            "<Configure>",
            lambda event: self._resize_controls(event.width, controls_window),
        )
        self.root.bind_all("<MouseWheel>", self._scroll_controls, add="+")

        ttk.Label(
            controls_content, text="PropertyProject Control Center",
            font=("Segoe UI", 18, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            controls_content,
            text="Recommended order: 1, 2, 3, 4, 5, 6, then 7. The 1–4 button runs the first four steps together.",
        ).pack(anchor="w", pady=(2, 12))

        credentials = ttk.LabelFrame(controls_content, text="Collection settings", padding=10)
        credentials.pack(fill="x")
        ttk.Label(credentials, text="OneMap token").grid(row=0, column=0, sticky="w")
        ttk.Entry(credentials, textvariable=self.onemap_token, show="•", width=55).grid(
            row=0, column=1, sticky="ew", padx=8,
        )
        ttk.Checkbutton(
            credentials, text="Download fresh data.gov.sg files",
            variable=self.force_download,
        ).grid(row=0, column=2, sticky="w", padx=(8, 0))
        credentials.columnconfigure(1, weight=1)
        ttk.Label(
            credentials,
            text="The token is passed only to the running process and is not saved by this app.",
        ).grid(row=1, column=1, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Label(credentials, text="OneMap email").grid(row=2, column=0, sticky="w", pady=(7, 0))
        ttk.Entry(credentials, textvariable=self.onemap_email, width=55).grid(
            row=2, column=1, sticky="ew", padx=8, pady=(7, 0),
        )
        ttk.Button(
            credentials, text="Reauthenticate with OneMap",
            command=self._reauthenticate_onemap,
        ).grid(row=2, column=2, sticky="w", pady=(7, 0))
        ttk.Label(credentials, text="OneMap password").grid(row=3, column=0, sticky="w")
        ttk.Entry(credentials, textvariable=self.onemap_password, show="•", width=55).grid(
            row=3, column=1, sticky="ew", padx=8,
        )
        token_help = ttk.Frame(credentials)
        token_help.grid(row=4, column=1, columnspan=3, sticky="w", pady=(7, 0))
        ttk.Button(
            token_help, text="How to get a OneMap token", command=self._show_onemap_help,
        ).pack(side="left")
        ttk.Button(
            token_help, text="Open registration", command=lambda: webbrowser.open(ONEMAP_REGISTRATION_URL),
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            token_help, text="Open authentication guide",
            command=lambda: webbrowser.open(ONEMAP_AUTHENTICATION_URL),
        ).pack(side="left", padx=(6, 0))

        actions = ttk.Frame(controls_content)
        actions.pack(fill="x", pady=12)
        official_frame = self._workflow_group(actions, "Official data", [
            "data_gov", "onemap", "official_refresh",
        ], column=0)
        processing_frame = self._workflow_group(actions, "Processing", [
            "build_database", "model_pipeline", "dashboard_data",
        ], column=1)
        listings_frame = self._workflow_group(actions, "Listings and dashboard", [
            "chrome_collector",
        ], column=2)

        dashboard_frame = ttk.Frame(actions)
        dashboard_frame.grid(row=1, column=2, sticky="ew", padx=5, pady=(6, 0))
        ttk.Button(
            dashboard_frame, text="Browse 99.co HDB listings (function not built in yet)",
            command=lambda: webbrowser.open(NINETY_NINE_HDB_URL),
        ).pack(fill="x")
        ttk.Button(
            dashboard_frame, text="Browse SRX HDB listings (function not built in yet)",
            command=lambda: webbrowser.open(SRX_HDB_URL),
        ).pack(fill="x", pady=(4, 0))
        ttk.Button(
            dashboard_frame, text="7. Start / open dashboard", command=self._start_dashboard,
        ).pack(fill="x", pady=(4, 0))
        ttk.Button(
            dashboard_frame, text="Stop dashboard", command=self.dashboard.stop,
        ).pack(fill="x", pady=(4, 0))
        self.action_frames = (official_frame, processing_frame, listings_frame)
        self.dashboard_buttons_frame = dashboard_frame
        self.actions_frame = actions

        status_frame = ttk.LabelFrame(controls_content, text="Local files", padding=8)
        status_frame.pack(fill="x", pady=(0, 10))
        self.file_status = ttk.Label(status_frame, text="", wraplength=600)
        self.file_status.pack(side="left", anchor="w")
        ttk.Button(
            status_frame, text="Refresh status", command=self._refresh_file_status,
        ).pack(side="right")

        log_header = ttk.Frame(activity_pane)
        log_header.pack(fill="x")
        ttk.Label(log_header, text="Activity log", font=("Segoe UI", 10, "bold")).pack(side="left")
        self.cancel_button = ttk.Button(
            log_header, text="Cancel running workflow", command=self.runner.cancel,
            state="disabled",
        )
        self.cancel_button.pack(side="right")
        self.log = ScrolledText(activity_pane, height=18, wrap="word", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, pady=(5, 8))
        self.progress = ttk.Progressbar(activity_pane, mode="indeterminate")
        self.progress.pack(fill="x")
        ttk.Label(activity_pane, textvariable=self.status).pack(anchor="w", pady=(5, 0))

    def _position_activity_divider(self) -> None:
        available_height = self.content_panes.winfo_height()
        if available_height > 1:
            self.content_panes.sash_place(0, 0, int(available_height * 0.58))

    def _resize_controls(self, width: int, window_id: int) -> None:
        self.controls_canvas.itemconfigure(window_id, width=width)
        if not hasattr(self, "action_frames"):
            return
        layout = "wide" if width >= 1200 else "narrow"
        if getattr(self, "_actions_layout", None) == layout:
            return
        official, processing, listings = self.action_frames
        for frame in (*self.action_frames, self.dashboard_buttons_frame):
            frame.grid_forget()
        official.grid(row=0, column=0, sticky="nsew", padx=5)
        processing.grid(row=0, column=1, sticky="nsew", padx=5)
        if layout == "wide":
            listings.grid(row=0, column=2, sticky="nsew", padx=5)
            self.dashboard_buttons_frame.grid(row=1, column=2, sticky="ew", padx=5, pady=(6, 0))
        else:
            listings.grid(row=1, column=0, columnspan=2, sticky="ew", padx=5, pady=(6, 0))
            self.dashboard_buttons_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=5, pady=(6, 0))
        self.actions_frame.columnconfigure(0, weight=1)
        self.actions_frame.columnconfigure(1, weight=1)
        self.actions_frame.columnconfigure(2, weight=1 if layout == "wide" else 0)
        self._actions_layout = layout

    def _scroll_controls(self, event) -> str | None:
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        while widget is not None and widget != self.controls_canvas:
            widget = widget.master
        if widget != self.controls_canvas:
            return None
        steps = -int(event.delta / 120)
        if steps == 0 and event.delta:
            steps = -1 if event.delta > 0 else 1
        self.controls_canvas.yview_scroll(steps, "units")
        return "break"

    def _workflow_group(self, parent: ttk.Frame, title: str, keys: list[str], column: int) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent, text=title, padding=8)
        frame.grid(row=0, column=column, sticky="nsew", padx=5)
        workflows = build_workflows(self.project_root, self.force_download.get())
        for key in keys:
            workflow = workflows[key]
            button = ttk.Button(
                frame, text=workflow.label, command=lambda selected=key: self._start(selected),
            )
            button.pack(fill="x", pady=2)
            self.workflow_buttons.append(button)
            if key == "model_pipeline":
                ttk.Button(
                    frame, text="Open model performance table",
                    command=self._show_model_performance,
                ).pack(fill="x", pady=2)
                self.review_button = ttk.Button(
                    frame, text="Open model diagnostics review",
                    command=self._open_diagnostics_review,
                )
                self.review_button.pack(fill="x", pady=2)
        return frame

    def _open_diagnostics_review(self) -> None:
        try:
            review_path = diagnostics_review_path(self.project_root)
            os.startfile(review_path)
        except (OSError, ValueError) as error:
            messagebox.showerror("Cannot open diagnostics review", str(error))

    def _show_model_performance(self) -> dict | None:
        try:
            release, rows = model_performance_report(self.project_root)
        except ValueError as error:
            messagebox.showerror("Model results unavailable", str(error))
            return None
        if self._performance_window is not None and self._performance_window.winfo_exists():
            self._performance_window.destroy()
        window = Toplevel(self.root)
        self._performance_window = window
        window.title("Model performance and selected method")
        window.geometry("1050x440")
        window.minsize(760, 340)
        panel = ttk.Frame(window, padding=12)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="Model performance", font=("Segoe UI", 14, "bold")).pack(anchor="w")
        ttk.Label(
            panel,
            text=f"Chosen for valuation: {MODEL_LABELS.get(release['model_id'], release['model_name'])} ({release['release_type'].replace('_', ' ')})",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", pady=(6, 0))
        ttk.Label(panel, text=release["selection_reason"], wraplength=980).pack(anchor="w", pady=(2, 8))
        ttk.Label(
            panel,
            text="Lower MAE, median error, MAPE and RMSE are better; higher R² is better. ★ marks the released method. ML selection uses recent validation MAE; the release check also compares test MAE with the baseline.",
            wraplength=980,
        ).pack(anchor="w", pady=(0, 8))
        table_frame = ttk.Frame(panel)
        table_frame.pack(fill="both", expand=True)
        columns = ("method", "type", "validation_mae", "test_mae", "median_ae", "mape", "rmse", "r2")
        headings = ("Method", "Type", "Validation MAE", "Test MAE", "Test median error", "Test MAPE", "Test RMSE", "Test R²")
        widths = (265, 75, 115, 105, 125, 90, 110, 75)
        table = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        for column, heading, width in zip(columns, headings, widths):
            table.heading(column, text=heading)
            table.column(column, width=width, minwidth=65, anchor="w" if column == "method" else "e")
        vertical = ttk.Scrollbar(table_frame, orient="vertical", command=table.yview)
        horizontal = ttk.Scrollbar(table_frame, orient="horizontal", command=table.xview)
        table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        table.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        for row in rows:
            table.insert("", "end", values=(
                ("★ " if row["released"] else "") + row["name"], row["type"],
                f"S${row['validation_mae']:,.0f}", f"S${row['test_mae']:,.0f}",
                f"S${row['test_median_ae']:,.0f}", f"{row['test_mape_pct']:.2f}%",
                f"S${row['test_rmse']:,.0f}", f"{row['test_r_squared']:.3f}",
            ))
        return release

    def _start(self, key: str) -> None:
        database_workflows = {"official_refresh", "build_database", "model_pipeline", "dashboard_data"}
        if self.dashboard.running and key in database_workflows:
            messagebox.showerror(
                "Dashboard is running",
                "Stop the dashboard before starting a workflow that rebuilds database or model files.",
            )
            return
        workflows = build_workflows(self.project_root, self.force_download.get())
        workflow = workflows[key]
        try:
            self.runner.start(workflow, self.onemap_token.get())
            self._active_workflow_key = key
        except (RuntimeError, ValueError) as error:
            messagebox.showerror("Cannot start workflow", str(error))

    def _show_onemap_help(self) -> None:
        messagebox.showinfo(
            "Getting a OneMap access token",
            "1. Open the OneMap registration page and create an API account.\n\n"
            "2. Confirm the account using the instructions sent by OneMap and set a password.\n\n"
            "3. Open the authentication guide and generate an access token using the registered "
            "email address and password.\n\n"
            "4. Copy the access_token value into the OneMap token field in this window.\n\n"
            "5. Run '2. Refresh OneMap and amenities' or '1–4. Run full official-data refresh'.\n\n"
            "OneMap currently documents tokens as valid for three days. Generate a new token after expiry. "
            "The Control Center does not save your token or account password.",
        )

    def _reauthenticate_onemap(self) -> None:
        email = self.onemap_email.get().strip()
        password = self.onemap_password.get()
        if not email or not password:
            messagebox.showerror(
                "OneMap login required",
                "Enter your registered OneMap email and password first.",
            )
            return
        self.status.set("Requesting a new OneMap access token...")
        threading.Thread(
            target=self._request_onemap_token,
            args=(email, password),
            daemon=True,
        ).start()

    def _request_onemap_token(self, email: str, password: str) -> None:
        try:
            payload = json.dumps({"email": email, "password": password}).encode("utf-8")
            request = urllib.request.Request(
                ONEMAP_TOKEN_URL,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                result = json.loads(response.read().decode("utf-8"))
            token = str(result.get("access_token", "")).strip()
            if not token:
                raise RuntimeError("OneMap did not return an access token.")
            self.root.after(0, lambda: self._onemap_token_received(token))
        except Exception as error:
            self.root.after(0, lambda: self._onemap_auth_failed(str(error)))

    def _onemap_token_received(self, token: str) -> None:
        self.onemap_token.set(token)
        self.onemap_password.set("")
        self.status.set("OneMap reauthentication succeeded; token ready for the next refresh.")
        messagebox.showinfo(
            "OneMap reauthenticated",
            "A new access token was received. Run step 2 or the full official refresh.",
        )

    def _onemap_auth_failed(self, message: str) -> None:
        self.onemap_password.set("")
        self.status.set("OneMap reauthentication failed")
        messagebox.showerror(
            "OneMap reauthentication failed",
            "Check the email and password, then try again.\n\n" + message,
        )

    def _start_dashboard(self) -> None:
        if self.runner.running:
            messagebox.showerror(
                "Workflow is running",
                "Wait for the workflow to finish or cancel it before starting the dashboard.",
            )
            return
        try:
            self.dashboard.start()
        except (OSError, FileNotFoundError) as error:
            messagebox.showerror("Cannot start dashboard", str(error))

    def _set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        for button in self.workflow_buttons:
            button.configure(state=state)
        self.cancel_button.configure(state="normal" if running else "disabled")
        if running:
            self.progress.start(12)
        else:
            self.progress.stop()
            self._refresh_file_status()

    def _poll_events(self) -> None:
        try:
            while True:
                event, message = self.runner.events.get_nowait()
                if event == "started":
                    self._set_running(True)
                    self.status.set(message)
                    self._write_log(f"\n=== {message} ===")
                elif event == "step":
                    self.status.set(message)
                    self._write_log(f"\n{message}")
                elif event == "log":
                    self._write_log(message)
                elif event in {"finished", "failed"}:
                    completed_key = self._active_workflow_key
                    self._active_workflow_key = None
                    self._set_running(False)
                    self.status.set(message)
                    self._write_log(message)
                    if event == "failed":
                        messagebox.showerror("Workflow stopped", message)
                    elif completed_key in {"model_pipeline", "official_refresh"}:
                        release = self._show_model_performance()
                        if release:
                            chosen = MODEL_LABELS.get(release["model_id"], release["model_name"])
                            self.status.set(f"{message} — chosen method: {chosen}")
                            self._write_log(f"Chosen valuation method: {chosen}. {release['selection_reason']}")
                elif event == "dashboard":
                    self.status.set(message)
                    self._write_log(message)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _write_log(self, message: str) -> None:
        self.log.insert(END, message + "\n")
        self.log.see(END)

    def _refresh_file_status(self) -> None:
        files = [
            ("Raw data", self.project_root / "data" / "raw"),
            ("Processed data", self.project_root / "data" / "processed"),
            ("Research DB", self.project_root / "data" / "property.duckdb"),
            ("Dashboard DB", self.project_root / "data" / "listings.db"),
        ]
        parts = []
        for label, path in files:
            if path.exists():
                modified = time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime))
                parts.append(f"{label}: {modified}")
            else:
                parts.append(f"{label}: missing")
        self.file_status.configure(text="   |   ".join(parts))

    def _close(self) -> None:
        if self.runner.running:
            if not messagebox.askyesno("Workflow running", "Cancel the workflow and close?"):
                return
            self.runner.cancel()
        if self.dashboard.running:
            if not messagebox.askyesno("Dashboard running", "Stop the dashboard and close?"):
                return
            self.dashboard.stop()
        self.root.destroy()


def main() -> None:
    try:
        project_root = find_project_root()
    except RuntimeError as error:
        root = Tk()
        root.withdraw()
        messagebox.showerror("PropertyProject not found", str(error))
        raise SystemExit(1) from error
    root = Tk()
    ControlCenter(root, project_root)
    root.mainloop()


if __name__ == "__main__":
    main()
