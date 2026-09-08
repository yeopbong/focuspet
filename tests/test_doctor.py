import json

from focuspet import doctor as diagnostics


def test_dependency_check_reports_safe_import_failure_without_private_paths(monkeypatch):
    original = diagnostics.importlib.import_module

    def load(name):
        if name == "sklearn.linear_model":
            raise ImportError(
                "cannot import name 'missing_symbol' from '/private/confidential/session'",
                name="scipy.linalg",
            )
        return original(name)

    monkeypatch.setattr(diagnostics.importlib, "import_module", load)
    result = diagnostics.doctor()
    assert result["dependency_imports"]["numpy"]["status"] == "available"
    failed = result["dependency_imports"]["sklearn.linear_model"]
    assert failed == {
        "status": "error",
        "causes": [{"type": "ImportError", "module": "scipy.linalg", "symbol": "missing_symbol"}],
    }
    assert "confidential" not in json.dumps(result)
    assert result["permissions"]["screen_recording"] == "not-requested"
