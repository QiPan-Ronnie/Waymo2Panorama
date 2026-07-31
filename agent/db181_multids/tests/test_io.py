from __future__ import annotations

import errno
import os
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

    method = materialize_file(source, destination)

    assert destination.read_bytes() == source.read_bytes()
    if hardlinks_supported:
        assert method == "hardlink"
        source_stat = source.stat()
        destination_stat = destination.stat()
        if source_stat.st_ino and destination_stat.st_ino:
            assert (destination_stat.st_dev, destination_stat.st_ino) == (
                source_stat.st_dev,
                source_stat.st_ino,
            )
    else:
        assert method == "copy"


def test_materialize_file_falls_back_to_copy2_on_link_error(
    cache_temp_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = cache_temp_dir / "source.bin"
    destination = cache_temp_dir / "nested" / "destination.bin"
    source.write_bytes(b"copy payload")
    source_mtime_ns = 1_700_000_000_123_456_700
    os.utime(source, ns=(source_mtime_ns, source_mtime_ns))

    def fail_link(_source: object, _destination: object) -> None:
        raise OSError("forced hardlink failure")

    monkeypatch.setattr(io_module.os, "link", fail_link)

    method = materialize_file(source, destination)

    assert method == "copy"
    assert destination.read_bytes() == source.read_bytes()
    assert destination.stat().st_mtime_ns == source.stat().st_mtime_ns


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


def test_materialize_file_copy_interruption_removes_owned_partial_destination(
    cache_temp_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = cache_temp_dir / "source.bin"
    destination = cache_temp_dir / "destination.bin"
    source.write_bytes(b"source payload")
    real_utime = io_module.os.utime

    def interrupt_windows_copy_file2(
        _source: object, destination_path: object, _flags: object
    ) -> None:
        Path(destination_path).write_bytes(b"partial")
        error = OSError("simulated metadata-copy interruption")
        error.winerror = 12345  # type: ignore[attr-defined]
        raise error

    windows_api = getattr(io_module.shutil, "_winapi", None)
    if windows_api is not None and hasattr(windows_api, "CopyFile2"):
        monkeypatch.setattr(windows_api, "CopyFile2", interrupt_windows_copy_file2)

    def fail_destination_utime(path: object, *args: object, **kwargs: object) -> None:
        if isinstance(path, int) or Path(path) == destination:
            raise OSError("simulated metadata-copy interruption")
        real_utime(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(io_module.os, "utime", fail_destination_utime)

    with pytest.raises(OSError, match="simulated metadata-copy interruption"):
        materialize_file(source, destination, prefer_hardlink=False)

    assert not os.path.lexists(destination)


def test_materialize_file_symlink_race_never_touches_link_target(
    cache_temp_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    symlink_probe_target = cache_temp_dir / "symlink-probe-target.bin"
    symlink_probe = cache_temp_dir / "symlink-probe.bin"
    symlink_probe_target.write_bytes(b"probe")
    try:
        os.symlink(symlink_probe_target, symlink_probe)
    except (NotImplementedError, OSError):
        pytest.skip("file symlinks are not available in this test environment")
    else:
        symlink_probe.unlink()

    source = cache_temp_dir / "source.bin"
    destination = cache_temp_dir / "destination.bin"
    victim = cache_temp_dir / "victim.bin"
    source.write_bytes(b"new source bytes")
    victim.write_bytes(b"victim must stay unchanged")
    real_lexists = io_module.os.path.lexists
    destination_checks = 0

    def inject_symlink_after_check(path: object) -> bool:
        nonlocal destination_checks
        if Path(path) == destination:
            destination_checks += 1
            if destination_checks == 2:
                os.symlink(victim, destination)
                return False
        return real_lexists(path)

    monkeypatch.setattr(io_module.os.path, "lexists", inject_symlink_after_check)

    with pytest.raises(FileExistsError):
        materialize_file(source, destination, prefer_hardlink=False)

    assert victim.read_bytes() == b"victim must stay unchanged"
    assert destination.is_symlink()


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
