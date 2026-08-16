from __future__ import annotations

import errno
import os
import stat
import warnings
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
import pyarrow as pa
import pyarrow.feather
import pytest

import agent.db181_multids.io as io_module
from agent.db181_multids.io import (
    empty_annotations_frame,
    materialize_file,
    sha256_file,
    write_empty_annotations,
    write_feather,
)


ANNOTATION_DTYPES = {
    "timestamp_ns": "int64",
    "track_uuid": "object",
    "category": "object",
    "length_m": "float64",
    "width_m": "float64",
    "height_m": "float64",
    "qw": "float64",
    "qx": "float64",
    "qy": "float64",
    "qz": "float64",
    "tx_m": "float64",
    "ty_m": "float64",
    "tz_m": "float64",
    "num_interior_pts": "int64",
}


@pytest.fixture
def cache_temp_dir() -> Iterator[Path]:
    cache_root = Path.cwd() / ".pytest_cache" / "db181_multids"
    cache_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=cache_root) as temporary_directory:
        yield Path(temporary_directory)


def _assert_no_sibling_temps(destination: Path) -> None:
    assert list(destination.parent.glob(f".{destination.name}.*.tmp")) == []


def test_sha256_file_matches_known_bytes(cache_temp_dir: Path) -> None:
    source = cache_temp_dir / "known.bin"
    source.write_bytes(b"abc")

    assert sha256_file(source, chunk_size=2) == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


@pytest.mark.parametrize("chunk_size", [0, -1])
def test_sha256_file_rejects_nonpositive_chunks(
    cache_temp_dir: Path, chunk_size: int
) -> None:
    source = cache_temp_dir / "source.bin"
    source.write_bytes(b"bytes")

    with pytest.raises(ValueError):
        sha256_file(source, chunk_size=chunk_size)


def test_materialize_file_uses_real_hardlink_when_supported(cache_temp_dir: Path) -> None:
    source = cache_temp_dir / "source.bin"
    destination = cache_temp_dir / "destination.bin"
    source.write_bytes(b"hardlink payload")

    probe_source = cache_temp_dir / "hardlink-probe-source.bin"
    probe_destination = cache_temp_dir / "hardlink-probe-destination.bin"
    probe_source.write_bytes(b"probe")
    try:
        os.link(probe_source, probe_destination)
    except OSError:
        hardlinks_supported = False
    else:
        hardlinks_supported = True
        probe_destination.unlink()

    if hardlinks_supported:
        method = materialize_file(source, destination)

        assert method == "hardlink"
        assert destination.read_bytes() == source.read_bytes()
        source_stat = source.stat()
        destination_stat = destination.stat()
        if source_stat.st_ino and destination_stat.st_ino:
            assert (destination_stat.st_dev, destination_stat.st_ino) == (
                source_stat.st_dev,
                source_stat.st_ino,
            )
    else:
        with pytest.raises(OSError, match="atomic no-clobber publication failed"):
            materialize_file(source, destination)

        assert not os.path.lexists(destination)
        _assert_no_sibling_temps(destination)


