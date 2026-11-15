# packaging/agentry.spec — PyInstaller build for the `agentry` CLI.
#
# Default: a one-folder build, dist/agentry/ = the `agentry` executable + `_internal/`.
# Every installer ships this folder. It starts in ~0.2s. A one-file build unpacks itself to
# a new temp dir on every run, and macOS scans the unpacked, unsigned libraries each time,
# because the path is new every time. That cost ~5s per command.
#
# AGENTRY_ONEFILE=1: the one-file build (dist/agentry), still published as the bare
# `agentry-<version>-<target>` download for anyone who wants a single file. Build it into a
# separate --distpath / --workpath so the two builds don't collide.
#
# The `agy` and `agyx` aliases are created next to the executable by whichever channel
# installs it (install.sh/.ps1, nfpm, Homebrew, Scoop, Inno Setup, the macOS .pkg).
#
# Run from the repo root:
#   uv run --extra build pyinstaller packaging/agentry.spec
#   AGENTRY_ONEFILE=1 uv run --extra build pyinstaller packaging/agentry.spec \
#     --distpath dist-onefile --workpath build-onefile
import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

ONEFILE = os.environ.get("AGENTRY_ONEFILE") == "1"

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

_exe_options = dict(
    name="agentry",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

if ONEFILE:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], runtime_tmpdir=None, **_exe_options)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **_exe_options)
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="agentry")
