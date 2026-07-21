"""Run redacted discovery and minimal live checks for configured providers."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import SecretStr

from config.settings import settings
from router.config import router_settings
from router.providers import DeepSeekProvider, LongCatProvider, MiMoProvider, QwenProvider


@dataclass(frozen=True)
class ProviderProbe:
    name: str
    base_url: str | None
    secret: SecretStr | None
    configured_model: str


def provider_probes() -> tuple[ProviderProbe, ...]:
    return (
        ProviderProbe(
            "deepseek",
            router_settings.deepseek_base_url,
            router_settings.deepseek_api_key,
            router_settings.deepseek_model,
        ),
        ProviderProbe(
            "qwen",
            router_settings.qwen_base_url,
            router_settings.qwen_api_key,
            router_settings.qwen_model,
        ),
        ProviderProbe(
            "mimo",
            router_settings.mimo_base_url,
            router_settings.mimo_api_key,
            router_settings.mimo_model,
        ),
        ProviderProbe(
            "longcat",
            router_settings.longcat_base_url,
            router_settings.longcat_api_key,
            router_settings.longcat_model,
        ),
    )


def safe_error(exc: Exception) -> str:
    message = str(exc).replace("\r", " ").replace("\n", " ")
    return f"{type(exc).__name__}: {message[:300]}"


async def discover_models(probe: ProviderProbe) -> dict[str, Any]:
    if probe.secret is None or not probe.base_url:
        return {"provider": probe.name, "status": "not_configured"}
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout=30, connect=15),
            trust_env=False,
        ) as client:
            response = await client.get(
                probe.base_url.rstrip("/") + "/models",
                headers={"Authorization": f"Bearer {probe.secret.get_secret_value().strip()}"},
            )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        model_ids = sorted(
            str(item["id"])
            for item in (data or [])
            if isinstance(item, dict) and item.get("id")
        )
        relevant = [
            model_id
            for model_id in model_ids
            if any(token in model_id.lower() for token in ("deepseek", "mimo", "qwen", "longcat"))
        ]
        return {
            "provider": probe.name,
            "status": "ok",
            "configured_model": probe.configured_model,
            "configured_model_listed": probe.configured_model in model_ids,
            "model_count": len(model_ids),
            "relevant_models": relevant[:20],
        }
    except Exception as exc:
        return {
            "provider": probe.name,
            "status": "error",
            "configured_model": probe.configured_model,
            "error": safe_error(exc),
        }


async def invoke_models() -> list[dict[str, Any]]:
    providers = (
        ("deepseek", DeepSeekProvider(), "risk_controller"),
        ("qwen", QwenProvider(), "announcement_verifier"),
        ("mimo", MiMoProvider(), "vision_reader"),
        ("longcat", LongCatProvider(), "news_processor"),
    )
    results: list[dict[str, Any]] = []
    for name, provider, role in providers:
        try:
            response = await provider.invoke(
                role=role,
                prompt="Return only the word OK.",
                system_prompt="This is a minimal provider connectivity check.",
                temperature=0,
                max_tokens=64,
            )
            results.append(
                {
                    "provider": name,
                    "status": "ok",
                    "model": response.model,
                    "latency_ms": response.latency_ms,
                    "usage": response.usage,
                    "content_nonempty": bool(response.content),
                }
            )
        except Exception as exc:
            results.append({"provider": name, "status": "error", "error": safe_error(exc)})
    return results


async def check_tavily() -> dict[str, Any]:
    if not settings.tavily_api_key:
        return {"provider": "tavily", "status": "not_configured"}
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout=30, connect=15),
            trust_env=False,
        ) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": settings.tavily_api_key,
                    "query": "Shanghai Stock Exchange official website",
                    "search_depth": "basic",
                    "max_results": 1,
                    "include_answer": False,
                },
            )
        response.raise_for_status()
        payload = response.json()
        results = payload.get("results") if isinstance(payload, dict) else None
        return {
            "provider": "tavily",
            "status": "ok",
            "result_count": len(results) if isinstance(results, list) else 0,
        }
    except Exception as exc:
        return {"provider": "tavily", "status": "error", "error": safe_error(exc)}


async def run(*, invoke: bool) -> dict[str, Any]:
    discovery = [await discover_models(probe) for probe in provider_probes()]
    payload: dict[str, Any] = {"model_discovery": discovery, "secrets_printed": False}
    if invoke:
        payload["model_invocations"] = await invoke_models()
        payload["search_invocation"] = await check_tavily()
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--invoke",
        action="store_true",
        help="Make minimal billable model calls and one Tavily search",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = asyncio.run(run(invoke=args.invoke))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    sections = [payload["model_discovery"]]
    if args.invoke:
        sections.extend([payload["model_invocations"], [payload["search_invocation"]]])
    failed = any(item.get("status") == "error" for section in sections for item in section)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
