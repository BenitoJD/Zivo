"""Router-backed model pool: cooldown + failover config sanity."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.llm_router_pool import (
    ALLOWED_FAILS,
    COOLDOWN_SECONDS,
    RATE_LIMIT_RETRIES,
    build_router,
    router_enabled,
    router_model_list,
)


def _fake_model(litellm_model: str, *, is_default: bool = False, api_key: str = "k"):
    record = MagicMock()
    record.litellm_model = litellm_model
    record.is_default = is_default
    record.meta = {}
    provider = MagicMock()
    provider.api_key = api_key
    provider.api_base_url = None
    provider.slug = "openai"
    provider.litellm_prefix = "openai"
    provider.extra_env = {}
    m = MagicMock()
    m.litellm_model = litellm_model
    m.record = record
    m.provider = provider
    return m


def test_router_model_list_shape() -> None:
    models = [_fake_model("openai/a"), _fake_model("openai/b")]
    ml = router_model_list(models)
    assert [m["model_name"] for m in ml] == ["openai/a", "openai/b"]
    assert ml[0]["litellm_params"]["model"] == "openai/a"
    assert ml[0]["litellm_params"]["api_key"] == "k"


def test_router_cooldown_and_retry_config() -> None:
    models = [_fake_model("openai/a", is_default=True), _fake_model("openai/b")]
    with patch("app.services.llm_router_pool.Router") as router_cls:
        build_router(models)
        kwargs = router_cls.call_args.kwargs
        assert kwargs["cooldown_time"] == COOLDOWN_SECONDS
        assert kwargs["allowed_fails"] == ALLOWED_FAILS
        assert kwargs["retry_policy"]["RateLimitErrorRetries"] == RATE_LIMIT_RETRIES
        # Fallbacks must be keyed by the PRIMARY model group name — litellm
        # matches the key against the requested model; anything else is dead config.
        assert kwargs["fallbacks"] == [{"openai/a": ["openai/b"]}]
        # Router never double-retries (policy governs).
        assert kwargs["num_retries"] == 0
        # Recommended production routing: no per-request overhead.
        assert kwargs["routing_strategy"] == "simple-shuffle"
        # Pre-flight context check on.
        assert kwargs["enable_pre_call_checks"] is True


def test_router_enabled_default() -> None:
    assert router_enabled() is True
