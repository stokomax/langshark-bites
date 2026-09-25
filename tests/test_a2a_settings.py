"""Tests for langshark_bites.a2a_completion_notifier.settings.

Exercises the full ``A2A_*`` env surface of both dataclasses: defaults,
mapping, numeric parsing, and the emitter's private-key file loading.
"""

from __future__ import annotations

import pytest

from langshark_bites.a2a_completion_notifier.settings import (
    A2AVerifyMode,
    EmitterSettings,
    ReceiverSettings,
)

_RECEIVER_ENV_NAMES = (
    "SUPERVISOR_URL",
    "SUPERVISOR_API_KEY",
    "CALLBACK_TOKEN_SECRET",
    "RECEIVER_URL",
    "SUBAGENT_ISSUER",
    "SUBAGENT_JWKS_URL",
    "SUBAGENT_URL",
    "SUBAGENT_API_KEY",
    "RECEIVER_PORT",
    "JTI_TTL_SECONDS",
    "IAT_STALENESS_SECONDS",
    "CALLBACK_TOKEN_TTL",
)

_EMITTER_ENV_NAMES = (
    "PRIVATE_KEY_PEM",
    "PRIVATE_KEY_FILE",
    "KID",
    "EMITTER_ISSUER",
    "EMITTER_AUDIENCE",
    "JTI_TTL_SECONDS",
    "TIMEOUT_SECONDS",
    "MAX_RETRIES",
    "RETRY_BACKOFF_SECONDS",
)


class TestReceiverSettingsFromEnv:
    def test_defaults_without_environment(self, monkeypatch) -> None:
        for name in _RECEIVER_ENV_NAMES:
            monkeypatch.delenv(f"A2A_{name}", raising=False)
        settings = ReceiverSettings.from_env()
        assert settings.supervisor_url == "http://localhost:8000"
        assert settings.receiver_url == "http://localhost:8001"
        assert settings.receiver_port == 8001
        assert settings.jti_ttl_seconds == 900
        assert settings.iat_staleness_seconds == 300
        assert settings.callback_token_ttl_seconds == 86400
        assert settings.callback_token_secret == ""

    def test_maps_all_env_vars(self, monkeypatch) -> None:
        monkeypatch.setenv("A2A_SUPERVISOR_URL", "http://sup:1234")
        monkeypatch.setenv("A2A_SUPERVISOR_API_KEY", "sup-key")
        monkeypatch.setenv("A2A_CALLBACK_TOKEN_SECRET", "ct-secret")
        monkeypatch.setenv("A2A_RECEIVER_URL", "http://recv:9999")
        monkeypatch.setenv("A2A_SUBAGENT_ISSUER", "https://sub.example")
        monkeypatch.setenv("A2A_SUBAGENT_JWKS_URL", "https://sub.example/jwks")
        monkeypatch.setenv("A2A_SUBAGENT_URL", "https://sub.example")
        monkeypatch.setenv("A2A_SUBAGENT_API_KEY", "sub-key")
        monkeypatch.setenv("A2A_RECEIVER_PORT", "8123")
        monkeypatch.setenv("A2A_JTI_TTL_SECONDS", "60")
        monkeypatch.setenv("A2A_IAT_STALENESS_SECONDS", "45")
        monkeypatch.setenv("A2A_CALLBACK_TOKEN_TTL", "120")
        settings = ReceiverSettings.from_env()
        assert settings.supervisor_url == "http://sup:1234"
        assert settings.supervisor_api_key == "sup-key"
        assert settings.callback_token_secret == "ct-secret"
        assert settings.receiver_url == "http://recv:9999"
        assert settings.subagent_issuer == "https://sub.example"
        assert settings.subagent_jwks_url == "https://sub.example/jwks"
        assert settings.subagent_url == "https://sub.example"
        assert settings.subagent_api_key == "sub-key"
        assert settings.receiver_port == 8123
        assert settings.jti_ttl_seconds == 60
        assert settings.iat_staleness_seconds == 45
        assert settings.callback_token_ttl_seconds == 120

    def test_invalid_int_raises(self, monkeypatch) -> None:
        monkeypatch.setenv("A2A_RECEIVER_PORT", "not-a-port")
        with pytest.raises(ValueError):
            ReceiverSettings.from_env()


