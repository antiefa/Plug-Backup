# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parents[2]
translations = [(str(p), str(Path("locale") / p.stem / "LC_MESSAGES")) for p in (ROOT / "packaging" / "windows" / "locale").glob("*.mo")]

a = Analysis(
    [str(ROOT / "packaging" / "windows" / "entry_cli.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=translations,
    hiddenimports=[],
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
    name="plugbackup-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
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
    name="plugbackup-cli",
)
