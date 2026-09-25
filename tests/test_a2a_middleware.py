"""Tests for langshark_bites.a2a_completion_notifier.middleware."""

from __future__ import annotations

import pytest

from langshark_bites.a2a_completion_notifier.middleware import (
    A2APushNotifierMiddleware,
    PushEmissionError,
    PushNotificationConfig,
    PushNotificationConfigError,
    build_a2a_notifier_from_config,
    extract_push_config,
)


class _FakePushClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.succeed = True

    async def send(self, url: str, *, bearer: str, payload: dict) -> bool:
        self.calls.append({"url": url, "bearer": bearer, "payload": payload})
        return self.succeed


class _FakeSigner:
    def __init__(self) -> None:
        self.signed: list[str] = []

    def sign(self, task_id: str, *, now: int | None = None) -> str:
        self.signed.append(task_id)
        return f"signed:{task_id}"


class _FakeRuntime:
    def __init__(self, run_id: str | None = "run-1", thread_id: str | None = "thr-1"):
        self.execution_info = type("ExecInfo", (), {"run_id": run_id, "thread_id": thread_id})()


def _make_middleware(*, task_id_resolver=None, push_config=None, push_client=None):
    client = push_client or _FakePushClient()
    signer = _FakeSigner()
    cfg = push_config or PushNotificationConfig(
        url="https://receiver.example/a2a/notifications", token="opaque-token"
    )
    mw = A2APushNotifierMiddleware(
        push_config=cfg,
        signer=signer,  # type: ignore[arg-type]
        push_client=client,  # type: ignore[arg-type]
        task_id_resolver=task_id_resolver,
    )
    return mw, client, signer


class TestPushNotificationConfig:
    def test_requires_url_and_token(self):
        with pytest.raises(PushNotificationConfigError):
            PushNotificationConfig(url="", token="t")
        with pytest.raises(PushNotificationConfigError):
            PushNotificationConfig(url="https://x", token=None)  # type: ignore[arg-type]

    def test_from_dict(self):
        cfg = PushNotificationConfig.from_dict(
            {
                "url": "https://receiver",
                "token": "t",
                "authentication": {"schemes": ["Bearer"]},
            }
        )
        assert cfg.url == "https://receiver"
        assert cfg.token == "t"
        assert cfg.authentication == {"schemes": ["Bearer"]}


class TestExtractPushConfig:
    def test_absent_config_returns_none(self):
        assert extract_push_config(None) is None
        assert extract_push_config({}) is None
        assert extract_push_config({"configurable": {}}) is None

    def test_present_config_parses(self):
        cfg = extract_push_config(
            {
                "configurable": {
                    "a2a_push_config": {
                        "url": "https://receiver/a2a/notifications",
                        "token": "opaque",
                    }
                }
            }
        )
        assert cfg is not None
        assert cfg.url.endswith("/a2a/notifications")
        assert cfg.token == "opaque"


class TestFactory:
    def test_builds_middleware_from_config(self, callback_token):
        mw = build_a2a_notifier_from_config(
            {
                "configurable": {
                    "a2a_push_config": {
                        "url": "https://receiver.example/a2a/notifications",
                        "token": callback_token,
                    }
                }
            },
            signer=_FakeSigner(),  # type: ignore[arg-type]
            push_client=_FakePushClient(),  # type: ignore[arg-type]
        )
        assert isinstance(mw, A2APushNotifierMiddleware)

    def test_raises_without_push_config(self):
        with pytest.raises(PushNotificationConfigError):
            build_a2a_notifier_from_config(
                {"configurable": {}},
                signer=_FakeSigner(),  # type: ignore[arg-type]
                push_client=_FakePushClient(),  # type: ignore[arg-type]
            )


