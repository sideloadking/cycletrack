"""Deterministic browser regression checks for the VeloTrack frontend.

The suite serves the real ``web/`` entry point, but intercepts API calls with
small in-memory fixtures. It therefore exercises the browser, routing,
rendering, interaction, and cleanup paths without depending on a user's SQLite
state, network APIs, or import timing.

Setup and run::

    python -m pip install -r tests/requirements-browser.txt
    python -m playwright install chromium
    python tests/browser_regression.py
"""

from __future__ import annotations

import base64
import json
import threading
from collections import Counter
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Page, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
RIDE_ID = 42
STARTED_AT = 1_735_689_600


def ride_fixtures() -> dict:
    return {
        "rides": [
            {
                "id": RIDE_ID,
                "filename": "morning.fit",
                "started_at": STARTED_AT,
                "distance_m": 42_500,
                "duration_s": 5_400,
                "gain_m": 620,
                "avg_watts": 196,
                "has_hr": True,
                "bike_calibrated": True,
            },
            {
                "id": 43,
                "filename": "evening.fit",
                "started_at": STARTED_AT - 86_400,
                "distance_m": 31_200,
                "duration_s": 4_100,
                "gain_m": 410,
                "avg_watts": 182,
                "has_hr": True,
                "bike_calibrated": True,
            },
        ],
        "records": [
            {"ride_id": RIDE_ID, "label": "20-minute power", "metric": "avg_watts", "value": 248, "started_at": STARTED_AT},
            {"ride_id": RIDE_ID, "label": "Longest ride", "metric": "distance_m", "value": 42_500, "started_at": STARTED_AT},
        ],
        "fitness": {
            "points": [
                {"date": STARTED_AT - 172_800, "ride_id": 43, "ctl": 28.0, "atl": 31.0, "tsb": -3.0, "trimp": 82},
                {"date": STARTED_AT - 86_400, "ride_id": 43, "ctl": 29.4, "atl": 30.2, "tsb": -0.8, "trimp": 74},
                {"date": STARTED_AT, "ride_id": RIDE_ID, "ctl": 31.1, "atl": 32.0, "tsb": -0.9, "trimp": 96},
            ],
            "ctl_tau": 42,
            "atl_tau": 7,
        },
        "drift": {
            "points": [
                {"date": STARTED_AT - 86_400, "ride_id": 43, "drift_bpm_per_hr": 3.2},
                {"date": STARTED_AT, "ride_id": RIDE_ID, "drift_bpm_per_hr": 1.4},
            ]
        },
        "wattsHr": {
            "fixed_hrs": [140, 150],
            "series": {
                "140": [
                    {"date": STARTED_AT - 86_400, "ride_id": 43, "watts": 176, "lo": 150, "hi": 204},
                    {"date": STARTED_AT, "ride_id": RIDE_ID, "watts": 188, "lo": 164, "hi": 214},
                ],
                "150": [
                    {"date": STARTED_AT - 86_400, "ride_id": 43, "watts": 194, "lo": 170, "hi": 220},
                    {"date": STARTED_AT, "ride_id": RIDE_ID, "watts": 201, "lo": 179, "hi": 225},
                ],
            },
        },
        "power": {
            "durations": [1, 5, 20, 60],
            "series": {
                str(minutes): [
                    {"date": STARTED_AT - 86_400, "ride_id": 43, "watts": 320 - minutes, "lo": 300 - minutes, "hi": 340 - minutes},
                    {"date": STARTED_AT, "ride_id": RIDE_ID, "watts": 332 - minutes, "lo": 310 - minutes, "hi": 351 - minutes},
                ]
                for minutes in (1, 5, 20, 60)
            },
            "curves": [
                {"ride_id": 43, "date": STARTED_AT - 86_400, "points": [{"min": m, "watts": 320 - m} for m in (1, 2, 5, 10, 20, 60)]},
                {"ride_id": RIDE_ID, "date": STARTED_AT, "points": [{"min": m, "watts": 332 - m} for m in (1, 2, 5, 10, 20, 60)]},
            ],
        },
    }


