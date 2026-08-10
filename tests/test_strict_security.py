from strict_common.security import payload_fingerprint, redact_payload


def test_redact_payload_recurses_through_secret_bundles() -> None:
    payload = {
        "provider": "feishu",
        "credentials": {
            "appId": "cli_public",
            "appSecret": "secret-value",
        },
        "members": [
            {
                "access_token": "member-token",
                "tokenPrefix": "public-prefix",
                "secretFingerprint": "public-fingerprint",
            }
        ],
    }

    redacted = redact_payload(payload)

    assert redacted["credentials"] == "[REDACTED]"
    assert redacted["members"][0]["access_token"] == "[REDACTED]"
    assert redacted["members"][0]["tokenPrefix"] == "public-prefix"
    assert redacted["members"][0]["secretFingerprint"] == "public-fingerprint"


def test_payload_fingerprint_distinguishes_secret_changes_without_plaintext() -> None:
    first = payload_fingerprint(
        {"provider": "feishu", "credentials": {"appSecret": "first-secret"}}
    )
    second = payload_fingerprint(
        {"provider": "feishu", "credentials": {"appSecret": "second-secret"}}
    )

    assert first != second
    assert "first-secret" not in first
    assert "second-secret" not in second
