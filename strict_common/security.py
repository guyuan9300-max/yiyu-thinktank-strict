from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from typing import Any

from .ids import canonical_json, fingerprint_secret


PASSWORD_SCHEME = "scrypt-v1"
LEGACY_PASSWORD_SCHEME = "passlib-pbkdf2-sha256-v1"
_PHONE_PATTERN = re.compile(r"^\+?[0-9]{6,20}$")
_CN_MOBILE_PATTERN = re.compile(r"^1[3-9][0-9]{9}$")


def normalize_email(value: str) -> str:
    normalized = value.strip().lower()
    if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
        raise ValueError("invalid email address")
    return normalized


def normalize_phone(value: str) -> str:
    normalized = re.sub(r"[\s()-]", "", value.strip())
    if not _PHONE_PATTERN.fullmatch(normalized):
        raise ValueError("invalid phone number")
    # Mainland mobile contacts are stored in one E.164 form.  Login accepts
    # the common local and country-code spellings, but still performs an exact
    # lookup against the authoritative contact row in ``principals``.
    local_number = normalized
    if normalized.startswith("+86"):
        local_number = normalized[3:]
    elif normalized.startswith("0086"):
        local_number = normalized[4:]
    elif normalized.startswith("86") and len(normalized) == 13:
        local_number = normalized[2:]
    if _CN_MOBILE_PATTERN.fullmatch(local_number):
        return f"+86{local_number}"
    return normalized


def normalize_identifier(value: str) -> tuple[str, str]:
    normalized = value.strip()
    if "@" in normalized:
        return "email", normalize_email(normalized)
    return "phone", normalize_phone(normalized)


def validate_password(value: str) -> None:
    if len(value) < 8:
        raise ValueError("password must contain at least 8 characters")
    if len(value) > 256:
        raise ValueError("password is too long")


def hash_password(value: str) -> str:
    validate_password(value)
    salt = secrets.token_bytes(16)
    n, r, p = 2**14, 8, 1
    digest = hashlib.scrypt(
        value.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=32,
    )
    return "$".join(
        (
            PASSWORD_SCHEME,
            str(n),
            str(r),
            str(p),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )
    )


def _verify_scrypt_password(value: str, encoded: str) -> bool:
    try:
        scheme, n_text, r_text, p_text, salt_text, digest_text = encoded.split("$")
        if scheme != PASSWORD_SCHEME:
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        actual = hashlib.scrypt(
            value.encode("utf-8"),
            salt=salt,
            n=int(n_text),
            r=int(r_text),
            p=int(p_text),
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


def verify_password(
    value: str,
    encoded: str,
    *,
    scheme: str = PASSWORD_SCHEME,
) -> bool:
    if scheme == PASSWORD_SCHEME:
        return _verify_scrypt_password(value, encoded)
    if scheme == LEGACY_PASSWORD_SCHEME:
        try:
            from passlib.hash import pbkdf2_sha256

            return bool(pbkdf2_sha256.verify(value, encoded))
        except (TypeError, ValueError):
            return False
    return False


def new_secret_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EncryptedSecret:
    ciphertext: str
    fingerprint: str


class SecretCipher:
    def __init__(self, key: str):
        from cryptography.fernet import Fernet

        normalized = key.strip().encode("ascii")
        self._fernet = Fernet(normalized)

    def encrypt(self, value: str) -> EncryptedSecret:
        ciphertext = self._fernet.encrypt(value.encode("utf-8")).decode("ascii")
        return EncryptedSecret(
            ciphertext=ciphertext,
            fingerprint=fingerprint_secret(value),
        )

    def decrypt(self, ciphertext: str) -> str:
        return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")


def _sensitive_key(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", value.casefold())
    if normalized.startswith("has") or normalized.endswith(
        ("fingerprint", "prefix", "reference", "ref")
    ):
        return False
    return normalized in {
        "password",
        "accesstoken",
        "refreshtoken",
        "token",
        "apikey",
        "bootstrapToken".casefold(),
        "authorization",
        "credential",
        "credentials",
        "appsecret",
        "clientsecret",
        "secret",
        "secretbundle",
    } or normalized.endswith(("password", "token", "apikey", "secret", "credentials"))


def _redact_nested(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if _sensitive_key(str(key))
                else _redact_nested(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_nested(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_nested(item) for item in value]
    return value


def _fingerprint_nested(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                {
                    "$secretFingerprint": fingerprint_secret(
                        canonical_json(item)
                        if isinstance(item, (dict, list, tuple))
                        else str(item)
                    )
                }
                if _sensitive_key(str(key))
                else _fingerprint_nested(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_fingerprint_nested(item) for item in value]
    if isinstance(value, tuple):
        return [_fingerprint_nested(item) for item in value]
    return value


def redact_payload(value: dict[str, Any]) -> dict[str, Any]:
    redacted = _redact_nested(value)
    if not isinstance(redacted, dict):
        raise TypeError("payload must be an object")
    return redacted


def payload_fingerprint(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        canonical_json(_fingerprint_nested(value)).encode("utf-8")
    ).hexdigest()


def encode_secret_bundle(value: dict[str, Any]) -> str:
    return canonical_json(value)


def decode_secret_bundle(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("secret bundle must be an object")
    return parsed
