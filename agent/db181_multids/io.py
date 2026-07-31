from __future__ import annotations

import errno
import hashlib
import os
import shutil
import stat
import tempfile
import warnings
from pathlib import Path
from typing import Literal

import pandas as pd
import pyarrow as pa
import pyarrow.feather


_ANNOTATION_DTYPES = {
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

_ANNOTATION_ARROW_SCHEMA = pa.schema(
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


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    """Return the lowercase SHA-256 digest of a file."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")

    digest = hashlib.sha256()
    with Path(path).open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _destination_exists(path: Path) -> bool:
    return os.path.lexists(path)


def _raise_destination_exists(path: Path) -> None:
    raise FileExistsError(errno.EEXIST, "destination already exists", str(path))


def _same_file_identity(path: Path, expected: os.stat_result) -> bool:
    try:
        current = path.lstat()
    except OSError:
        return False
    return os.path.samestat(expected, current)


def _copy_file_exclusive(source: Path, destination: Path) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(destination, flags, 0o666)
    owned_identity = os.fstat(descriptor)
    completed = False
    try:
        with source.open("rb") as source_file:
            with os.fdopen(descriptor, "wb", closefd=False) as destination_file:
                shutil.copyfileobj(source_file, destination_file)
                destination_file.flush()

        source_stat = source.stat()
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, stat.S_IMODE(source_stat.st_mode))

        timestamps_ns = (source_stat.st_atime_ns, source_stat.st_mtime_ns)
        if os.utime in os.supports_fd:
            os.utime(descriptor, ns=timestamps_ns)
        else:
            if not _same_file_identity(destination, owned_identity):
                _raise_destination_exists(destination)
            os.utime(destination, ns=timestamps_ns)
            if not _same_file_identity(destination, owned_identity):
                _raise_destination_exists(destination)
        completed = True
    finally:
        os.close(descriptor)
        if not completed and _same_file_identity(destination, owned_identity):
            destination.unlink()


def materialize_file(
    src: str | Path,
    dst: str | Path,
    prefer_hardlink: bool = True,
) -> Literal["hardlink", "copy"]:
    """Materialize a file without overwriting an existing destination.

    A ``hardlink`` result aliases the source inode: modifying either path will
    affect the same file contents and metadata.
    """
    source = Path(src)
    destination = Path(dst)
    if not source.is_file():
        raise ValueError(f"source must be a regular file: {source}")
    if _destination_exists(destination):
        _raise_destination_exists(destination)

    destination.parent.mkdir(parents=True, exist_ok=True)
    if prefer_hardlink:
        try:
            os.link(source, destination)
            return "hardlink"
        except FileExistsError:
            raise
        except OSError:
            pass

    if _destination_exists(destination):
        _raise_destination_exists(destination)
    _copy_file_exclusive(source, destination)
    return "copy"


def write_feather(frame: pd.DataFrame, path: str | Path) -> None:
    """Atomically write a DataFrame as Feather via a sibling temporary file."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
        payload: pd.DataFrame | pa.Table = frame
        if tuple(frame.columns) == tuple(_ANNOTATION_ARROW_SCHEMA.names):
            payload = pa.Table.from_pandas(
                frame,
                schema=_ANNOTATION_ARROW_SCHEMA,
                preserve_index=False,
            )
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="pyarrow.feather.write_feather is deprecated.*",
                category=FutureWarning,
            )
            pyarrow.feather.write_feather(payload, temporary_path)
        temporary_path.replace(destination)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def empty_annotations_frame() -> pd.DataFrame:
    """Return a zero-row DataFrame with the db89-compatible AV2 schema."""
    return pd.DataFrame(
        {
            column: pd.Series(dtype=dtype)
            for column, dtype in _ANNOTATION_DTYPES.items()
        }
    )


def write_empty_annotations(path: str | Path) -> None:
    """Write a schema-correct empty annotations Feather file."""
    write_feather(empty_annotations_frame(), path)
