import json
import re

from focuspet.demo_export import export_demo
from focuspet.replay import replay_scenario


def test_export_is_offline_deterministic_core_playback(tmp_path):
    a, b = tmp_path / "one", tmp_path / "two"
    export_demo("reading", a, seed=17)
    export_demo("reading", b, seed=17)
    assert (a / "trajectory.js").read_bytes() == (b / "trajectory.js").read_bytes()
    data = json.loads(
        (a / "trajectory.js").read_text().removeprefix("window.FOCUS_PET_DEMO = ").strip().removesuffix(";")
    )
    assert data["mode"] == "synthetic-demo"
    assert (
        data["scenarios"]["reading"]["original"]["snapshots"] == replay_scenario("reading", 17)["snapshots"]
    )
    for character in data["characters"]:
        assert (a / "characters" / character / "sprites.png").exists()
        assert (a / "characters" / character / "thumbnail.png").exists()
    page = (a / "index.html").read_text()
    for ref in re.findall(r'(?:src|href)="([^"]+)"', page):
        assert not ref.startswith(("http:", "https:", "//"))
        if not ref.startswith("#"):
            assert (a / ref).exists()
    for name in ("app.js", "style.css"):
        source = (a / name).read_text()
        assert "https://" not in source and "http://" not in source
    assert "DEMO / SYNTHETIC DATA" in page