class TestEmitterSettingsFromEnv:
    def test_defaults_without_environment(self, monkeypatch) -> None:
        for name in _EMITTER_ENV_NAMES:
            monkeypatch.delenv(f"A2A_{name}", raising=False)
        settings = EmitterSettings.from_env()
        assert settings.private_key_pem == ""
        assert settings.kid == "subagent"
        assert settings.jti_ttl_seconds == 300
        assert settings.timeout_seconds == 30.0
        assert settings.max_retries == 3
        assert settings.retry_backoff_seconds == 1.0

    def test_inline_pem_and_scalars(self, monkeypatch) -> None:
        monkeypatch.setenv("A2A_PRIVATE_KEY_PEM", "-----BEGIN PRIVATE KEY-----\nAAAA\n")
        monkeypatch.setenv("A2A_KID", "worker-42")
        monkeypatch.setenv("A2A_EMITTER_ISSUER", "https://sub.example")
        monkeypatch.setenv("A2A_EMITTER_AUDIENCE", "https://recv.example")
        monkeypatch.setenv("A2A_TIMEOUT_SECONDS", "12.5")
        monkeypatch.setenv("A2A_MAX_RETRIES", "0")
        monkeypatch.setenv("A2A_RETRY_BACKOFF_SECONDS", "0.25")
        settings = EmitterSettings.from_env()
        assert settings.private_key_pem == "-----BEGIN PRIVATE KEY-----\nAAAA\n"
        assert settings.kid == "worker-42"
        assert settings.issuer == "https://sub.example"
        assert settings.audience == "https://recv.example"
        assert settings.timeout_seconds == 12.5
        assert settings.max_retries == 0
        assert settings.retry_backoff_seconds == 0.25

    def test_key_file_wins_over_inline_pem(self, monkeypatch, tmp_path) -> None:
        key_file = tmp_path / "signing.pem"
        key_file.write_text("-----BEGIN PRIVATE KEY-----\nFROM-FILE\n", encoding="utf-8")
        monkeypatch.setenv("A2A_PRIVATE_KEY_PEM", "inline-pem")
        monkeypatch.setenv("A2A_PRIVATE_KEY_FILE", str(key_file))
        settings = EmitterSettings.from_env()
        assert settings.private_key_pem == "-----BEGIN PRIVATE KEY-----\nFROM-FILE\n"

    def test_invalid_float_raises(self, monkeypatch) -> None:
        monkeypatch.setenv("A2A_TIMEOUT_SECONDS", "soon")
        with pytest.raises(ValueError):
            EmitterSettings.from_env()


class TestA2AVerifyMode:
    def test_default_mode_is_dev(self, monkeypatch) -> None:
        for name in (*_RECEIVER_ENV_NAMES, "VERIFY_MODE"):
            monkeypatch.delenv(f"A2A_{name}", raising=False)
        assert ReceiverSettings.from_env().mode is A2AVerifyMode.DEV

    def test_mode_parses_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv("A2A_VERIFY_MODE", "verify")
        assert ReceiverSettings.from_env().mode is A2AVerifyMode.VERIFY

    def test_invalid_mode_raises(self, monkeypatch) -> None:
        monkeypatch.setenv("A2A_VERIFY_MODE", "paranoid")
        with pytest.raises(ValueError):
            ReceiverSettings.from_env()

    def test_strict_without_sender_auth_config_raises(self, monkeypatch) -> None:
        for name in ("VERIFY_MODE", "SUBAGENT_JWKS_URL", "SUBAGENT_ISSUER"):
            monkeypatch.delenv(f"A2A_{name}", raising=False)
        monkeypatch.setenv("A2A_VERIFY_MODE", "strict")
        with pytest.raises(ValueError, match="strict requires"):
            ReceiverSettings.from_env()

    def test_strict_with_sender_auth_config_ok(self, monkeypatch) -> None:
        monkeypatch.setenv("A2A_VERIFY_MODE", "strict")
        monkeypatch.setenv("A2A_SUBAGENT_JWKS_URL", "https://sub.example/jwks")
        monkeypatch.setenv("A2A_SUBAGENT_ISSUER", "https://sub.example")
        settings = ReceiverSettings.from_env()
        assert settings.mode is A2AVerifyMode.STRICT

    def test_verify_partial_config_raises(self, monkeypatch) -> None:
        monkeypatch.setenv("A2A_VERIFY_MODE", "verify")
        monkeypatch.setenv("A2A_SUBAGENT_JWKS_URL", "https://sub.example/jwks")
        monkeypatch.delenv("A2A_SUBAGENT_ISSUER", raising=False)
        with pytest.raises(ValueError, match="verify requires both"):
            ReceiverSettings.from_env()

    def test_verify_without_config_ok(self, monkeypatch) -> None:
        monkeypatch.setenv("A2A_VERIFY_MODE", "verify")
        for name in ("SUBAGENT_JWKS_URL", "SUBAGENT_ISSUER"):
            monkeypatch.delenv(f"A2A_{name}", raising=False)
        assert ReceiverSettings.from_env().mode is A2AVerifyMode.VERIFY

    def test_dev_with_config_ok(self, monkeypatch) -> None:
        monkeypatch.setenv("A2A_SUBAGENT_JWKS_URL", "https://sub.example/jwks")
        monkeypatch.setenv("A2A_SUBAGENT_ISSUER", "https://sub.example")
        assert ReceiverSettings.from_env().mode is A2AVerifyMode.DEV
