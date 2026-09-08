from collections import Counter
import json
import subprocess
import sys

from focuspet.learning.data import chronological_split, support
from focuspet.learning.evaluation import _query_stream
from focuspet.learning.synthetic import work_dataset


def test_latent_simulation_reproducible_distinct_labels_and_prior():
    a, b = work_dataset(seed=3, days=3), work_dataset(seed=3, days=3)
    # Generated feature ids from domain are replaced with deterministic identifiers.
    assert a == b
    assert {r["label"] for r in a["records"]} == {"Focused", "Normal", "Distracted"}
    assert any(max(r["prior"], key=r["prior"].get) != r["label"] for r in a["records"])
    assert a["mode"] == "synthetic-demo"


def test_query_strategies_share_independent_budget_and_temporal_opportunities():
    data = work_dataset(seed=5, days=17)
    pool, later = chronological_split(data["records"], fraction=16/17)
    assert max(r["end"] for r in pool) + 300 <= min(r["start"] for r in later)
    for strategy in ("random", "uncertainty-diversity"):
        snapshots, log = _query_stream(pool, data["feature_names"], 5, strategy)
        assert support(snapshots[30])["episodes"] == 30
        by_day = Counter(int(q["at"]//86400) for q in log)
        assert max(by_day.values()) == 2
        assert all(b["at"]-a["at"] >= 5400 for a, b in zip(log, log[1:]))
        assert all(q["target_end"] < q["at"] <= q["target_end"]+1800 for q in log)


def test_cli_evaluate_runs_installed_protocol_and_persists_evidence(tmp_path):
    config = tmp_path / "config.json"
    output = tmp_path / "results"
    config.write_text(json.dumps({"seeds": [7], "label_budgets": [30], "search_budget": 2,
                                  "output": str(output)}))
    run = subprocess.run([sys.executable, "-m", "focuspet.cli", "evaluate", "--config", str(config)],
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["mode"] == "synthetic-demo"
    evidence = json.loads((output / "seed-7" / "results.json").read_text())
    assert len(evidence["calibration"]["tpe"]["trials"]) == 2
    assert (output / "report.md").exists()
