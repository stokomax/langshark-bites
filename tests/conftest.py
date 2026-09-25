"""Shared fixtures for a2a_completion_notifier tests.

Provides:
- ``rsa_pem`` -- a fresh RSA private key (PEM, PKCS#8) used by the signer
  and auth tests, so every test exercises real cryptography.
- ``signer`` -- an :class:`A2ASigner` built from ``rsa_pem``.
- ``callback_token`` -- a supervisor-minted opaque callback token.
"""

from __future__ import annotations

import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from langshark_bites.a2a_completion_notifier.signer import A2ASigner
from langshark_bites.a2a_completion_notifier.tokens import mint_callback_token

RECEIVER_URL = "https://receiver.example"
ISSUER = "https://subagents.example"
KID = "subagent-key-1"
CALLBACK_SECRET = "super-secret-callback-token-secret"

TEST_THREAD_ID = "thread_1"
TEST_ASSISTANT_ID = "supervisor_asst"
TEST_DISPATCH_ID = "dispatch_1"


@pytest.fixture
def rsa_pem() -> str:
    """A fresh RSA-2048 private key in PEM (PKCS#8) form."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


@pytest.fixture
def signer(rsa_pem: str) -> A2ASigner:
    """An A2ASigner valid for the shared test receiver/issuer pair."""
    return A2ASigner(
        private_key_pem=rsa_pem,
        kid=KID,
        issuer=ISSUER,
        audience=RECEIVER_URL,
    )


@pytest.fixture
def callback_token() -> str:
    """An unexpired callback token routed to TEST_THREAD_ID."""
    return mint_callback_token(
        thread_id=TEST_THREAD_ID,
        assistant_id=TEST_ASSISTANT_ID,
        dispatch_id=TEST_DISPATCH_ID,
        secret=CALLBACK_SECRET,
        now=int(time.time()),
    )
