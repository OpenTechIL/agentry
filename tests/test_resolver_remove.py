"""`resolver._remove` must delete a store clone even when parts of it are read-only.

git marks pack files read-only, which Windows refuses to unlink; a read-only *directory*
blocks unlinking its entries on POSIX too. The rmtree hook clears both, so this test catches
a regression on every OS without needing Windows.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from agentry.resolver import _remove


@pytest.fixture
def readonly_tree(tmp_path: Path):
    root = tmp_path / "clone"
    pack = root / ".git" / "objects" / "pack"
    pack.mkdir(parents=True)
    (pack / "pack-1.pack").write_bytes(b"x")
    (pack / "pack-1.idx").write_bytes(b"x")
    locked = root / "locked"
    locked.mkdir()
    (locked / "inside.txt").write_text("x", encoding="utf-8")
    ro_file = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH
    for f in (pack / "pack-1.pack", pack / "pack-1.idx", locked / "inside.txt"):
        os.chmod(f, ro_file)
    os.chmod(locked, stat.S_IRUSR | stat.S_IXUSR)  # read-only directory
    yield root
    # If the test failed, make the tree removable so pytest's tmp cleanup still works.
    if root.exists():
        for dirpath, _dirnames, filenames in os.walk(root):
            os.chmod(dirpath, stat.S_IRWXU)
            for name in filenames:
                os.chmod(os.path.join(dirpath, name), stat.S_IRUSR | stat.S_IWUSR)


def test_remove_clears_readonly_files(readonly_tree: Path):
    _remove(readonly_tree)
    assert not readonly_tree.exists()


def test_remove_deletes_a_single_readonly_file(tmp_path: Path):
    f = tmp_path / "ro.txt"
    f.write_text("x", encoding="utf-8")
    os.chmod(f, stat.S_IRUSR)
    _remove(f)
    assert not f.exists()


def test_remove_unlinks_a_symlink_without_touching_its_target(tmp_path: Path):
    target = tmp_path / "real"
    target.mkdir()
    (target / "keep.txt").write_text("x", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    _remove(link)
    assert not link.exists() and not link.is_symlink()
    assert (target / "keep.txt").exists()
