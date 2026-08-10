from __future__ import annotations

import os

import pytest

from backend.app.secret_store import EncryptedFileSecretStore, SecretStoreError


def test_encrypted_file_secret_store_persists_without_plaintext(tmp_path):
    store = EncryptedFileSecretStore(tmp_path, "strict-test")
    store.set("sandbox/session", "private-token-value")

    restored = EncryptedFileSecretStore(tmp_path, "strict-test")
    assert restored.get("sandbox/session") == "private-token-value"

    secret_files = list((tmp_path / ".runtime-secrets").iterdir())
    assert len(secret_files) == 2
    for path in secret_files:
        assert oct(path.stat().st_mode & 0o777) == "0o600"
        content = path.read_bytes()
        assert b"private-token-value" not in content
        assert b"sandbox/session" not in content

    restored.delete("sandbox/session")
    assert restored.get("sandbox/session") is None


def test_encrypted_file_secret_store_rejects_tampering(tmp_path):
    store = EncryptedFileSecretStore(tmp_path, "strict-test")
    store.set("sandbox/session", "private-token-value")

    vault = next((tmp_path / ".runtime-secrets").glob("*.vault"))
    vault.write_bytes(b"tampered")
    os.chmod(vault, 0o600)

    with pytest.raises(SecretStoreError, match="校验失败"):
        store.get("sandbox/session")
