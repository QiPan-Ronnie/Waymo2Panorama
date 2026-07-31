from __future__ import annotations

import errno
import hashlib
import os
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


def _copy_fd(source_descriptor: int, destination_descriptor: int) -> None:
    while chunk := os.read(source_descriptor, 1024 * 1024):
        remaining = memoryview(chunk)
        while remaining:
            written = os.write(destination_descriptor, remaining)
            if written <= 0:
                raise OSError(errno.EIO, "copy write made no progress")
            remaining = remaining[written:]


def _apply_private_temp_metadata_best_effort(
    descriptor: int,
    path: Path,
    source_stat: os.stat_result,
) -> None:
    source_mode = stat.S_IMODE(source_stat.st_mode)
    if source_mode & stat.S_IWUSR and hasattr(os, "fchmod"):
        try:
            os.fchmod(descriptor, source_mode)
        except (NotImplementedError, OSError):
            pass

    timestamps_ns = (source_stat.st_atime_ns, source_stat.st_mtime_ns)
    try:
        if os.utime in os.supports_fd:
            os.utime(descriptor, ns=timestamps_ns)
        else:
            os.utime(path, ns=timestamps_ns)
    except (NotImplementedError, OSError):
        pass


def _copy_file_with_atomic_publish(source: Path, destination: Path) -> None:
    source_descriptor: int | None = None
    temporary_descriptor: int | None = None
    temporary_path: Path | None = None
    try:
        source_descriptor = os.open(
            source,
            os.O_RDONLY | getattr(os, "O_BINARY", 0),
        )
        source_stat = os.fstat(source_descriptor)
        if not stat.S_ISREG(source_stat.st_mode):
            raise ValueError(f"source must be a regular file: {source}")

        temporary_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        temporary_path = Path(temporary_name)
        _copy_fd(source_descriptor, temporary_descriptor)
        _apply_private_temp_metadata_best_effort(
            temporary_descriptor,
            temporary_path,
            source_stat,
        )
        os.fsync(temporary_descriptor)
        os.close(temporary_descriptor)
        temporary_descriptor = None

        try:
            os.link(temporary_path, destination)
        except FileExistsError:
            raise
        except OSError as error:
            error_number = error.errno if error.errno is not None else errno.EIO
            raise OSError(
                error_number,
                f"atomic no-clobber publication failed for {destination}",
            ) from error
    finally:
        if temporary_descriptor is not None:
            os.close(temporary_descriptor)
        if source_descriptor is not None:
            os.close(source_descriptor)
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def materialize_file(
    src: str | Path,
    dst: str | Path,
    prefer_hardlink: bool = True,
) -> Literal["hardlink", "copy"]:
    """Materialize a file without overwriting an existing destination.

    A ``hardlink`` result aliases the source inode: modifying either path will
    affect the same file contents and metadata. The copy fallback publishes a
    complete private sibling atomically and may skip restrictive mode bits to
    keep cleanup safe.
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

    _copy_file_with_atomic_publish(source, destination)
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
