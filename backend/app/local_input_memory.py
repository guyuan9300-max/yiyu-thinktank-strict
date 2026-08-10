"""Device credential projection for personal input-memory preferences.

Public preferences are authoritative in the member's cloud personal
``scoped_configuration_records`` row.  This store keeps only a device cache of
that public document and the sensitive values in the configured local secret
store.  No password, API key, or app secret is written to SQLite.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Mapping

from strict_common.ids import canonical_json, sha256_text, utc_now

from .secret_store import SecretStore


_DEFAULT_PUBLIC: dict[str, Any] = {
    "cloudAuth": {
        "rememberInputs": True,
        "lastEmail": None,
        "accounts": [],
    },
    "aiSettings": {"rememberApiKey": False},
    "feishuIntegration": {
        "rememberInputs": False,
        "appId": "",
        "callbackMode": "cloud_relay",
        "customCallbackUrl": "",
    },
}


class PersonalSecretBoundaryRequired(ValueError):
    """Raised when a personal secret is accessed without a verified identity."""


class LocalInputMemoryStore:
    DEVICE_PUBLIC_CACHE_REF = "local-input-memory:public:device:v2"
    CLOUD_PASSWORDS_REF = "local-input-memory:cloud-passwords:v1"

    def __init__(
        self,
        secret_store: SecretStore,
        *,
        cloud_instance_id: str = "",
        organization_id: str = "",
        principal_id: str = "",
        membership_id: str = "",
    ):
        self.secret_store = secret_store
        identity = {
            "cloudInstanceId": str(cloud_instance_id).strip(),
            "organizationId": str(organization_id).strip(),
            "principalId": str(principal_id).strip(),
            "membershipId": str(membership_id).strip(),
        }
        self._personal_boundary = (
            sha256_text(canonical_json(identity))[:32]
            if all(identity.values())
            else ""
        )

    @property
    def has_personal_boundary(self) -> bool:
        return bool(self._personal_boundary)

    @property
    def _public_cache_ref(self) -> str:
        if self._personal_boundary:
            return f"local-input-memory:public:personal:v2:{self._personal_boundary}"
        return self.DEVICE_PUBLIC_CACHE_REF

    def _personal_secret_ref(self, kind: str) -> str:
        if not self._personal_boundary:
            raise PersonalSecretBoundaryRequired(
                "个人凭据要求已验证的 cloud_instance + organization + principal + membership"
            )
        return f"local-input-memory:{kind}:personal:v2:{self._personal_boundary}"

    @staticmethod
    def _public(value: Mapping[str, Any] | None) -> dict[str, Any]:
        value = dict(value or {})
        cloud = value.get("cloudAuth")
        cloud = dict(cloud) if isinstance(cloud, Mapping) else {}
        accounts = []
        for raw in cloud.get("accounts") or []:
            if not isinstance(raw, Mapping):
                continue
            email = str(raw.get("email") or "").strip()
            identifier = str(raw.get("identifier") or "").strip()
            if not email and not identifier:
                continue
            accounts.append(
                {
                    "email": email or identifier,
                    "identifier": identifier or email,
                    "fullName": str(raw.get("fullName") or "").strip(),
                    "updatedAt": str(raw.get("updatedAt") or utc_now()),
                }
            )
        ai = value.get("aiSettings")
        ai = dict(ai) if isinstance(ai, Mapping) else {}
        feishu = value.get("feishuIntegration")
        feishu = dict(feishu) if isinstance(feishu, Mapping) else {}
        return {
            "cloudAuth": {
                "rememberInputs": bool(
                    cloud.get(
                        "rememberInputs",
                        _DEFAULT_PUBLIC["cloudAuth"]["rememberInputs"],
                    )
                ),
                "lastEmail": (
                    str(cloud.get("lastEmail") or "").strip() or None
                ),
                "accounts": accounts,
            },
            "aiSettings": {
                "rememberApiKey": bool(
                    ai.get("rememberApiKey", ai.get("rememberCredential"))
                ),
            },
            "feishuIntegration": {
                "rememberInputs": bool(feishu.get("rememberInputs")),
                "appId": str(feishu.get("appId") or "").strip(),
                "callbackMode": (
                    str(feishu.get("callbackMode") or "").strip()
                    or "cloud_relay"
                ),
                "customCallbackUrl": str(
                    feishu.get("customCallbackUrl") or ""
                ).strip(),
            },
        }

    def _cached_public_from(self, reference: str) -> dict[str, Any]:
        encoded = self.secret_store.get(reference)
        if not encoded:
            return deepcopy(_DEFAULT_PUBLIC)
        try:
            value = json.loads(encoded)
        except json.JSONDecodeError:
            return deepcopy(_DEFAULT_PUBLIC)
        return self._public(value if isinstance(value, Mapping) else None)

    def cached_public(self) -> dict[str, Any]:
        public = self._cached_public_from(self._public_cache_ref)
        if self._public_cache_ref == self.DEVICE_PUBLIC_CACHE_REF:
            password_keys = list(self._passwords())
            if password_keys and not public["cloudAuth"]["accounts"]:
                accounts = [
                    {
                        "email": identifier,
                        "identifier": identifier,
                        "fullName": "",
                        "updatedAt": utc_now(),
                    }
                    for identifier in password_keys[:20]
                ]
                public = {
                    **public,
                    "cloudAuth": {
                        "rememberInputs": True,
                        "lastEmail": accounts[0]["email"],
                        "accounts": accounts,
                    },
                }
                self.cache_device_cloud_auth(public)
        return public

    def cache_public(self, value: Mapping[str, Any]) -> dict[str, Any]:
        public = self._public(value)
        self.secret_store.set(self._public_cache_ref, canonical_json(public))
        return public

    def cache_device_cloud_auth(
        self,
        value: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Keep only the remembered login form available before authentication."""

        public = self._public(value)
        device = self._cached_public_from(self.DEVICE_PUBLIC_CACHE_REF)
        device = {**device, "cloudAuth": public["cloudAuth"]}
        self.secret_store.set(
            self.DEVICE_PUBLIC_CACHE_REF,
            canonical_json(device),
        )
        return device

    def _passwords(self) -> dict[str, str]:
        encoded = self.secret_store.get(self.CLOUD_PASSWORDS_REF)
        if not encoded:
            return {}
        try:
            value = json.loads(encoded)
        except json.JSONDecodeError:
            return {}
        if not isinstance(value, Mapping):
            return {}
        return {
            str(key): str(secret)
            for key, secret in value.items()
            if str(key) and str(secret)
        }

    def _write_passwords(self, value: Mapping[str, str]) -> None:
        normalized = {
            str(key): str(secret)
            for key, secret in value.items()
            if str(key) and str(secret)
        }
        if normalized:
            self.secret_store.set(
                self.CLOUD_PASSWORDS_REF,
                canonical_json(normalized),
            )
        else:
            self.secret_store.delete(self.CLOUD_PASSWORDS_REF)

    @staticmethod
    def _account_key(value: Mapping[str, Any]) -> str:
        return str(
            value.get("identifier") or value.get("email") or ""
        ).strip().casefold()

    def read(
        self,
        public: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = (
            self.cache_public(public)
            if public is not None
            else self.cached_public()
        )
        if self.has_personal_boundary:
            self.cache_device_cloud_auth(normalized)
        passwords = self._passwords()
        cloud = normalized["cloudAuth"]
        accounts = [
            {
                **account,
                "password": (
                    passwords.get(self._account_key(account), "")
                    if cloud["rememberInputs"]
                    else ""
                ),
            }
            for account in cloud["accounts"]
        ]
        return {
            "cloudAuth": {**cloud, "accounts": accounts},
            "aiSettings": {
                **normalized["aiSettings"],
                "apiKey": (
                    self.secret_store.get(
                        self._personal_secret_ref("ai-api-key")
                    )
                    or ""
                    if normalized["aiSettings"]["rememberApiKey"]
                    and self.has_personal_boundary
                    else ""
                ),
            },
            "feishuIntegration": {
                **normalized["feishuIntegration"],
                "appSecret": (
                    self.secret_store.get(
                        self._personal_secret_ref("feishu-app-secret")
                    )
                    or ""
                    if normalized["feishuIntegration"]["rememberInputs"]
                    and self.has_personal_boundary
                    else ""
                ),
            },
            "credentialBoundary": {
                "cloudAuthPasswords": "device",
                "publicPreferenceCache": (
                    "personal_workspace"
                    if self.has_personal_boundary
                    else "device_pre_authentication"
                ),
                "aiApiKey": (
                    "personal_workspace"
                    if self.has_personal_boundary
                    else "unavailable_without_verified_identity"
                ),
                "feishuAppSecret": (
                    "personal_workspace"
                    if self.has_personal_boundary
                    else "unavailable_without_verified_identity"
                ),
            },
        }

    def cloud_auth_public(
        self,
        current: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        public = self._public(current)
        remember = bool(payload.get("rememberInputs"))
        cloud = public["cloudAuth"]
        if not remember:
            cloud = {
                "rememberInputs": False,
                "lastEmail": None,
                "accounts": [],
            }
        else:
            email = str(payload.get("email") or "").strip()
            identifier = str(
                payload.get("identifier") or email
            ).strip()
            account_key = (identifier or email).casefold()
            existing_account = next(
                (
                    item
                    for item in cloud["accounts"]
                    if self._account_key(item) == account_key
                ),
                None,
            )
            account = {
                "email": email or identifier,
                "identifier": identifier or email,
                "fullName": str(payload.get("fullName") or "").strip(),
                "updatedAt": (
                    str(existing_account.get("updatedAt") or "")
                    if existing_account is not None
                    else utc_now()
                ),
            }
            key = self._account_key(account)
            accounts = [
                item
                for item in cloud["accounts"]
                if self._account_key(item) != key
            ]
            if key:
                accounts.insert(0, account)
            cloud = {
                "rememberInputs": True,
                "lastEmail": account["email"] or None,
                "accounts": accounts[:20],
            }
        return {**public, "cloudAuth": cloud}

    def apply_cloud_auth_secret(self, payload: Mapping[str, Any]) -> None:
        if not bool(payload.get("rememberInputs")):
            self._write_passwords({})
            return
        password = str(payload.get("password") or "")
        key = str(
            payload.get("identifier") or payload.get("email") or ""
        ).strip().casefold()
        if password and key:
            passwords = self._passwords()
            passwords[key] = password
            self._write_passwords(passwords)

    def ai_public(
        self,
        current: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        public = self._public(current)
        return {
            **public,
            "aiSettings": {
                "rememberApiKey": bool(payload.get("rememberApiKey")),
            },
        }

    def apply_ai_secret(self, payload: Mapping[str, Any]) -> None:
        secret_ref = self._personal_secret_ref("ai-api-key")
        if not bool(payload.get("rememberApiKey")):
            self.secret_store.delete(secret_ref)
            return
        api_key = str(payload.get("apiKey") or "")
        if api_key:
            self.secret_store.set(secret_ref, api_key)

    def feishu_public(
        self,
        current: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        public = self._public(current)
        return {
            **public,
            "feishuIntegration": {
                "rememberInputs": bool(payload.get("rememberInputs")),
                "appId": str(payload.get("appId") or "").strip(),
                "callbackMode": (
                    str(payload.get("callbackMode") or "").strip()
                    or "cloud_relay"
                ),
                "customCallbackUrl": str(
                    payload.get("customCallbackUrl") or ""
                ).strip(),
            },
        }

    def apply_feishu_secret(self, payload: Mapping[str, Any]) -> None:
        secret_ref = self._personal_secret_ref("feishu-app-secret")
        if not bool(payload.get("rememberInputs")):
            self.secret_store.delete(secret_ref)
            return
        app_secret = str(payload.get("appSecret") or "")
        if app_secret:
            self.secret_store.set(secret_ref, app_secret)
