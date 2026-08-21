"""Capture full-page screenshots of the running engine for visual review.

Serves the real SPA against the local engine (default 127.0.0.1:8391), so the
captures show actual rider data including route ribbons and replay. Read-only:
nothing in the database is touched.

Usage::

    python main.py --port 8391 --no-browser   # in another shell
    python tests/capture_screens.py [--base-url http://127.0.0.1:8391] [--out dogfood-output/screenshots/polish]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

PAGES = [
    ("overview", "#/dashboard"),
    ("rides", "#/rides"),
    ("routes", "#/routes"),
    ("route-detail", "#/route/85"),
    ("records", "#/records"),
    ("import", "#/import"),
    ("profile", "#/profile"),
    ("ride-detail", "#/ride/7"),
]

VIEWPORTS = [
    ("desktop-1440", 1440, 900),
    ("mobile-390", 390, 844),
]


def run(base_url: str, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for label, width, height in VIEWPORTS:
                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    device_scale_factor=2,
                    locale="en-GB",
                )
                page = context.new_page()
                for name, hash_route in PAGES:
                    page.goto(f"{base_url}/{hash_route}", wait_until="domcontentloaded")
                    page.wait_for_timeout(3200)
                    target = out_dir / f"{name}-{label}.png"
                    page.screenshot(path=str(target), full_page=True)
                    print(f"captured {target.name}")
                context.close()
        finally:
            browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8391")
    parser.add_argument("--out", default="dogfood-output/screenshots/polish")
    args = parser.parse_args()
    run(args.base_url, Path(args.out))