def ride_detail_fixture() -> dict:
    return {
        "id": RIDE_ID,
        "filename": "morning.fit",
        "started_at": STARTED_AT,
        "metrics": {
            "distance_m": 42_500,
            "duration_s": 5_400,
            "elevation_gain_m": 620,
            "avg_watts": 196,
            "avg_watts_lo": 151,
            "avg_watts_hi": 281,
            "normalized_power": 218,
            "normalized_power_lo": 172,
            "normalized_power_hi": 304,
            "has_hr": True,
            "cardiac_drift": {
                "duration_min": 31,
                "mean_power": 196,
                "drift_bpm_per_hr": 1.4,
                "drift_pct_per_hr": 2.1,
                "start_hr": 138,
                "end_hr": 145,
                "r2": 0.94,
            },
            "grade_distribution": [
                {"from": -5, "count": 12},
                {"from": 0, "count": 42},
                {"from": 3, "count": 18},
            ],
        },
    }


def series_fixture() -> dict:
    points = [
        {"idx": 0, "t": 0, "lat": 52.0000, "lon": -1.5000, "elev": 100, "elev_raw": 100, "grade": 0.01, "speed": 7.0, "dist": 0},
        {"idx": 1, "t": 1_000, "lat": 52.0040, "lon": -1.4960, "elev": 132, "elev_raw": 132, "grade": -0.01, "speed": 7.5, "dist": 7_500},
        {"idx": 2, "t": 3_672, "lat": 52.0080, "lon": -1.4920, "elev": 118, "elev_raw": 118, "grade": -0.04, "speed": 8.0, "dist": 28_500},
        {"idx": 3, "t": 3_724, "lat": 52.0090, "lon": -1.4910, "elev": 114, "elev_raw": 114, "grade": -0.04, "speed": 8.0, "dist": 28_916},
        {"idx": 4, "t": 4_000, "lat": 52.0100, "lon": -1.4900, "elev": 128, "elev_raw": 128, "grade": 0.02, "speed": 6.5, "dist": 30_700},
    ]
    return {
        "gps": points,
        "hr": [{"t": point["t"], "hr": 138 + index} for index, point in enumerate(points)],
        "power": [
            {"t": point["t"], "watts_est": 180 + index * 5, "watts_lo": 130 + index * 4, "watts_hi": 250 + index * 6}
            for index, point in enumerate(points)
        ],
    }


