from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

assets = collect_data_files('focuspet') + copy_metadata('focuspet', recursive=True)
assets += copy_metadata('PyInstaller')
assets += [('docs/third-party', 'licenses/third-party'), ('LICENSE', 'licenses')]
assets += [('.runtime/build-info.json', 'focuspet/assets')]
hidden = collect_submodules('focuspet') + ['backports', 'backports.tarfile', 'scipy._cyutility']
a = Analysis(['scripts/desktop.py'], pathex=['src'], binaries=[], datas=assets,
             hiddenimports=hidden, hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=['tkinter', 'matplotlib', 'pandas', 'PySide6.QtWebEngineCore',
                       'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick',
                       'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtQuickWidgets'],
             noarchive=False)
def needed(entry):
    path = entry[0].replace('\\', '/').lower()
    if path.endswith('/direct_url.json'):
        return False
    if any('/plugins/' + name + '/' in path for name in
           ('imageformats', 'iconengines', 'platforminputcontexts')):
        return False
    return not any(module in path for module in
                   ('qtpdf', 'qtqml', 'qtquick', 'qtvirtualkeyboard', 'qtsvg'))

a.binaries = [entry for entry in a.binaries if needed(entry)]
a.datas = [entry for entry in a.datas if needed(entry)]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='FocusPet', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False,
          argv_emulation=False, target_arch='arm64', codesign_identity=None,
          entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='FocusPet')
app = BUNDLE(coll, name='Focus Pet.app', icon='src/focuspet/assets/app.icns',
             bundle_identifier='app.focuspet.companion',
             info_plist={'CFBundleDisplayName': 'Focus Pet', 'CFBundleShortVersionString': '0.1.0',
                         'NSHighResolutionCapable': True, 'LSUIElement': True,
                         'LSMinimumSystemVersion': '14.0',
                         'NSInputMonitoringUsageDescription': 'Focus Pet counts input activity after your consent. It never reads typed characters or records mouse coordinates.'})
