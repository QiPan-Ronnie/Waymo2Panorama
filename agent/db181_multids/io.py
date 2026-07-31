from __future__ import annotations

import errno
import hashlib
import os
import shutil
import tempfile
import warnings
from pathlib import Path
from typing import Literal

import pandas as pd
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


def materialize_file(
    src: str | Path,
    dst: str | Path,
    prefer_hardlink: bool = True,
) -> Literal["hardlink", "copy"]:
    """Materialize a source file without overwriting an existing destination."""
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
        except OSError:
            if _destination_exists(destination):
                _raise_destination_exists(destination)

    if _destination_exists(destination):
        _raise_destination_exists(destination)
    shutil.copy2(source, destination)
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
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="pyarrow.feather.write_feather is deprecated.*",
                category=FutureWarning,
            )
            pyarrow.feather.write_feather(frame, temporary_path)
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