class WebHandler(SimpleHTTPRequestHandler):
    """Serve the production SPA at ``/`` and its files below ``/static``."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def translate_path(self, path: str) -> str:
        clean = urlsplit(path).path
        if clean == "/":
            return str(WEB / "index.html")
        if clean.startswith("/static/"):
            return str(WEB / clean.removeprefix("/static/"))
        return str(WEB / clean.lstrip("/"))

    def log_message(self, *_args):
        return


class StaticWebServer:
    def __init__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), WebHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class MockApi:
    """Small per-test API fixture with explicit state for async flows."""

    def __init__(self, scenario: str):
        self.scenario = scenario
        self.calls = Counter()
        self.unexpected = []
        self.import_payload = None
        self.imported = False
        self.manual_labels = {}

    def install(self, page: Page):
        page.route("**/api/**", self.handle)
        # The replay map is still exercised, but external tile availability is
        # deliberately outside this deterministic suite's scope.
        page.route("https://*.basemaps.cartocdn.com/**", lambda route: route.abort())

    def json(self, route, payload, status=200):
        route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))

    def error(self, route, message="fixture failure", status=503):
        self.json(route, {"error": message}, status=status)

    def handle(self, route):
        request = route.request
        parsed = urlsplit(request.url)
        path = parsed.path
        self.calls[path] += 1

        if path == "/api/overview":
            if self.scenario == "partial":
                self.error(route, "overview projection unavailable")
            else:
                data = ride_fixtures()
                data["routes"] = []
                self.json(route, data)
            return

        if path == "/api/rides":
            self.json(route, ride_fixtures()["rides"])
            return
        if path == "/api/records":
            self.json(route, ride_fixtures()["records"])
            return
        if path == "/api/trends/fitness":
            if self.scenario == "partial" and self.calls[path] == 1:
                self.error(route, "fitness trend unavailable")
            else:
                self.json(route, ride_fixtures()["fitness"])
            return
        if path == "/api/trends/cardiac":
            self.json(route, ride_fixtures()["drift"])
            return
        if path == "/api/trends/watts_hr":
            self.json(route, ride_fixtures()["wattsHr"])
            return
        if path == "/api/trends/power":
            self.json(route, ride_fixtures()["power"])
            return

        if path == "/api/jobs":
            self.json(route, [{"id": "job-1", "status": "done", "progress": 100, "filename": "morning.fit", "message": "Imported"}] if self.imported else [])
            return
        if path == "/api/import" and request.method == "POST":
            self.import_payload = json.loads(request.post_data or "{}")
            self.imported = True
            self.json(route, {"jobs": ["job-1"]})
            return

        if path == f"/api/rides/{RIDE_ID}" and request.method == "GET":
            self.json(route, ride_detail_fixture())
            return
        if path == f"/api/rides/{RIDE_ID}/series":
            self.json(route, series_fixture())
            return
        if path == f"/api/rides/{RIDE_ID}/descents" and request.method == "GET":
            self.json(route, self.descents())
            return
        if path == f"/api/rides/{RIDE_ID}/coast_segments" and request.method == "POST":
            payload = json.loads(request.post_data or "{}")
            key = round(float(payload["t_start"]), 3)
            label = payload.get("label")
            if label is None:
                self.manual_labels.pop(key, None)
            else:
                self.manual_labels[key] = label
            self.json(route, {"ok": True})
            return

        self.unexpected.append((request.method, path))
        self.error(route, f"Unhandled fixture request: {request.method} {path}", status=500)

    def descents(self) -> dict:
        base = [
            {"t_start": 3_672, "t_end": 3_724, "label": "ask", "source": "auto"},
            {"t_start": 1_000, "t_end": 1_050, "label": "coast", "source": "manual"},
        ]
        for descent in base:
            key = round(descent["t_start"], 3)
            if key in self.manual_labels:
                descent["label"] = self.manual_labels[key]
                descent["source"] = "manual"
        return {"descents": base}


def new_page(browser, backend: MockApi):
    context = browser.new_context(
        viewport={"width": 1280, "height": 900},
        locale="en-GB",
        timezone_id="UTC",
        service_workers="block",
    )
    context.set_default_timeout(5_000)
    page = context.new_page()
    backend.install(page)
    return context, page


def wait_for(page: Page, selector: str):
    page.locator(selector).first.wait_for(state="visible")


def assert_no_page_errors(errors: list[str], name: str):
    if errors:
        raise AssertionError(f"{name} produced page errors: {errors}")


def test_overview(page: Page, backend: MockApi, base_url: str):
    page.goto(f"{base_url}/#/dashboard", wait_until="domcontentloaded")
    wait_for(page, ".primary-chart")
    wait_for(page, "#fitness-chart .graph-line")

    assert page.locator("h1").inner_text() == "Overview"
    assert page.locator("#period-title").text_content().strip() == "Last 30 days"
    assert page.locator("#watts-hr-chart svg").count() == 1
    assert page.locator("#fitness-chart .graph-line").count() == 3
    assert page.locator("#fitness-chart .graph-bar").count() == 0
    for selector in ("#drift-chart svg", "#power-trend-chart svg", "#power-curves-chart svg"):
        wait_for(page, selector)
    assert not backend.unexpected, backend.unexpected


def test_import(page: Page, backend: MockApi, base_url: str):
    page.goto(f"{base_url}/#/import", wait_until="domcontentloaded")
    wait_for(page, "#dropzone")
    assert page.locator(".import-guide").count() == 0
    layout_box = page.locator(".import-layout").bounding_box()
    card_box = page.locator(".import-card").bounding_box()
    assert abs((card_box["x"] + card_box["width"] / 2) - (layout_box["x"] + layout_box["width"] / 2)) < 1

    page.set_input_files(
        "#file-input",
        {"name": "morning.fit", "mimeType": "application/octet-stream", "buffer": b"FIT fixture"},
    )
    wait_for(page, "#queue-card")
    wait_for(page, ".job--done")

    assert page.locator(".job--done").inner_text().find("morning.fit") >= 0
    assert backend.import_payload is not None
    assert [item["name"] for item in backend.import_payload["files"]] == ["morning.fit"]
    assert base64.b64decode(backend.import_payload["files"][0]["data"]) == b"FIT fixture"
    assert not backend.unexpected, backend.unexpected


def test_ride_detail(page: Page, backend: MockApi, base_url: str):
    page.goto(f"{base_url}/#/ride/{RIDE_ID}", wait_until="domcontentloaded")
    wait_for(page, "#replay-play")
    wait_for(page, "#ch-elev .graph-line")

    assert page.locator("h1").count() == 1
    assert "42.5 km" in page.locator(".detail-hero").inner_text()
    assert "151 W – 281 W" in page.locator(".detail-hero").inner_text()
    assert page.locator("#ride-map.leaflet-container").count() == 1
    assert page.locator("#ch-hr .graph-line").count() == 1
    assert page.locator("#ch-power .graph-line").count() == 1
    replay = page.locator("#replay-play")
    replay.click()
    assert replay.get_attribute("aria-pressed") == "true"
    replay.click()
    assert replay.get_attribute("aria-pressed") == "false"
    assert not backend.unexpected, backend.unexpected


def test_descent_review(page: Page, backend: MockApi, base_url: str):
    page.goto(f"{base_url}/#/ride/{RIDE_ID}", wait_until="domcontentloaded")
    wait_for(page, "#descents-count")
    details = page.locator("details.descents")
    assert details.get_attribute("open") is None
    assert page.locator("#descents-count").inner_text() == "1 to review · 2 total"

    details.locator("summary").click()
    wait_for(page, "#descents-list .list-row")
    rows = page.locator("#descents-list .list-row")
    assert rows.count() == 2
    assert "To review" in rows.nth(0).inner_text()
    assert "Freewheeled" in rows.nth(1).inner_text()
    assert "1:01:12 – 1:02:04" in rows.nth(0).inner_text()

    groups = page.locator("#descents-list .descent-tags")
    assert groups.count() == 2
    group = groups.nth(1)
    assert group.locator("button").count() == 4
    before_width = group.bounding_box()["width"]
    before_height = group.bounding_box()["height"]
    before_button_widths = group.locator("button").evaluate_all("els => els.map(el => el.getBoundingClientRect().width)")
    assert max(before_button_widths) - min(before_button_widths) < 0.1

    group.locator("button[data-value='pedal']").click()
    group.locator("button[data-value='pedal'].active").wait_for(state="visible")
    after_group = page.locator("#descents-list .descent-tags").nth(1)
    after_group.wait_for(state="visible")
    after_box = after_group.bounding_box()
    after_button_widths = after_group.locator("button").evaluate_all("els => els.map(el => el.getBoundingClientRect().width)")
    assert abs(after_box["width"] - before_width) < 1
    assert abs(after_box["height"] - before_height) < 1
    assert max(after_button_widths) - min(after_button_widths) < 0.1
    assert backend.manual_labels[1_000.0] == "pedal"
    assert not backend.unexpected, backend.unexpected


def test_partial_failure_retry(page: Page, backend: MockApi, base_url: str):
    page.goto(f"{base_url}/#/dashboard", wait_until="domcontentloaded")
    retry = page.locator("[data-retry-overview='fitness']")
    retry.wait_for(state="visible")
    assert "Fitness trend unavailable" in page.locator("#fitness-chart").inner_text()
    assert page.locator("#watts-hr-chart .graph-line").count() == 1

    retry.click()
    wait_for(page, "#fitness-chart .graph-line")
    assert page.locator("#fitness-chart .graph-line").count() == 3
    assert backend.calls["/api/trends/fitness"] == 2
    assert not backend.unexpected, backend.unexpected


def run():
    server = StaticWebServer()
    passed = 0
    tests = [
        ("overview", "overview", test_overview),
        ("import", "import", test_import),
        ("ride detail", "ride", test_ride_detail),
        ("descent review", "ride", test_descent_review),
        ("partial failure retry", "partial", test_partial_failure_retry),
    ]
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                for name, scenario, test in tests:
                    backend = MockApi(scenario)
                    context, page = new_page(browser, backend)
                    page_errors: list[str] = []
                    page.on("pageerror", lambda error: page_errors.append(str(error)))
                    try:
                        test(page, backend, server.url)
                        assert_no_page_errors(page_errors, name)
                    finally:
                        context.close()
                    print(f"PASS {name}")
                    passed += 1
            finally:
                browser.close()
    finally:
        server.close()
    print(f"\nBrowser regression checks passed: {passed}/{len(tests)}")


if __name__ == "__main__":
    run()
