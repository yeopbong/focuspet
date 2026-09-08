# Qt / Qt for Python 6.8.3 notices

Focus Pet uses PySide6, PySide6-Essentials, PySide6-Addons and shiboken6 6.8.3. Bundled Qt libraries and third-party components retain their own licenses.

`pyside-setup/LICENSES` and `qtbase/LICENSES` preserve upstream license texts. The `qt_attribution.json` files retain component copyrights, versions and license identifiers, with referenced notices alongside them. `sources.json` records original URLs and checksums. This directory contains notices, not the full corresponding source.

Source downloads and documentation:

- [Qt for Python / PySide source, v6.8.3](https://github.com/pyside/pyside-setup/tree/v6.8.3).
- [Qt Base source, v6.8.3](https://github.com/qt/qtbase/tree/v6.8.3).
- [Complete Qt 6.8.3 source archive](https://download.qt.io/archive/qt/6.8/6.8.3/single/qt-everywhere-src-6.8.3.tar.xz).
- [Qt for Python acknowledgments](https://doc.qt.io/qtforpython-6.8/licenses.html) and [Qt licensing](https://doc.qt.io/qt-6.8/licensing.html).

## Replacing or rebuilding dynamic libraries

Qt frameworks and binding extensions remain separate under `Focus Pet.app/Contents/Frameworks/PySide6/`, including `Qt/lib/*.framework` and `Qt/plugins/`. Shiboken libraries are also separate. Replacements must preserve architecture, Qt/Python ABI compatibility and framework/plugin layout.

1. Build matching macOS arm64 Qt and Qt for Python 6.8.3 libraries from the sources above. Follow the [Qt macOS build guide](https://doc.qt.io/qt-6.8/macos-building.html) and [Qt for Python source build guide](https://doc.qt.io/qtforpython-6.8/building_from_source/index.html).
2. In a separate Python 3.11 environment, install the project's build dependencies and the modified Qt/PySide/shiboken build. Install Focus Pet with `python -m pip install -e . --no-deps` to keep the modified bindings.
3. From the source directory, run `python -m PyInstaller --noconfirm --clean FocusPet.spec`. Check native startup and UI behavior. For direct library replacement, work on a copy and retain the complete library layout.
4. Re-sign the modified local app as appropriate for the build.

The project supplies Focus Pet's source and build specification. It adds no restriction on inspecting, modifying or debugging LGPL-covered components. Maintainers can refresh the notice inventory with `python scripts/fetch_qt_notices.py`.
