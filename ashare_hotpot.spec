# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files


datas = collect_data_files("certifi")

a = Analysis(
    ["launcher.py"],
    pathex=["src"],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # The development interpreter is Anaconda and contains many unrelated GUI,
    # notebook and scientific packages. Excluding them keeps the desktop build
    # deterministic and prevents PyInstaller from collecting a second Qt binding.
    excludes=[
        "PyQt5",
        "PyQt6",
        "tkinter",
        "_tkinter",
        "IPython",
        "jedi",
        "matplotlib",
        "numpy",
        "pandas",
        "scipy",
        "PIL",
        "lxml",
        "sphinx",
        "docutils",
        "pytest",
        "pytestqt",
        "black",
        "zmq",
        "nacl",
        "cryptography",
        "bcrypt",
        "sqlalchemy",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AshareHotPot",
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
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="AshareHotPot",
)
