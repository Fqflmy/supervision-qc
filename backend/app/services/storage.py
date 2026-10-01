# -*- coding: utf-8 -*-
"""文件存储（SRS FR-KB-01：原始文件落盘或 MinIO）。"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import BinaryIO, Optional

from app.config import settings
from app.core.logging_conf import get_logger

logger = get_logger(__name__)


def _classify(source: BinaryIO) -> "object":
    raise NotImplementedError


class LocalStorage:
    """本地文件存储（默认）。目录结构：{root}/{yyyy}/{mm}/{sha1}{ext}。"""

    backend = "local"

    def __init__(self, root: Optional[str] = None) -> None:
        self.root = Path(root or settings.storage_local_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, file_obj: BinaryIO, filename: str, *, subdir: Optional[str] = None) -> tuple[str, str, int]:
        """保存文件，返回 (相对路径, sha1, 字节数)。"""
        import datetime as dt

        suffix = Path(filename).suffix.lower()
        sha1 = hashlib.sha1()
        temp = self.root / f".upload-{hashlib.md5(filename.encode('utf-8')).hexdigest()}{suffix}"
        size = 0
        with temp.open("wb") as fh:
            while True:
                chunk = file_obj.read(1024 * 1024)
                if not chunk:
                    break
                sha1.update(chunk)
                size += len(chunk)
                fh.write(chunk)
        digest = sha1.hexdigest()
        now = dt.datetime.now()
        relative_dir = Path(subdir) if subdir else Path(f"{now:%Y}") / f"{now:%m}"
        target_dir = self.root / relative_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{digest}{suffix}"
        if target.exists():
            temp.unlink(missing_ok=True)
        else:
            shutil.move(str(temp), str(target))
        return str(Path(relative_dir) / target.name), digest, size

    def path_of(self, relative_path: str) -> Path:
        return self.root / relative_path

    def exists(self, relative_path: str) -> bool:
        return self.path_of(relative_path).exists()

    def delete(self, relative_path: str) -> bool:
        target = self.path_of(relative_path)
        if target.exists():
            target.unlink()
            return True
        return False


class MinioStorage(LocalStorage):
    """MinIO 对象存储（Compose 中的 minio 服务）；本地留缓存用于解析。"""

    backend = "minio"

    def __init__(self) -> None:
        super().__init__()
        self._client = None

    def _get_client(self):
        if self._client is None:
            from minio import Minio  # type: ignore

            self._client = Minio(
                settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=settings.minio_secure,
            )
            if not self._client.bucket_exists(settings.minio_bucket):
                self._client.make_bucket(settings.minio_bucket)
        return self._client

    def save(self, file_obj: BinaryIO, filename: str, *, subdir: Optional[str] = None):
        relative, digest, size = super().save(file_obj, filename, subdir=subdir)
        try:
            client = self._get_client()
            client.fput_object(
                settings.minio_bucket, relative.replace("\\", "/"), str(self.path_of(relative))
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("MinIO 上传失败，保留本地副本", extra={"error": str(exc)[:200]})
        return relative, digest, size


_storage = None


def get_storage() -> LocalStorage:
    global _storage
    if _storage is None:
        if settings.storage_backend == "minio":
            try:
                _storage = MinioStorage()
            except Exception as exc:  # noqa: BLE001
                logger.warning("MinIO 初始化失败，回退本地存储", extra={"error": str(exc)[:200]})
                _storage = LocalStorage()
        else:
            _storage = LocalStorage()
    return _storage


__all__ = ["LocalStorage", "MinioStorage", "get_storage"]
