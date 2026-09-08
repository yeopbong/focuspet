"""HTTP browser verification. This suite makes no claim about file-URL access."""
from __future__ import annotations

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
from urllib.parse import urlsplit

import pytest

from focuspet.demo_export import export_demo


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


@pytest.fixture(scope="module")
def demo_url(tmp_path_factory):
    if os.environ.get("FOCUSPET_BROWSER_TESTS") != "1":
        pytest.skip("HTTP browser suite is opt-in: set FOCUSPET_BROWSER_TESTS=1 and install Chromium")
    root = tmp_path_factory.mktemp("http-demo")
    export_demo("workday", root / "focuspet", seed=7)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(root)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/focuspet/"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser(demo_url):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def page(browser, demo_url, request):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    errors = []
    requests = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.on("request", lambda item: requests.append(item.url))
    response = page.goto(demo_url)
    assert response and response.ok
    page.wait_for_function("window.FOCUS_PET_DEMO && document.querySelector('#scenario').options.length === 5")
    page.wait_for_function("Array.from(document.images).every(image => image.complete && image.naturalWidth > 0)")
    yield page
    artifacts = os.environ.get("FOCUSPET_BROWSER_ARTIFACTS")
    if artifacts:
        output = Path(artifacts)
        output.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(output / f"{request.node.name}.png"), full_page=True)
        (output / f"{request.node.name}.json").write_text(json.dumps({
            "transport": "HTTP", "project_subpath": "/focuspet/", "browser": browser.version,
            "console_errors": errors, "requests": [urlsplit(url).path for url in requests],
        }, indent=2) + "\n")
    context.close()
    assert errors == []
    origin = urlsplit(demo_url)
    assert all(urlsplit(url).netloc == origin.netloc for url in requests), "Unexpected external request"


def assert_display_matches_export(page, scenario, branch, index):
    expected = page.evaluate("([s,b,i]) => window.FOCUS_PET_DEMO.scenarios[s][b].snapshots[i]",
                             [scenario, branch, index])
    assert page.locator("#state").inner_text() == expected["state"]
    assert page.locator("#source").inner_text() == expected["prediction"]["source"]
    focus = "—" if expected["focus"] is None else str(int(expected["focus"] + 0.5))
    load = "120+" if expected["workload"] > 120 else str(int(expected["workload"] // 1 + (expected["workload"] % 1 >= 0.5)))
    assert page.locator("#focus").inner_text() == focus
    assert page.locator("#load").inner_text() == load
    assert page.locator("#observation").inner_text() == expected["observation"]


def test_scenarios_and_four_character_resources(page):
    assert "SYNTHETIC DATA" in page.locator(".demo-badge").inner_text()
    for scenario in ["workday", "reading", "browser-ambiguity", "breaks", "permission-loss"]:
        page.locator("#scenario").select_option(scenario)
        assert_display_matches_export(page, scenario, "original", 0)
    for name in ["Mira", "Jun", "Ada", "Sol"]:
        page.get_by_role("button", name=f"Choose {name}", exact=True).click()
        assert page.locator("#character-name").inner_text() == name
        page.wait_for_function("document.querySelector('#character').getContext('2d').getImageData(0,0,64,80).data.some((v,i) => i%4===3 && v>0)")


def test_play_pause_seek_and_reset(page):
    page.get_by_role("button", name="Play playback", exact=True).click()
    page.wait_for_function("Number(document.querySelector('#seek').value) > 0")
    page.get_by_role("button", name="Pause playback", exact=True).click()
    position = page.locator("#seek").input_value()
    page.wait_for_timeout(600)
    assert page.locator("#seek").input_value() == position
    page.locator("#seek").fill("12")
    page.locator("#seek").dispatch_event("input")
    assert_display_matches_export(page, "workday", "original", 12)
    page.get_by_role("button", name="Reset playback", exact=True).click()
    assert page.locator("#seek").input_value() == "0"
    assert page.locator("#play").get_attribute("aria-pressed") == "false"
    assert_display_matches_export(page, "workday", "original", 0)


def test_precomputed_branch_changes_timeline_with_values_and_resets(page):
    original_states = page.locator("#strip span").evaluate_all("items => items.map(item => item.dataset.state)")
    page.get_by_role("button", name="Explore a correction", exact=True).click()
    index = int(page.locator("#seek").input_value())
    assert_display_matches_export(page, "workday", "corrected", index)
    expected_states = page.evaluate("window.FOCUS_PET_DEMO.scenarios.workday.corrected.snapshots.map(row => row.state)")
    assert page.locator("#strip span").evaluate_all("items => items.map(item => item.dataset.state)") == expected_states
    assert expected_states != original_states
    assert "No browser training occurs" in page.locator("#branch-note").inner_text()
    page.get_by_role("button", name="Return to original", exact=True).click()
    assert page.locator("#strip span").evaluate_all("items => items.map(item => item.dataset.state)") == original_states
    page.get_by_role("button", name="Explore a correction", exact=True).click()
    page.get_by_role("button", name="Reset playback", exact=True).click()
    assert page.locator("#correct").inner_text() == "Explore a correction"
    assert page.locator("#seek").input_value() == "0"


def test_declared_rest_missing_values_and_refresh_subpath(page, demo_url):
    page.locator("#scenario").select_option("breaks")
    page.get_by_role("button", name="Jump to a declared rest", exact=True).click()
    assert page.locator("#state").inner_text() == "Rest"
    assert page.locator("#focus").inner_text() == "—"
    page.locator("#scenario").select_option("permission-loss")
    index = page.evaluate("window.FOCUS_PET_DEMO.scenarios['permission-loss'].original.snapshots.findIndex(row => row.workload_stale)")
    assert index >= 0
    page.locator("#seek").fill(str(index))
    page.locator("#seek").dispatch_event("input")
    assert page.locator("#focus").inner_text() == "—"
    assert "observation gap" in page.locator("#load-detail").inner_text()
    response = page.reload()
    assert response and response.ok
    assert page.url == demo_url
    assert_display_matches_export(page, "workday", "original", 0)


@pytest.mark.parametrize("width", [375, 768, 1440])
def test_layout_stays_within_viewport(page, width):
    page.set_viewport_size({"width": width, "height": 900})
    page.get_by_role("button", name="Reset playback", exact=True).scroll_into_view_if_needed()
    dimensions = page.evaluate("({width:innerWidth, content:document.documentElement.scrollWidth})")
    assert dimensions["content"] <= dimensions["width"], dimensions
    assert page.locator("#play").is_visible()
    assert page.locator("#seek").is_visible()
