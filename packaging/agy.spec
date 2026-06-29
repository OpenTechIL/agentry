# packaging/agy.spec — one-file build for the `agy` CLI.
# Run from the repo root: `uv run --extra build pyinstaller packaging/agy.spec`
import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

# ruamel.yaml ships C extensions + plugins PyInstaller misses; pydantic builds
# models dynamically. Collect both explicitly.
datas, binaries, hiddenimports = collect_all("ruamel.yaml")
hiddenimports += collect_submodules("pydantic")
hiddenimports += collect_submodules("pydantic_core")

entry = os.path.join(SPECPATH, "entry.py")
src = os.path.join(SPECPATH, "..", "src")

a = Analysis(
    [entry],
    pathex=[src],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="agy",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
