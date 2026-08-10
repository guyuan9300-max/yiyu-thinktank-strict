from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken


class SecretStoreError(RuntimeError):
    pass


class SecretStore(Protocol):
    def get(self, reference: str) -> str | None: ...

    def set(self, reference: str, value: str) -> None: ...

    def delete(self, reference: str) -> None: ...


@dataclass
class MemorySecretStore:
    values: dict[str, str] = field(default_factory=dict)

    def get(self, reference: str) -> str | None:
        return self.values.get(reference)

    def set(self, reference: str, value: str) -> None:
        self.values[reference] = value

    def delete(self, reference: str) -> None:
        self.values.pop(reference, None)


class EncryptedFileSecretStore:
    def __init__(self, data_dir: Path, namespace: str):
        self.namespace = namespace
        self._lock = threading.RLock()
        namespace_hash = hashlib.sha256(namespace.encode("utf-8")).hexdigest()[:16]
        self._root = data_dir / ".runtime-secrets"
        self._key_path = self._root / f"{namespace_hash}.key"
        self._vault_path = self._root / f"{namespace_hash}.vault"

    def _ensure_root(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        os.chmod(self._root, 0o700)

    def _read_key(self) -> bytes:
        self._ensure_root()
        try:
            key = self._key_path.read_bytes().strip()
            Fernet(key)
            os.chmod(self._key_path, 0o600)
            return key
        except FileNotFoundError:
            key = Fernet.generate_key()
            try:
                descriptor = os.open(
                    self._key_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                return self._read_key()
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(key)
                handle.flush()
                os.fsync(handle.fileno())
            return key
        except (OSError, ValueError) as exc:
            raise SecretStoreError("本机密钥文件不可用") from exc

    def _load(self) -> dict[str, str]:
        try:
            encrypted = self._vault_path.read_bytes()
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise SecretStoreError("本机密钥仓读取失败") from exc

        try:
            decoded = Fernet(self._read_key()).decrypt(encrypted)
            payload = json.loads(decoded.decode("utf-8"))
        except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise SecretStoreError("本机密钥仓校验失败") from exc
        if not isinstance(payload, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in payload.items()
        ):
            raise SecretStoreError("本机密钥仓内容无效")
        return payload

    def _write(self, values: dict[str, str]) -> None:
        self._ensure_root()
        payload = json.dumps(
            values,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        encrypted = Fernet(self._read_key()).encrypt(payload)
        temporary = self._vault_path.with_name(
            f".{self._vault_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )

        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encrypted)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._vault_path)
            os.chmod(self._vault_path, 0o600)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise SecretStoreError("本机密钥仓写入失败") from exc

    def get(self, reference: str) -> str | None:
        with self._lock:
            return self._load().get(reference)

    def set(self, reference: str, value: str) -> None:
        with self._lock:
            values = self._load()
            values[reference] = value
            self._write(values)
            if self._load().get(reference) != value:
                raise SecretStoreError("本机密钥仓写入后校验失败")

    def delete(self, reference: str) -> None:
        with self._lock:
            values = self._load()
            if reference not in values:
                return
            values.pop(reference)
            self._write(values)


def secret_fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def build_secret_store(
    *,
    data_dir: Path,
    namespace: str,
    test_mode: bool,
) -> SecretStore:
    if test_mode:
        return MemorySecretStore()
    return EncryptedFileSecretStore(data_dir, namespace)
