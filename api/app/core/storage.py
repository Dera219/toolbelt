"""Blob storage seam. Dev serves from local disk via /uploads; production swaps in
S3/R2 behind the same interface (keys are provider-agnostic)."""

from pathlib import Path
from typing import Protocol

from app.core.config import get_settings


class StorageProvider(Protocol):
    def save(self, key: str, data: bytes) -> None: ...
    def public_url(self, key: str) -> str: ...


class LocalDiskStorage:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, key: str, data: bytes) -> None:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError("Invalid storage key")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def public_url(self, key: str) -> str:
        return f"/uploads/{key}"


def get_storage() -> StorageProvider:
    settings = get_settings()
    if settings.environment == "prod":
        raise RuntimeError("Wire S3-compatible storage before running uploads in prod")
    return LocalDiskStorage(Path(settings.upload_dir))
