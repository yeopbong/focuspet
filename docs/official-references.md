# Implementation references

Official documentation consulted during implementation and checked against installed runtime behavior:

- [Qt for Python QWidget](https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QWidget.html): transparent widgets, masks, window flags and activation behavior. Installed Qt6.8.3 was exercised natively through pytest-qt and bounded Cocoa launches.
- [Qt QSystemTrayIcon](https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QSystemTrayIcon.html): tray activation and persistent recovery controls.
- [PyObjC introduction](https://pyobjc.readthedocs.io/en/latest/core/intro.html): Objective-C class uniqueness and bridging. The observer class is cached and instance owners are weakly referenced; actual Foundation object allocation/routing was verified.
- [Apple Quartz Event Services](https://developer.apple.com/documentation/coregraphics/quartz-event-services): listen-only taps, event masks, counters and enabling/disabling. Permission preflight was environment-dependent: granted with ordinary host access and denied under restricted execution. Consented native event reception is explicitly not claimed tested.
- [scikit-learn time series splitting](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html): chronological evaluation and gap handling. This application implements stricter whole-day/episode grouping rather than shuffling overlapping windows.
- [scikit-learn LogisticRegression](https://scikit-learn.org/1.6/modules/generated/sklearn.linear_model.LogisticRegression.html) and [RandomForestClassifier](https://scikit-learn.org/1.6/modules/generated/sklearn.ensemble.RandomForestClassifier.html): actual1.6.1 fitting/scaling exercised by experiments and artifact round-trip tests.
- [Optuna samplers](https://optuna.readthedocs.io/en/v4.2.1/reference/samplers/index.html): RandomSampler and TPESampler, seeded searches with recorded objective calls.
- [PyInstaller6.12 usage](https://pyinstaller.org/en/v6.12.0/usage.html) and [hook metadata helpers](https://pyinstaller.org/en/stable/hooks.html): native bundle construction, resource collection, metadata and hidden dependencies. Standalone startup is verified separately from successful bundle construction.

Documentation is implementation guidance, not evidence that the product's workload scale or classifier is physiologically validated.
