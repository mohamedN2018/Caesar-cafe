# PyInstaller spec — builds CaesarPOS.exe
#
#   cd desktop && pyinstaller packaging/caesar_pos.spec
#
# Note on obfuscation: there is none, deliberately. Packers and anti-debugging
# cost real engineering time, break on Windows updates, trip antivirus
# heuristics, and delay a determined attacker by about an afternoon. The licence
# design does not depend on the binary being opaque — see docs/06.

import sys
from pathlib import Path

SPEC_DIR = Path(SPECPATH)
PROJECT = SPEC_DIR.parent

block_cipher = None

a = Analysis(
    [str(PROJECT / "src" / "caesar_pos" / "app.py")],
    pathex=[str(PROJECT / "src")],
    binaries=[],
    datas=[],
    hiddenimports=[
        # keyring resolves its backend at runtime, so PyInstaller cannot see it.
        "keyring.backends.Windows",
        "keyring.backends.SecretService",
        "keyring.backends.fail",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Qt modules the POS never touches. Dropping them saves ~80MB on a
        # machine that may well be an aging counter PC.
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuick3D",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.Qt3DCore",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtMultimedia",
        "tkinter",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CaesarPOS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX is a common antivirus false-positive trigger
    console=False,      # a GUI app: no console window behind it
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT / "packaging" / "icon.ico")
    if (PROJECT / "packaging" / "icon.ico").exists()
    else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="CaesarPOS",
)
