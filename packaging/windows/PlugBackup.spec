# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parents[2]
translations = [(str(p), str(Path("locale") / p.stem / "LC_MESSAGES")) for p in (ROOT / "packaging" / "windows" / "locale").glob("*.mo")]
gui_module = ROOT / "plugbackup" / "qt" / "app.py"

a = Analysis(
    [str(ROOT / "packaging" / "windows" / "entry_gui.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=translations,
    hiddenimports=["plugbackup.qt.app"] if gui_module.is_file() else [],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    name="PlugBackup",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    manifest=str(ROOT / "packaging" / "windows" / "windows.manifest"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="PlugBackup",
)