def test_materialize_file_falls_back_to_atomic_copy_on_source_link_error(
    cache_temp_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = cache_temp_dir / "source.bin"
    destination = cache_temp_dir / "nested" / "destination.bin"
    source.write_bytes(b"copy payload")
    source_mtime_ns = 1_700_000_000_123_456_700
    os.utime(source, ns=(source_mtime_ns, source_mtime_ns))
    real_link = io_module.os.link

    def fail_source_link(source_path: object, destination_path: object) -> None:
        if Path(source_path) == source:
            raise OSError(errno.EXDEV, "forced cross-volume source link failure")
        real_link(source_path, destination_path)

    monkeypatch.setattr(io_module.os, "link", fail_source_link)

    method = materialize_file(source, destination)

    assert method == "copy"
    assert destination.read_bytes() == source.read_bytes()
    assert destination.stat().st_mtime_ns == source.stat().st_mtime_ns
    _assert_no_sibling_temps(destination)


def test_materialize_file_reraises_hardlink_file_exists_race(
    cache_temp_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = cache_temp_dir / "source.bin"
    destination = cache_temp_dir / "destination.bin"
    source.write_bytes(b"source")

    def race_link(_source: object, _destination: object) -> None:
        destination.write_bytes(b"concurrent target")
        raise FileExistsError(
            errno.EEXIST,
            "simulated hardlink race",
            str(destination),
        )

    monkeypatch.setattr(io_module.os, "link", race_link)

    with pytest.raises(FileExistsError, match="simulated hardlink race"):
        materialize_file(source, destination)

    assert destination.read_bytes() == b"concurrent target"


def test_materialize_file_publish_race_preserves_rival_and_removes_private_temp(
    cache_temp_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = cache_temp_dir / "source.bin"
    destination = cache_temp_dir / "destination.bin"
    source.write_bytes(b"source payload")
    link_calls = 0

    def inject_rival_before_publish(_source: object, _destination: object) -> None:
        nonlocal link_calls
        link_calls += 1
        if link_calls == 1:
            raise OSError(errno.EXDEV, "forced source link failure")
        destination.write_bytes(b"rival bytes")
        raise FileExistsError(errno.EEXIST, "simulated publication race", str(destination))

    monkeypatch.setattr(io_module.os, "link", inject_rival_before_publish)

    with pytest.raises(FileExistsError, match="simulated publication race"):
        materialize_file(source, destination)

    assert destination.read_bytes() == b"rival bytes"
    _assert_no_sibling_temps(destination)


def test_materialize_file_byte_copy_interruption_leaves_no_final_or_temp(
    cache_temp_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = cache_temp_dir / "source.bin"
    destination = cache_temp_dir / "destination.bin"
    source.write_bytes(b"source payload large enough to interrupt")
    real_write = io_module.os.write

    def fail_source_link(_source: object, _destination: object) -> None:
        raise OSError(errno.EXDEV, "forced source link failure")

    def interrupt_write(descriptor: int, data: bytes) -> int:
        real_write(descriptor, data[:3])
        raise OSError("simulated byte-copy interruption")

    monkeypatch.setattr(io_module.os, "link", fail_source_link)
    monkeypatch.setattr(io_module.os, "write", interrupt_write)

    with pytest.raises(OSError, match="simulated byte-copy interruption"):
        materialize_file(source, destination)

    assert not os.path.lexists(destination)
    _assert_no_sibling_temps(destination)


def test_materialize_file_unsupported_atomic_publication_cleans_temp(
    cache_temp_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = cache_temp_dir / "source.bin"
    destination = cache_temp_dir / "destination.bin"
    source.write_bytes(b"source payload")
    link_calls = 0

    def fail_both_links(_source: object, _destination: object) -> None:
        nonlocal link_calls
        link_calls += 1
        if link_calls == 1:
            raise OSError(errno.EXDEV, "forced source link failure")
        raise OSError(errno.EOPNOTSUPP, "same-directory hardlinks unsupported")

    monkeypatch.setattr(io_module.os, "link", fail_both_links)

    with pytest.raises(OSError, match="atomic no-clobber publication failed"):
        materialize_file(source, destination)

    assert not os.path.lexists(destination)
    _assert_no_sibling_temps(destination)


def test_materialize_file_readonly_source_metadata_failure_is_best_effort(
    cache_temp_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = cache_temp_dir / "source.bin"
    destination = cache_temp_dir / "destination.bin"
    source.write_bytes(b"read-only source payload")
    source.chmod(stat.S_IREAD)
    real_link = io_module.os.link

    def fail_source_link(source_path: object, destination_path: object) -> None:
        if Path(source_path) == source:
            raise OSError(errno.EXDEV, "forced source link failure")
        real_link(source_path, destination_path)

    def fail_mode_metadata(_descriptor: int, _mode: int) -> None:
        raise OSError("forced mode metadata failure")

    def fail_time_metadata(_path: object, **_kwargs: object) -> None:
        raise OSError("forced timestamp metadata failure")

    monkeypatch.setattr(io_module.os, "link", fail_source_link)
    monkeypatch.setattr(io_module.os, "fchmod", fail_mode_metadata)
    monkeypatch.setattr(io_module.os, "utime", fail_time_metadata)

    try:
        method = materialize_file(source, destination)
    finally:
        source.chmod(stat.S_IREAD | stat.S_IWRITE)

    assert method == "copy"
    assert destination.read_bytes() == b"read-only source payload"
    _assert_no_sibling_temps(destination)


def test_materialize_file_uses_opened_source_fd_after_path_replacement(
    cache_temp_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened_source = cache_temp_dir / "opened-source.bin"
    source_path = cache_temp_dir / "source.bin"
    destination = cache_temp_dir / "destination.bin"
    opened_source.write_bytes(b"bytes from opened inode")
    source_path.write_bytes(b"replacement path bytes")
    opened_mtime_ns = 1_700_000_000_111_222_300
    replacement_mtime_ns = 1_700_000_100_444_555_600
    os.utime(opened_source, ns=(opened_mtime_ns, opened_mtime_ns))
    os.utime(source_path, ns=(replacement_mtime_ns, replacement_mtime_ns))
    real_open = io_module.os.open
    real_link = io_module.os.link

    # Windows cannot rename over an open file, so model the equivalent race:
    # the opened descriptor resolves the old inode while the path resolves its replacement.
    def open_original_inode(
        path: object,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        access_mode = flags & (os.O_WRONLY | os.O_RDWR)
        if Path(path) == source_path and access_mode == os.O_RDONLY:
            return real_open(opened_source, flags, mode, dir_fd=dir_fd)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    def fail_source_link(source: object, destination_path: object) -> None:
        if Path(source) == source_path:
            raise OSError(errno.EXDEV, "forced source link failure")
        real_link(source, destination_path)

    monkeypatch.setattr(io_module.os, "open", open_original_inode)
    monkeypatch.setattr(io_module.os, "link", fail_source_link)

    method = materialize_file(source_path, destination)

    assert method == "copy"
    assert source_path.read_bytes() == b"replacement path bytes"
    assert destination.read_bytes() == b"bytes from opened inode"
    assert destination.stat().st_mtime_ns == opened_source.stat().st_mtime_ns
    _assert_no_sibling_temps(destination)


def test_materialize_file_documents_hardlink_inode_aliasing() -> None:
    documentation = materialize_file.__doc__ or ""

    assert "inode" in documentation
    assert "affect" in documentation


def test_materialize_file_never_overwrites_existing_destination(
    cache_temp_dir: Path,
) -> None:
    source = cache_temp_dir / "source.bin"
    destination = cache_temp_dir / "destination.bin"
    source.write_bytes(b"new")
    destination.write_bytes(b"old")

    with pytest.raises(FileExistsError):
        materialize_file(source, destination, prefer_hardlink=False)

    assert destination.read_bytes() == b"old"


def test_materialize_file_rejects_non_regular_source(cache_temp_dir: Path) -> None:
    source = cache_temp_dir / "directory"
    source.mkdir()

    with pytest.raises(ValueError):
        materialize_file(source, cache_temp_dir / "destination.bin")


def test_empty_annotations_frame_has_exact_av2_schema_and_dtypes() -> None:
    frame = empty_annotations_frame()

    assert frame.empty
    assert list(frame.columns) == list(ANNOTATION_DTYPES)
    assert {column: str(dtype) for column, dtype in frame.dtypes.items()} == ANNOTATION_DTYPES


def test_write_empty_annotations_roundtrips_exact_schema(cache_temp_dir: Path) -> None:
    destination = cache_temp_dir / "nested" / "annotations.feather"

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        write_empty_annotations(destination)

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="pyarrow.feather.read_table is deprecated.*",
            category=FutureWarning,
        )
        stored = pd.read_feather(destination)
    assert stored.empty
    assert list(stored.columns) == list(ANNOTATION_DTYPES)
    stored_dtypes = {column: str(dtype) for column, dtype in stored.dtypes.items()}
    for column, expected_dtype in ANNOTATION_DTYPES.items():
        if expected_dtype == "object":
            assert stored_dtypes[column] in {"object", "str", "string"}
        else:
            assert stored_dtypes[column] == expected_dtype
    _assert_no_sibling_temps(destination)


def test_write_empty_annotations_has_exact_physical_arrow_schema(cache_temp_dir: Path) -> None:
    destination = cache_temp_dir / "annotations.feather"
    write_empty_annotations(destination)

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="pyarrow.feather.read_table is deprecated.*",
            category=FutureWarning,
        )
        table = pyarrow.feather.read_table(destination)

    expected_schema = pa.schema(
        [
            pa.field("timestamp_ns", pa.int64()),
            pa.field("track_uuid", pa.string()),
            pa.field("category", pa.string()),
            pa.field("length_m", pa.float64()),
            pa.field("width_m", pa.float64()),
            pa.field("height_m", pa.float64()),
            pa.field("qw", pa.float64()),
            pa.field("qx", pa.float64()),
            pa.field("qy", pa.float64()),
            pa.field("qz", pa.float64()),
            pa.field("tx_m", pa.float64()),
            pa.field("ty_m", pa.float64()),
            pa.field("tz_m", pa.float64()),
            pa.field("num_interior_pts", pa.int64()),
        ]
    )
    assert table.num_rows == 0
    assert table.schema.remove_metadata() == expected_schema


def test_write_feather_serialization_failure_preserves_destination_and_cleans_temp(
    cache_temp_dir: Path,
) -> None:
    destination = cache_temp_dir / "annotations.feather"
    destination.write_bytes(b"old destination")
    unserializable = pd.DataFrame({"bad": [object()]})

    with pytest.raises(Exception):
        write_feather(unserializable, destination)

    assert destination.read_bytes() == b"old destination"
    _assert_no_sibling_temps(destination)


def test_write_feather_replace_failure_preserves_destination_and_cleans_temp(
    cache_temp_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = cache_temp_dir / "annotations.feather"
    destination.write_bytes(b"old destination")

    def fail_replace(_source: object, _destination: object) -> None:
        raise OSError("forced atomic replace failure")

    monkeypatch.setattr(io_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="forced atomic replace failure"):
        write_feather(empty_annotations_frame(), destination)

    assert destination.read_bytes() == b"old destination"
    _assert_no_sibling_temps(destination)
