"""install.sh, run end to end against a fake release served from file:// URLs (no network).

The release asset is a one-folder build (`agentry/agentry` + `agentry/_internal/`) in a
tarball. install.sh unpacks it into AGENTRY_LIB_DIR and links the executable into
AGENTRY_INSTALL_DIR. Releases from before the folder build only have the single-file binary
(or, on Linux, a tarball holding that binary), and must still install.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not shutil.which("curl"), reason="install.sh needs sh + curl"
)

REPO_ROOT = Path(__file__).resolve().parent.parent
_BASE_LINE = 'base="https://github.com/$REPO/releases/download/$tag"'


def _target() -> str:
    os_name = "macos" if sys.platform == "darwin" else "linux"
    machine = os.uname().machine
    arch = "arm64" if machine in ("arm64", "aarch64") else "x86_64"
    return f"{os_name}-{arch}"


def _fake_exe(path: Path, version: str) -> None:
    path.write_text(f'#!/bin/sh\necho "agentry {version}"\n', encoding="utf-8")
    path.chmod(0o755)


def _release(root: Path, version: str, *, kind: str) -> Path:
    """Write a fake release for `version` and return its download dir.

    kind: "folder" (the one-folder tarball), "binary" (the bare single-file asset only), or
    "binary-tarball" (a pre-0.3.5 Linux-style tarball holding the single binary itself).
    """
    rel = root / f"v{version}"
    rel.mkdir(parents=True)
    stage = root / f"stage-{version}"
    stage.mkdir()
    target = _target()
    if kind == "folder":
        (stage / "agentry" / "_internal").mkdir(parents=True)
        (stage / "agentry" / "_internal" / "lib.txt").write_text("lib", encoding="utf-8")
        _fake_exe(stage / "agentry" / "agentry", version)
        asset = rel / f"agentry-{version}-{target}.tar.gz"
        with tarfile.open(asset, "w:gz") as tar:
            tar.add(stage / "agentry", arcname="agentry")
    elif kind == "binary":
        asset = rel / f"agentry-{version}-{target}"
        _fake_exe(asset, version)
    else:
        _fake_exe(stage / "agentry", version)
        asset = rel / f"agentry-{version}-{target}.tar.gz"
        with tarfile.open(asset, "w:gz") as tar:
            tar.add(stage / "agentry", arcname="agentry")
    sha = hashlib.sha256(asset.read_bytes()).hexdigest()
    (rel / "SHA256SUMS.txt").write_text(f"{sha}  {asset.name}\n", encoding="utf-8")
    return rel


def _run(tmp_path: Path, version: str, home: Path, **env: str) -> subprocess.CompletedProcess:
    script = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
    assert _BASE_LINE in script  # the line this test redirects to the fake release
    releases = tmp_path / "releases"
    local = script.replace(_BASE_LINE, f'base="file://{releases}/$tag"')
    path = tmp_path / "install-local.sh"
    path.write_text(local, encoding="utf-8")
    full_env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "AGENTRY_VERSION": version,
        **env,
    }
    return subprocess.run(
        ["sh", str(path)], env=full_env, capture_output=True, text=True, check=False
    )


@pytest.fixture
def home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    return h


def test_folder_release_installs_into_lib_dir_and_links_bin(tmp_path: Path, home: Path):
    _release(tmp_path / "releases", "9.0.0", kind="folder")
    res = _run(tmp_path, "9.0.0", home)
    assert res.returncode == 0, res.stderr
    bin_dir, lib = home / ".local/bin", home / ".local/lib/agentry"
    assert (lib / "_internal" / "lib.txt").is_file()
    assert (bin_dir / "agentry").is_symlink()
    assert (bin_dir / "agentry").resolve() == (lib / "agentry").resolve()
    for alias in ("agy", "agyx"):
        assert os.readlink(bin_dir / alias) == "agentry"
    out = subprocess.run([str(bin_dir / "agyx")], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "agentry 9.0.0"


def test_upgrade_replaces_an_old_single_file_install(tmp_path: Path, home: Path):
    """A pre-0.3.5 install left a real binary in bin/; it becomes a link to the folder."""
    _release(tmp_path / "releases", "8.0.0", kind="binary")
    _release(tmp_path / "releases", "9.0.0", kind="folder")
    assert _run(tmp_path, "8.0.0", home).returncode == 0
    exe = home / ".local/bin/agentry"
    assert exe.is_file() and not exe.is_symlink()

    res = _run(tmp_path, "9.0.0", home)
    assert res.returncode == 0, res.stderr
    assert exe.is_symlink()
    out = subprocess.run([str(exe)], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "agentry 9.0.0"


def test_reinstall_swaps_the_folder_without_leftovers(tmp_path: Path, home: Path):
    _release(tmp_path / "releases", "9.0.0", kind="folder")
    assert _run(tmp_path, "9.0.0", home).returncode == 0
    stale = home / ".local/lib/agentry/_internal/stale.txt"
    stale.write_text("old", encoding="utf-8")
    assert _run(tmp_path, "9.0.0", home).returncode == 0
    assert not stale.exists()
    assert not (home / ".local/lib/agentry.new").exists()


def test_old_single_file_tarball_still_installs(tmp_path: Path, home: Path):
    """Linux tarballs before 0.3.5 held the binary itself, not an agentry/ folder."""
    _release(tmp_path / "releases", "8.0.0", kind="binary-tarball")
    res = _run(tmp_path, "8.0.0", home)
    assert res.returncode == 0, res.stderr
    exe = home / ".local/bin/agentry"
    assert exe.is_file() and not exe.is_symlink()
    assert not (home / ".local/lib/agentry").exists()


def test_downgrade_to_single_file_removes_the_folder(tmp_path: Path, home: Path):
    _release(tmp_path / "releases", "8.0.0", kind="binary")
    _release(tmp_path / "releases", "9.0.0", kind="folder")
    assert _run(tmp_path, "9.0.0", home).returncode == 0
    assert _run(tmp_path, "8.0.0", home).returncode == 0
    assert not (home / ".local/lib/agentry").exists()
    assert not (home / ".local/bin/agentry").is_symlink()


def test_refuses_to_replace_a_lib_dir_that_isnt_agentry(tmp_path: Path, home: Path):
    _release(tmp_path / "releases", "9.0.0", kind="folder")
    mine = home / "mine"
    mine.mkdir()
    (mine / "keep.txt").write_text("mine", encoding="utf-8")
    res = _run(tmp_path, "9.0.0", home, AGENTRY_LIB_DIR=str(mine))
    assert res.returncode != 0
    assert "isn't an agentry install" in res.stderr
    assert (mine / "keep.txt").read_text(encoding="utf-8") == "mine"


def test_custom_install_dir_puts_lib_beside_it(tmp_path: Path, home: Path):
    """The devcontainer Feature installs to /usr/local/bin; the folder lands in ../lib."""
    _release(tmp_path / "releases", "9.0.0", kind="folder")
    prefix = tmp_path / "usr-local"
    res = _run(tmp_path, "9.0.0", home, AGENTRY_INSTALL_DIR=str(prefix / "bin"))
    assert res.returncode == 0, res.stderr
    assert (prefix / "lib/agentry/_internal").is_dir()
    assert (prefix / "bin/agentry").resolve() == (prefix / "lib/agentry/agentry").resolve()
