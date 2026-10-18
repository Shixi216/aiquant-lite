from datetime import datetime, timezone

import httpx
import pytest

from router.api.app import app
from router.config import RouterSettings
from router.services.provider_registry import get_model_provider
from scripts.check_live_specialist_providers import provider_probes
from scripts.preflight_check import collect_role_availability
from trading.research.policy_news.extractor import PolicyNewsExtractor
from trading.research.policy_news.schemas import PolicyRiskFlag, SchemaValidationStatus
from trading.research.sentiment.models import EventBundle, EventSourceRecord


def test_legacy_mimo_credentials_cannot_restore_roles_or_probe_candidates():
    settings = RouterSettings(
        _env_file=None, XIAOMI_API_KEY="test-only",
        OPC_MIMO_BASE_URL="https://mimo.example.invalid/v1",
    )
    assert settings.mimo_ready is False
    assert get_model_provider("mimo") is None
    assert "mimo" not in {p.name for p in provider_probes()}
    assert not {"vision_reader", "complex_vision"}.intersection(
        r.role for r in collect_role_availability(settings)
    )


@pytest.mark.asyncio
async def test_router_exposes_no_retired_visual_roles():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.get("/v1/roles")
        roles = response.json()["roles"]
        assert all(r["provider"] != "mimo" for r in roles)
        for name in ("vision_reader", "complex_vision"):
            assert (await client.get(f"/v1/roles/{name}")).status_code == 404


@pytest.mark.asyncio
async def test_image_only_policy_material_does_not_call_any_model():
    now = datetime(2026, 10, 19, tzinfo=timezone.utc)
    source = EventSourceRecord(
        record_id="image-test", event_time=now, fetched_at=now,
        source_name="synthetic", source_level="official", verified=True,
        payload={"title": "公告扫描件", "image_url": "https://example.invalid/image.png"},
    )
    bundle = EventBundle(
        event_cluster_id="image-event", canonical_title="公告扫描件",
        cluster_event_type="announcement", event_time=now, data_cutoff=now,
        primary_source_id=source.record_id, source_count=1, source_records=[source],
        symbols=["600000.SH"], sectors=[], dedup_method="single",
        dedup_version="test", cluster_hash="test",
    )

    def forbidden_provider(name):
        pytest.fail(f"Image-only input must not request a model: {name}")

    extractor = PolicyNewsExtractor(provider_factory=forbidden_provider, audit_store=object())
    result = await extractor.extract(bundle, allow_model_calls=True)
    assert result.validation_status == SchemaValidationStatus.NOT_CALLED
    assert PolicyRiskFlag.TEXT_INCOMPLETE in result.risk_flags
    assert result.used_model is False
    assert result.provider is None
    assert result.model_call_ids == []
