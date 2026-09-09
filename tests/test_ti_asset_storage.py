from __future__ import annotations

from uuid import uuid4

import pytest

from app.models.common import VisualOutputFormat
from app.models.ti_assets import TiState
from app.ti_assets.errors import TiAssetPathError, TiAssetWriteError
from app.ti_assets.storage import TiAssetFileStore, build_relative_path


def test_build_relative_path_convention():
    asset_set_id = uuid4()
    path = build_relative_path(asset_set_id, "v1", TiState.NEUTRAL, VisualOutputFormat.PNG)
    assert path == f"{asset_set_id}/v1/NEUTRAL.png"


def test_build_relative_path_uses_jpg_extension_for_jpg_format():
    asset_set_id = uuid4()
    path = build_relative_path(asset_set_id, "v2", TiState.PANIC, VisualOutputFormat.JPG)
    assert path == f"{asset_set_id}/v2/PANIC.jpg"


def test_write_creates_file_with_exact_bytes(tmp_path):
    store = TiAssetFileStore(tmp_path)
    result_path = store.write("set1/v1/NEUTRAL.png", b"some-bytes")
    assert result_path == tmp_path / "set1" / "v1" / "NEUTRAL.png"
    assert result_path.read_bytes() == b"some-bytes"


def test_write_creates_missing_parent_directories(tmp_path):
    store = TiAssetFileStore(tmp_path)
    store.write("nested/does/not/exist/NEUTRAL.png", b"data")
    assert (tmp_path / "nested" / "does" / "not" / "exist" / "NEUTRAL.png").exists()


def test_write_is_atomic_no_leftover_temp_file(tmp_path):
    store = TiAssetFileStore(tmp_path)
    store.write("NEUTRAL.png", b"data")
    assert list(tmp_path.glob("*.tmp")) == []


def test_exists_true_after_write(tmp_path):
    store = TiAssetFileStore(tmp_path)
    store.write("set1/v1/NEUTRAL.png", b"data")
    assert store.exists("set1/v1/NEUTRAL.png") is True


def test_exists_false_for_never_written_file(tmp_path):
    store = TiAssetFileStore(tmp_path)
    assert store.exists("set1/v1/NEUTRAL.png") is False


def test_resolve_returns_absolute_path_without_touching_disk(tmp_path):
    store = TiAssetFileStore(tmp_path)
    resolved = store.resolve("set1/v1/NEUTRAL.png")
    assert resolved == tmp_path / "set1" / "v1" / "NEUTRAL.png"
    assert not resolved.exists()


def test_root_property_returns_configured_root(tmp_path):
    store = TiAssetFileStore(tmp_path)
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
    store = TiAssetFileStore(tmp_path)
    with pytest.raises(TiAssetPathError):
        store.write(bad_path, b"data")


@pytest.mark.parametrize("bad_path", ["", "../escape.png"])
def test_exists_rejects_unsafe_relative_path(tmp_path, bad_path):
    store = TiAssetFileStore(tmp_path)
    with pytest.raises(TiAssetPathError):
        store.exists(bad_path)


def test_write_failure_wrapped_as_ti_asset_write_error(tmp_path):
    store = TiAssetFileStore(tmp_path)
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"not a directory")

    with pytest.raises(TiAssetWriteError):
        store.write("blocker/NEUTRAL.png", b"data")

    assert list(tmp_path.glob("*.tmp")) == []


def test_write_overwrites_existing_file_at_same_path(tmp_path):
    store = TiAssetFileStore(tmp_path)
    store.write("NEUTRAL.png", b"first")
    result_path = store.write("NEUTRAL.png", b"second")
    assert result_path.read_bytes() == b"second"
