from __future__ import annotations
import importlib.metadata
import platform
import re
import sys


def doctor() -> dict:
    packages = {}
    for name in (
        "PySide6",
        "numpy",
        "scikit-learn",
        "optuna",
        "pyobjc-framework-Cocoa",
        "pyobjc-framework-Quartz",
        "PyInstaller",
    ):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "not-installed"
    permissions = {"input_monitoring": "unavailable", "screen_recording": "not-requested"}
    imports: dict[str, dict] = {}
    for module in ("numpy", "sklearn.linear_model", "sklearn.ensemble", "optuna"):
        try:
            importlib.import_module(module)
            imports[module] = {"status": "available"}
        except Exception as error:
            details = []
            current: BaseException | None = error
            seen = set()
            while current is not None and id(current) not in seen:
                seen.add(id(current))
                item = {"type": type(current).__name__}
                dependency_name = getattr(current, "name", None)
                if isinstance(dependency_name, str) and re.fullmatch(r"[A-Za-z0-9_.]+", dependency_name):
                    item["module"] = dependency_name
                symbol = re.search(r"cannot import name '([A-Za-z0-9_]+)'", str(current))
                if symbol:
                    item["symbol"] = symbol[1]
                details.append(item)
                current = current.__cause__ or current.__context__
            imports[module] = {"status": "error", "causes": details}
    if sys.platform == "darwin":
        try:
            import Quartz

            permissions["input_monitoring"] = "granted" if Quartz.CGPreflightListenEventAccess() else "denied"
        except Exception:
            permissions["input_monitoring"] = "error"
    return {
        "application": "Focus Pet",
        "version": "0.1.0",
        "platform": platform.system(),
        "platform_version": platform.mac_ver()[0] if sys.platform == "darwin" else platform.release(),
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "dependencies": packages,
        "dependency_imports": imports,
        "permissions": permissions,
        "collector": "macOS native adapter"
        if sys.platform == "darwin"
        else "unsupported native / replay available",
        "network_required_for_core": False,
        "telemetry": False,
        "full_screen_detection": "unavailable",
        "system_do_not_disturb": "unavailable",
        "screen_lock": "best-effort notifications; initial locked state unavailable",
        "signing": "Development package; no Developer ID notarization configured",
    }
