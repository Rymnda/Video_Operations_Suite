# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_dynamic_libs

BASE_DIR = Path.cwd()

datas = [
    (str(BASE_DIR / "assets"), "assets"),
    (str(BASE_DIR / "lang"), "lang"),
]

binaries = []
binaries += collect_dynamic_libs("PySide6")
binaries += collect_dynamic_libs("cv2")

a = Analysis(
    ['Video_Operations_Suite_v1.py'],
    pathex=[str(BASE_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=['vlc', 'cv2', 'numpy', 'psutil'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Video_Operations_Suite_Portable',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(BASE_DIR / 'assets' / 'Video_Operations_Suite_.ico'),
)
