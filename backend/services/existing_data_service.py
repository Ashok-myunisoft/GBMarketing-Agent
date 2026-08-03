"""Managed access to the files used as the deduplication baseline."""

from __future__ import annotations

import os
import threading
from pathlib import Path

from fastapi import HTTPException, UploadFile


BACKEND_DIR = Path(__file__).resolve().parent.parent
EXISTING_DATA_DIR = BACKEND_DIR / "Existing-data"
ALLOWED_SUFFIXES = {".csv", ".xlsx"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


class ExistingDataService:
    """Lists and replaces only CSV/XLSX files in the existing-data folder."""

    def __init__(self, directory: Path = EXISTING_DATA_DIR):
        self._directory = directory
        self._lock = threading.Lock()

    def list_files(self) -> list[dict[str, int | str]]:
        self._directory.mkdir(parents=True, exist_ok=True)
        return [
            {"name": path.name, "size": path.stat().st_size, "updated_at": int(path.stat().st_mtime)}
            for path in sorted(self._directory.iterdir(), key=lambda item: item.name.lower())
            if path.is_file() and path.suffix.lower() in ALLOWED_SUFFIXES
        ]

    async def upload(self, file: UploadFile) -> dict[str, int | str]:
        filename = self._validated_filename(file.filename)
        self._directory.mkdir(parents=True, exist_ok=True)
        target = self._directory / filename
        temporary = self._directory / f".{filename}.uploading"

        with self._lock:
            if target.exists():
                raise HTTPException(status_code=409, detail="A file with this name already exists. Delete it first.")
            written = 0
            try:
                with temporary.open("xb") as handle:
                    while chunk := await file.read(1024 * 1024):
                        written += len(chunk)
                        if written > MAX_UPLOAD_BYTES:
                            raise HTTPException(status_code=413, detail="File must be 25 MB or smaller.")
                        handle.write(chunk)
                os.replace(temporary, target)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
            finally:
                await file.close()

        return {"name": filename, "size": written, "updated_at": int(target.stat().st_mtime)}

    def delete(self, filename: str) -> None:
        target = self._directory / self._validated_filename(filename)
        with self._lock:
            if not target.is_file():
                raise HTTPException(status_code=404, detail="Existing-data file not found.")
            target.unlink()

    @staticmethod
    def _validated_filename(filename: str | None) -> str:
        name = Path(filename or "").name
        if not name or name != filename or Path(name).suffix.lower() not in ALLOWED_SUFFIXES:
            raise HTTPException(status_code=400, detail="Upload a .csv or .xlsx file with a simple filename.")
        return name
