from __future__ import annotations

import pytest

from app.audio.errors import AudioPathError, AudioWriteError
from app.audio.storage import AudioFileStore


def test_write_creates_file_with_exact_bytes(tmp_path):
    store = AudioFileStore(tmp_path)
    result_path = store.write("C001_T1.wav", b"some-audio-bytes")

    assert result_path == tmp_path / "C001_T1.wav"
    assert result_path.read_bytes() == b"some-audio-bytes"


def test_write_returns_absolute_path_under_root(tmp_path):
    store = AudioFileStore(tmp_path)
    result_path = store.write("sub/dir/C001_T1.wav", b"data")
    assert result_path.is_absolute()
    assert result_path == tmp_path / "sub" / "dir" / "C001_T1.wav"


def test_write_creates_missing_parent_directories(tmp_path):
    store = AudioFileStore(tmp_path)
    store.write("nested/does/not/exist/yet.wav", b"data")
    assert (tmp_path / "nested" / "does" / "not" / "exist" / "yet.wav").exists()


def test_write_is_atomic_no_leftover_temp_file(tmp_path):
    store = AudioFileStore(tmp_path)
    store.write("C001_T1.wav", b"data")
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


def test_root_property_returns_configured_root(tmp_path):
    store = AudioFileStore(tmp_path)
    assert store.root == tmp_path


@pytest.mark.parametrize(
    "bad_path",
    [
        "",
        "   ",
        "/etc/passwd",
        "C:/Windows/System32/evil.wav",
        "C:\\Windows\\System32\\evil.wav",
        "../escape.wav",
        "..\\escape.wav",
        "sub/../../escape.wav",
        "\\\\server\\share\\file.wav",
    ],
)
def test_write_rejects_unsafe_relative_path(tmp_path, bad_path):
    store = AudioFileStore(tmp_path)
    with pytest.raises(AudioPathError):
        store.write(bad_path, b"data")


def test_rejected_write_creates_no_file(tmp_path):
    store = AudioFileStore(tmp_path)
    with pytest.raises(AudioPathError):
        store.write("../escape.wav", b"data")
    assert list(tmp_path.parent.glob("escape.wav")) == []


def test_write_failure_wrapped_as_audio_write_error_and_leaves_no_temp_file(tmp_path):
    store = AudioFileStore(tmp_path)
    # "blocker" exists as a plain FILE, so treating it as a directory for
    # "blocker/take.wav" must fail at the filesystem level.
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"not a directory")

    with pytest.raises(AudioWriteError):
        store.write("blocker/take.wav", b"data")

    assert list(tmp_path.glob("*.tmp")) == []
    assert list(tmp_path.glob("blocker/*")) == []


def test_write_overwrites_existing_file_at_same_path(tmp_path):
    store = AudioFileStore(tmp_path)
    store.write("C001_T1.wav", b"first-version")
    result_path = store.write("C001_T1.wav", b"second-version")
    assert result_path.read_bytes() == b"second-version"
