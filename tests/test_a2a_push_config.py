"""Tests for langshark_bites.a2a_completion_notifier.push_config."""

from __future__ import annotations

import time

from langshark_bites.a2a_completion_notifier.push_config import build_push_config


class TestBuildPushConfig:
    def test_builds_a2a_push_config(self):
        config = build_push_config(
            thread_id="thread_1",
            assistant_id="asst_1",
            dispatch_id="dispatch_1",
            receiver_url="https://receiver.example",
            callback_token_secret="secret",
            now=int(time.time()),
        )
        cfg = config["configurable"]["a2a_push_config"]
        assert cfg["url"] == "https://receiver.example/a2a/notifications"
        assert cfg["authentication"] == {"schemes": ["Bearer"]}
        assert cfg["token"]  # opaque JWT present
        assert cfg["context_id"] == "thread_1"

    def test_roundtrips_through_token_unseal(self):
        now = int(time.time())
        config = build_push_config(
            thread_id="thread_1",
            assistant_id="asst_1",
            dispatch_id="dispatch_1",
            receiver_url="https://receiver.example/",
            callback_token_secret="secret",
            now=now,
        )
        from langshark_bites.a2a_completion_notifier.tokens import unseal_callback_token

        token = config["configurable"]["a2a_push_config"]["token"]
        parent = unseal_callback_token(token, "secret")
        assert parent.thread_id == "thread_1"
        assert parent.assistant_id == "asst_1"
        assert parent.dispatch_id == "dispatch_1"

    def test_strips_trailing_slash(self):
        config = build_push_config(
            thread_id="t",
            assistant_id="a",
            dispatch_id="d",
            receiver_url="https://receiver.example/",
            callback_token_secret="secret",
            now=int(time.time()),
        )
        assert (
            config["configurable"]["a2a_push_config"]["url"]
            == "https://receiver.example/a2a/notifications"
        )


class TestModeStamp:
    def test_stamps_dev_mode_by_default(self) -> None:
        config = build_push_config(
            thread_id="t",
            assistant_id="a",
            dispatch_id="d",
            receiver_url="https://receiver.example",
            callback_token_secret="secret",
        )
        push = config["configurable"]["a2a_push_config"]
        assert push["mode"] == "dev"

    def test_stamps_configured_mode(self) -> None:
        from langshark_bites.a2a_completion_notifier.settings import A2AVerifyMode

        config = build_push_config(
            thread_id="t",
            assistant_id="a",
            dispatch_id="d",
            receiver_url="https://receiver.example",
            callback_token_secret="secret",
            mode=A2AVerifyMode.STRICT,
        )
        push = config["configurable"]["a2a_push_config"]
        assert push["mode"] == "strict"
