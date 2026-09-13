"""Thin OpenAI-compatible client (spec D1). One socket serves NVIDIA NIM,
OpenRouter, Ollama, LM Studio, OpenAI — they all speak this API."""
from __future__ import annotations
from collections.abc import Iterator

from openai import OpenAI
from backend.config import AppConfig


class LLMClient:
    def __init__(self, cfg: AppConfig):
        # Explicit timeouts: the default client would hold a thread and an SSE
        # connection indefinitely against a hung local server (Ollama/LM Studio).
        # 300s per read still allows long generations — tokens arriving reset it.
        self.cfg = cfg
        self._client = OpenAI(base_url=cfg.llm.base_url, api_key=cfg.llm.api_key or "unused",
                              timeout=300.0, max_retries=2)
        self.max_context = cfg.llm.max_context or 8192

    def active_model(self) -> str | None:
        """Which model is active: fast_model when fast mode is on, else model."""
        if self.cfg.llm.active == "fast" and self.cfg.llm.fast_model:
            return self.cfg.llm.fast_model
        return self.cfg.llm.model

    def _completion_kwargs(self, model: str, params: dict) -> dict:
        """Shared extra-body defaults (cost/quality tweaks) for a given model."""
        defaults = {}
        if model and "deepseek" in model.lower():
            defaults["extra_body"] = {**defaults.get("extra_body", {}), "reasoning_effort": "low"}
        if model and "gpt-oss" in model.lower():
            defaults["extra_body"] = {**defaults.get("extra_body", {}), "repetition_penalty": 1.1}
        defaults.update(params)
        return defaults

    def complete(self, messages: list[dict], model: str | None = None, **params) -> str:
        """Non-streaming completion. Defaults to the active model; pass model to
        force a specific tier (e.g. the fast model for high-volume background work
        like objective extraction regardless of the active chat toggle)."""
        model = model or self.active_model()
        defaults = dict(model=model, messages=messages, stream=False,
                        max_tokens=params.pop("max_tokens", 1200))
        defaults.update(params)
        defaults = self._completion_kwargs(model, defaults)
        resp = self._client.chat.completions.create(**defaults)
        if not resp.choices:
            return ""
        return resp.choices[0].message.content or ""

    def stream(self, messages: list[dict], **params) -> Iterator[str]:
        model = self.active_model()
        defaults = dict(model=model, messages=messages, stream=True)
        # NVIDIA-hosted DeepSeek models default to a heavy reasoning mode that delays
        # the first token by minutes. Force the fastest reasoning level (low); the fast
        # model repeats — add a mild repetition penalty. Shared with complete().
        defaults = self._completion_kwargs(model, defaults)
        for chunk in self._client.chat.completions.create(**defaults):
            # Some providers emit trailing chunks with no choices (e.g. a usage/final
            # chunk). Guard so an empty choices list doesn't crash the whole stream.
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    def vision(self, messages: list[dict], page_image_b64: str | None = None,
               model: str | None = None, **params) -> str:
        """Send a multimodal request (text + one page image) and return the full reply.
        Use cfg.llm.vision_model, or the given model. The image is injected as a user
        image_url content block appended to the final user message.

        A request that carries an image MUST go to a vision-capable model. We never fall
        back to the standard (text-only) chat model here — doing so makes the provider
        reject the request with "model does not support image input"."""
        import copy
        if page_image_b64:
            # Multi-image path: require an explicit vision model; raise rather than
            # silently sending an image to a text-only model (callers degrade gracefully).
            model = model or self.cfg.llm.vision_model
            if not model:
                raise ValueError("No vision model configured; cannot send an image.")
        else:
            model = model or (self.cfg.llm.vision_model or self.cfg.llm.model)
        msgs = copy.deepcopy(messages)
        if page_image_b64:
            target = msgs[-1]
            if isinstance(target.get("content"), str):
                target["content"] = [
                    {"type": "text", "text": target["content"]},
                ]
            elif not isinstance(target["content"], list):
                target["content"] = [{"type": "text", "text": str(target["content"])}]
            target["content"].append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{page_image_b64}"},
            })
        defaults = dict(model=model, messages=msgs, stream=False,
                        max_tokens=params.pop("max_tokens", 1200))
        defaults.update(params)
        resp = self._client.chat.completions.create(**defaults)
        if not resp.choices:
            return ""
        return resp.choices[0].message.content or ""


def get_llm(cfg: AppConfig) -> LLMClient:
    return LLMClient(cfg)


if __name__ == "__main__":
    from unittest.mock import patch, MagicMock
    from backend.config import AppConfig, LLMSection
    cfg = AppConfig(llm=LLMSection(base_url="http://x/v1", api_key="k", model="m", max_context=4096))
    with patch("__main__.OpenAI") as MockOpenAI:
        client = MagicMock()
        MockOpenAI.return_value = client
        chunk = MagicMock()
        chunk.choices = [MagicMock(delta=MagicMock(content="hello"))]
        client.chat.completions.create.return_value = iter([chunk])
        llm = get_llm(cfg)
        assert llm.max_context == 4096
        out = "".join(llm.stream([{"role": "user", "content": "hi"}]))
        assert out == "hello", f"mocked stream should replay deltas, got {out!r}"
        # non-DeepSeek model must NOT force reasoning_effort
        called = client.chat.completions.create.call_args.kwargs
        assert "extra_body" not in called, f"non-DeepSeek model should not set extra_body, got {called}"
        # DeepSeek model (NVIDIA) should force the fast 'low' reasoning level
        cfg2 = AppConfig(llm=LLMSection(base_url="http://x/v1", api_key="k", model="deepseek-ai/deepseek-v4-flash-0731"))
        with patch("__main__.OpenAI") as Mock2:
            c2 = MagicMock()
            Mock2.return_value = c2
            c2.chat.completions.create.return_value = iter([chunk])
            llm2 = get_llm(cfg2)
            "".join(llm2.stream([{"role": "user", "content": "hi"}]))
            kw = c2.chat.completions.create.call_args.kwargs
            assert kw.get("extra_body") == {"reasoning_effort": "low"}, f"DeepSeek should force low reasoning, got {kw}"
    print("llm_client OK")
