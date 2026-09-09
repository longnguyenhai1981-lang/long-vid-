from __future__ import annotations

import pytest

from app.visual.errors import VisualPathError, VisualWriteError
from app.visual.storage import VisualFileStore


def test_write_creates_file_with_exact_bytes(tmp_path):
    store = VisualFileStore(tmp_path)
    result_path = store.write("proj/plan/V001_R1.png", b"some-visual-bytes")

    assert result_path == tmp_path / "proj" / "plan" / "V001_R1.png"
    assert result_path.read_bytes() == b"some-visual-bytes"


def test_write_returns_absolute_path_under_root(tmp_path):
    store = VisualFileStore(tmp_path)
    result_path = store.write("sub/dir/V001_R1.png", b"data")
    assert result_path.is_absolute()
    assert result_path == tmp_path / "sub" / "dir" / "V001_R1.png"


def test_write_creates_missing_parent_directories(tmp_path):
    store = VisualFileStore(tmp_path)
    store.write("nested/does/not/exist/yet.png", b"data")
    assert (tmp_path / "nested" / "does" / "not" / "exist" / "yet.png").exists()


def test_write_is_atomic_no_leftover_temp_file(tmp_path):
    store = VisualFileStore(tmp_path)
    store.write("V001_R1.png", b"data")
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


def test_root_property_returns_configured_root(tmp_path):
    store = VisualFileStore(tmp_path)
    assert store.root == tmp_path


@pytest.mark.parametrize(
    "bad_path",
    [
        "",
        "   ",
        "/etc/passwd",
        "C:/Windows/System32/evil.png",
        "C:\\Windows\\System32\\evil.png",
        "../escape.png",
        "..\\escape.png",
        "sub/../../escape.png",
        "\\\\server\\share\\file.png",
    ],
)
def test_write_rejects_unsafe_relative_path(tmp_path, bad_path):
    store = VisualFileStore(tmp_path)
    with pytest.raises(VisualPathError):
        store.write(bad_path, b"data")


def test_rejected_write_creates_no_file(tmp_path):
    store = VisualFileStore(tmp_path)
    with pytest.raises(VisualPathError):
        store.write("../escape.png", b"data")
    assert list(tmp_path.parent.glob("escape.png")) == []


def test_write_failure_wrapped_as_visual_write_error_and_leaves_no_temp_file(tmp_path):
    store = VisualFileStore(tmp_path)
    # "blocker" exists as a plain FILE, so treating it as a directory for
    # "blocker/asset.png" must fail at the filesystem level.
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"not a directory")

    with pytest.raises(VisualWriteError):
        store.write("blocker/asset.png", b"data")

    assert list(tmp_path.glob("*.tmp")) == []
    assert list(tmp_path.glob("blocker/*")) == []


def test_write_overwrites_existing_file_at_same_path(tmp_path):
    store = VisualFileStore(tmp_path)
    store.write("V001_R1.png", b"first-version")
    result_path = store.write("V001_R1.png", b"second-version")
    assert result_path.read_bytes() == b"second-version"
