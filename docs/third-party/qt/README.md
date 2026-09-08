# Qt / Qt for Python 6.8.3 notices

Focus Pet uses **PySide6, PySide6-Essentials, PySide6-Addons and shiboken6 6.8.3** from the pinned Python environment. The desktop application uses Qt Core, Gui and Widgets. The release build filters out unused Qt PDF, QML/Quick, Virtual Keyboard, SVG and extra image-format plugins; the final bundle inventory is recorded with the release checks. Dependency wheel metadata alone did not contain the complete notice texts, so this directory preserves upstream notices separately.

`pyside-setup/LICENSES` and `qtbase/LICENSES` contain **unmodified upstream license text**, including LGPL 3, GPL 2/3, the Qt exception, and the permissive third-party licenses. The metadata declarations provide licensing alternatives; retaining a commercial-reference text does not supply a commercial license. Each third-party component retains its own terms.

The source-tree `qt_attribution.json` files preserve the original copyright holders, versions, license identifiers and usage descriptions. Referenced standalone license/notice files are copied beside those records. This is a **superset of the upstream source notices**, including upstream tools, examples and platform-specific code; it is not a claim that every listed component ships in this macOS application. In particular, this application does not ship the Qt for Python web examples.

`sources.json` records each downloaded file's official URL, byte length and SHA-256, plus the exact repository tag, commit and tree identity. The manifest can be checked without network access. The files were retrieved from the official project mirrors at tag `v6.8.3`:

- [Qt for Python / PySide source, v6.8.3](https://github.com/pyside/pyside-setup/tree/v6.8.3), also maintained in [Qt's source service](https://code.qt.io/pyside/pyside-setup.git/).
- [Qt Base source, v6.8.3](https://github.com/qt/qtbase/tree/v6.8.3), also maintained in [Qt's source service](https://code.qt.io/qt/qtbase.git/).
- [Complete Qt 6.8.3 source archive](https://download.qt.io/archive/qt/6.8/6.8.3/single/qt-everywhere-src-6.8.3.tar.xz) and [official release directory](https://download.qt.io/archive/qt/6.8/6.8.3/single/).
- [Qt for Python third-party acknowledgments](https://doc.qt.io/qtforpython-6.8/licenses.html) and [Qt licensing documentation](https://doc.qt.io/qt-6.8/licensing.html).

The complete upstream source packages have **not** been downloaded into this project. Source links above identify their location; this notice directory is not the full corresponding source. PySide's shiboken signature support also contains adapted Python 3.7 code. Its full `PSF-3.7.0.txt` license is included under `pyside-setup/sources/shiboken6/shibokenmodule/files.dir/shibokensupport/signature/`; the libshiboken attribution refers to an upstream code header rather than a standalone notice, so that header is linked by the manifest instead of copying source code into this notice collection.

## Replacing or rebuilding dynamic libraries

The application does not statically link Qt into its own Python code. In the macOS bundle, the dynamic frameworks and binding extensions remain separate files under `Focus Pet.app/Contents/Frameworks/PySide6/`, including `Qt/lib/*.framework` and `Qt/plugins/`. Shiboken's corresponding extension/library files are also separate in the bundle. Keep the complete framework layout and plugin dependencies together when making a local replacement; architecture, Qt ABI, Python ABI and library install names must remain compatible.

A reliable route for a modified Qt/PySide build is:

1. Obtain the original 6.8.3 sources above, make the desired changes, and build matching macOS arm64 Qt and Qt for Python libraries. Follow the official [Qt macOS build guide](https://doc.qt.io/qt-6.8/macos-building.html) and [Qt for Python source build guide](https://doc.qt.io/qtforpython-6.8/building_from_source/index.html). The binding build supports a Core/Gui/Widgets module subset and a standalone library layout.
2. Use a separate Python 3.11 build environment. Install the project's build dependencies, then install the modified matching Qt/PySide/shiboken build into that environment. Install Focus Pet's available source with `python -m pip install -e . --no-deps` so this step does not replace the modified bindings with pinned upstream wheels.
3. Re-run `python -m PyInstaller --noconfirm --clean FocusPet.spec` from the Focus Pet source directory using that environment. This rebuilds the application around the replacement dynamic libraries. Run the native launch and UI checks again. For direct library replacement, work on a copy of the app, preserve its library layout, and perform the same checks.
4. Re-sign the resulting local application as appropriate for the local build; an original distribution signature does not describe modified binaries. This does not require disabling global macOS security settings.

Focus Pet's own source and build specification are supplied in this project. No product-specific restriction is added on inspecting, modifying or debugging the LGPL-covered components. These rebuild instructions describe the available technical route; a modified Qt source build was not performed during this release verification.

Maintainers can refresh this notice inventory with `python scripts/fetch_qt_notices.py`. That opt-in maintenance command downloads public license/attribution texts only; it is never called by the application and sends no activity data. Recheck the retained Qt module set and corresponding notices whenever packaging dependencies change.
