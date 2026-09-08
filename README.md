# Focus Pet

A local desktop companion that estimates work rhythms from activity counts, shows a quiet pixel character, and learns from voluntary feedback. The desktop application is Python with PySide6; the offline browser demo plays trajectories computed by the same Python core.

[Repository](https://github.com/yeopbong/focuspet) · [中文使用说明](docs/usage-zh.md) · [Privacy](docs/privacy.md) · [Validation status](docs/platform-status.md)

**Browser demo:** the planned Pages address is `https://yeopbong.github.io/focuspet/`. Deployment is pending verification. Open `demo/index.html` locally to use the offline demo.

![Focus Pet desktop demo](docs/screenshots/native-demo.png)

## Run from source

The native target is **macOS 14+ on Apple Silicon**, with **Python 3.11**. Windows and Linux do not have a working global activity collector in this release. Check [platform status](docs/platform-status.md) for the versions and behaviors actually tested.

```sh
git clone https://github.com/yeopbong/focuspet.git
cd focuspet
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-macos-arm64.lock
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
.venv/bin/focuspet demo
```

Use `focuspet doctor` to inspect capabilities and `focuspet run` to start normal mode. Collection requires explicit in-app consent; macOS Input Monitoring permission is a separate condition. Declining collection leaves the companion and isolated demo usable. Nothing is uploaded, and no account is required.

## Use the companion

Double-click the character for state and feedback. Right-click for rest, snooze, quiet mode and settings; use the menu-bar tray to pause collection or recover a hidden or click-through character. The dashboard shows the day's timeline, Focus and Work Load, corrections, evidence counts and model versions. Four interchangeable characters share the same behavior.

Focus describes estimated engagement, not productivity or correctness. Work Load is an engineering index with a reference line at 100, not a medical fatigue measurement. The initial classifier is a readable rule prior. Ambiguous, missing or unsupported observations can produce **Unknown**.

Voluntary state corrections train Logistic Regression and Random Forest candidates on past episodes. Held-out validation selects a model and prior-fusion weight; later independent audit feedback must support activation. Separate wants-rest self-reports calibrate workload parameters using equal-budget Random Search and TPE. Training, shadow status and rollback remain visible. [Exact evidence gates and formulas](docs/models-and-parameters.md).

The app records aggregate counts and broad application categories. It does not record typed text, keycodes, window titles, clipboard contents, page content, screenshots or pointer coordinates. Data stays in separate `real`, `synthetic-demo` and `test` directories under `~/Library/Application Support/Focus Pet/`; local data is not application-encrypted. Settings provide retention, export and deletion controls. [Privacy and data fields](docs/privacy.md).

## Replay and experiments

```sh
.venv/bin/focuspet scenarios
.venv/bin/focuspet replay reading --output .runtime/reading.json
.venv/bin/focuspet replay history.json --recorded --output .runtime/history.json
.venv/bin/focuspet evaluate --config experiments/config.json
.venv/bin/focuspet export-demo --scenario workday --output demo
```

Export replay distinguishes unchanged historical snapshots, strict recomputation with original versions, and explicit re-evaluation with chosen versions. Missing historical evidence causes strict recomputation to fail clearly. [Commands and limits](docs/export-replay.md).

The five-seed simulation uses fixed time splits, 300-second purges and equal label/search budgets. It reports per-class support, rejection coverage and every optimizer trial. These are simulated results, not evidence of real personal accuracy or improved work habits. [Protocol](docs/experiment-protocol.md) · [Results](experiments/results/report.md).

## Develop and package

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests scripts experiments examples
.venv/bin/python -m mypy src/focuspet
.venv/bin/python -m build --no-isolation
./scripts/build_macos.sh
```

Native Qt tests need a desktop session. Pure tests can run with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, excluding `tests/test_ui.py`. The macOS build script writes `dist/Focus Pet.app`; signing, notarization, release assets and long-run checks are tracked in [validation status](docs/platform-status.md).

[Architecture](docs/architecture.md) · [Data dictionary](docs/data-dictionary.md) · [Characters](docs/characters.md) · [Known limitations](docs/known-limitations.md) · [Native checklist](docs/native-testing.md)

Source code is [MIT](LICENSE). Character assets are dedicated under CC0-1.0 to the extent rights can be dedicated; see their manifests. Dependencies retain their own [licenses and notices](docs/third-party/README.md).