class TestEmission:
    async def test_aafter_agent_emits_completed(self):
        mw, client, signer = _make_middleware()
        state = {"messages": [{"role": "ai", "content": "all done"}]}
        result = await mw.aafter_agent(state, _FakeRuntime())
        assert result is None
        assert len(client.calls) == 1
        call = client.calls[0]
        assert call["url"] == "https://receiver.example/a2a/notifications"
        # task id resolves to the thread id (fetchable by the supervisor's
        # full-result primitive), not the run id.
        assert call["bearer"] == "signed:thr-1"
        assert call["payload"]["id"] == "thr-1"
        assert call["payload"]["status"]["state"] == "TASK_STATE_COMPLETED"
        assert call["payload"]["metadata"]["token"] == "opaque-token"
        assert signer.signed == ["thr-1"]

    async def test_thread_id_preferred_over_run_id(self):
        mw, client, _ = _make_middleware()
        await mw.aafter_agent({}, _FakeRuntime(run_id="run-x", thread_id="thread-x"))
        assert client.calls[0]["payload"]["id"] == "thread-x"
        assert client.calls[0]["bearer"] == "signed:thread-x"

    async def test_awrap_model_call_emits_failed_and_reraises(self):
        mw, client, _ = _make_middleware()
        # before-agent resolves the task id used by the error path (which
        # does not receive a runtime).
        await mw.abefore_agent({}, _FakeRuntime())

        class Boom(Exception):
            pass

        async def handler(request):
            raise Boom("model exploded")

        with pytest.raises(Boom):
            await mw.awrap_model_call({"request": 1}, handler)

        assert len(client.calls) == 1
        assert client.calls[0]["payload"]["status"]["state"] == "TASK_STATE_FAILED"
        assert client.calls[0]["payload"]["metadata"].get("summary") == "model exploded"

    async def test_only_one_notification_per_run(self):
        mw, client, _ = _make_middleware()
        await mw.aafter_agent({}, _FakeRuntime())
        await mw.aafter_agent({}, _FakeRuntime())  # second call suppressed
        assert len(client.calls) == 1

    async def test_task_id_resolver_custom(self):
        mw, client, _ = _make_middleware(task_id_resolver=lambda runtime: "custom-task-id")
        await mw.aafter_agent({}, _FakeRuntime())
        assert client.calls[0]["payload"]["id"] == "custom-task-id"
        assert client.calls[0]["bearer"] == "signed:custom-task-id"

    async def test_no_task_id_raises_emission_error(self):
        mw, client, _ = _make_middleware()
        runtime = _FakeRuntime(run_id=None, thread_id=None)
        await mw.abefore_agent({}, runtime)
        with pytest.raises(PushEmissionError, match="task id"):
            await mw.aafter_agent({}, runtime)
        assert client.calls == []

    async def test_dispatch_failure_swallowed(self):
        mw, client, _ = _make_middleware()
        client.succeed = False
        await mw.aafter_agent({"messages": []}, _FakeRuntime())
        assert len(client.calls) == 1  # logged, not raised

    async def test_last_message_summary_in_payload(self):
        mw, client, _ = _make_middleware()
        await mw.aafter_agent({"messages": [{"role": "user", "content": "x"}]}, _FakeRuntime())
        assert client.calls[0]["payload"]["metadata"].get("summary") == "x"

    async def test_summary_coerces_non_string_content(self):
        mw, client, _ = _make_middleware()
        await mw.aafter_agent(
            {"messages": [{"role": "ai", "content": {"text": "structured"}}]},
            _FakeRuntime(),
        )
        summary = client.calls[0]["payload"]["metadata"].get("summary")
        assert isinstance(summary, str)
        assert "structured" in summary

    async def test_build_or_sign_failure_raises(self):
        class _RaisingSigner:
            def sign(self, task_id, *, now=None):
                raise RuntimeError("signing key unavailable")

        client = _FakePushClient()
        mw = A2APushNotifierMiddleware(  # type: ignore[arg-type]
            push_config=PushNotificationConfig(
                url="https://receiver.example/a2a/notifications", token="t"
            ),
            signer=_RaisingSigner(),
            push_client=client,
        )
        with pytest.raises(PushEmissionError, match="build/sign"):
            await mw.aafter_agent({"messages": [{"role": "ai", "content": "x"}]}, _FakeRuntime())
        assert client.calls == []  # a build failure is surfaced, not swallowed

    async def test_build_or_sign_failure_on_failed_run_preserves_model_error(self):
        """awrap_model_call must propagate the model error, not the notify error."""

        class _RaisingSigner:
            def sign(self, task_id, *, now=None):
                raise RuntimeError("signing key unavailable")

        client = _FakePushClient()
        mw = A2APushNotifierMiddleware(  # type: ignore[arg-type]
            push_config=PushNotificationConfig(
                url="https://receiver.example/a2a/notifications", token="t"
            ),
            signer=_RaisingSigner(),
            push_client=client,
        )

        async def _handler(request):
            raise RuntimeError("model failed")

        with pytest.raises(RuntimeError, match="model failed"):
            await mw.awrap_model_call(object(), _handler)
        assert client.calls == []


class TestOptionalSigner:
    """Without a signer the emitter sends unsigned notifications."""

    async def test_no_signer_sends_unsigned(self):
        client = _FakePushClient()
        mw = A2APushNotifierMiddleware(
            push_config=PushNotificationConfig(
                url="https://receiver.example/a2a/notifications", token="t"
            ),
            signer=None,
            push_client=client,
        )
        await mw.aafter_agent({"messages": [{"role": "ai", "content": "x"}]}, _FakeRuntime())
        assert len(client.calls) == 1
        assert client.calls[0]["bearer"] is None
        assert client.calls[0]["payload"]["status"]["state"] == "TASK_STATE_COMPLETED"

    async def test_strict_dispatch_without_signer_raises(self):
        strict_cfg = PushNotificationConfig(
            url="https://receiver.example/a2a/notifications", token="t", mode="strict"
        )
        with pytest.raises(PushNotificationConfigError, match="strict-mode"):
            A2APushNotifierMiddleware(  # type: ignore[arg-type]
                push_config=strict_cfg, signer=None, push_client=_FakePushClient()
            )

    async def test_strict_dispatch_with_signer_ok(self):
        strict_cfg = PushNotificationConfig(
            url="https://receiver.example/a2a/notifications", token="t", mode="strict"
        )
        mw = A2APushNotifierMiddleware(  # type: ignore[arg-type]
            push_config=strict_cfg, signer=_FakeSigner(), push_client=_FakePushClient()
        )
        await mw.aafter_agent({}, _FakeRuntime())
        assert mw._signer is not None


class TestPushConfigMode:
    def test_from_dict_reads_mode(self):
        cfg = PushNotificationConfig.from_dict({"url": "https://r", "token": "t", "mode": "strict"})
        assert cfg.mode == "strict"

    def test_from_dict_defaults_to_dev(self):
        cfg = PushNotificationConfig.from_dict({"url": "https://r", "token": "t"})
        assert cfg.mode == "dev"

    def test_factory_raises_for_strict_without_signer(self):
        with pytest.raises(PushNotificationConfigError):
            build_a2a_notifier_from_config(
                {
                    "configurable": {
                        "a2a_push_config": {
                            "url": "https://receiver.example/a2a/notifications",
                            "token": "t",
                            "mode": "strict",
                        }
                    }
                },
                signer=None,
                push_client=_FakePushClient(),  # type: ignore[arg-type]
            )
