"""三模型（qwen/longcat/mimo）公告事件提取对比测试"""
import asyncio
import time
from datetime import datetime, timezone, timedelta

from trading.research.sentiment.models import EventBundle, EventSourceRecord
from trading.research.sentiment.event_extractor import _prompt, _json_payload
from trading.research.sentiment.schemas import ModelSentimentExtraction
from router.services.provider_registry import get_model_provider

TZ = timezone(timedelta(hours=8))

SYSTEM_PROMPT = "Return one strict JSON object matching the supplied schema. Do not output BUY/SELL/HOLD."


def make_bundle(record_id, title, content, pub_time, source, url):
    payload = {'title': title, 'content': content, 'publisher': source}
    src = EventSourceRecord(
        record_id=record_id, event_time=pub_time, fetched_at=pub_time,
        source_name=source, source_url=url, source_level='official',
        verified=True, content_hash='hash', payload=payload,
    )
    return EventBundle(
        event_cluster_id='evt_' + record_id[-8:], canonical_title=title,
        cluster_event_type='announcement', event_time=pub_time, data_cutoff=pub_time,
        primary_source_id=record_id, source_count=1, source_records=[src],
        symbols=['600664'], sectors=[], dedup_method='single', dedup_version='v1',
        cluster_hash='cluster_hash',
    )


async def test_one(provider_name, bundle):
    provider = get_model_provider(provider_name)
    start = time.perf_counter()
    try:
        resp = await provider.invoke(
            role='announcement_verifier',
            prompt=_prompt(bundle),
            system_prompt=SYSTEM_PROMPT,
            temperature=0.0,
            max_tokens=800,
        )
        elapsed = time.perf_counter() - start
        content = getattr(resp, 'content', None)
        if not content:
            return provider_name, elapsed, '空内容', None
        extraction = ModelSentimentExtraction.model_validate(_json_payload(content))
        return provider_name, elapsed, '成功', extraction
    except Exception as exc:
        elapsed = time.perf_counter() - start
        return provider_name, elapsed, f'失败({str(exc)[:50]})', None


async def main():
    tests = [
        ('业绩预增', '哈药股份2026年半年度业绩预增公告',
         '公司预计2026年上半年归母净利润同比大幅增长，主要系主营业务收入增长及费用管控改善。',
         datetime(2026, 7, 10, 9, 0, tzinfo=TZ)),
        ('异常波动', '哈药股份股票交易严重异常波动暨风险提示公告',
         '公司股票连续多日涨停，交易异常波动，提醒投资者注意风险，理性投资。',
         datetime(2026, 7, 24, 17, 0, tzinfo=TZ)),
        ('减持', '关于控股股东减持股份的公告',
         '控股股东拟在未来6个月内通过集中竞价减持不超过总股本1%的股份。',
         datetime(2026, 7, 15, 18, 0, tzinfo=TZ)),
    ]
    models = ['qwen', 'longcat', 'mimo']

    for name, title, content, t in tests:
        bundle = make_bundle(name, title, content, t, '公司公告', f'http://x/{name}')
        print(f'\n{"="*60}')
        print(f'测试: {name}')
        print(f'{"="*60}')
        results = await asyncio.gather(*[test_one(m, bundle) for m in models])
        for provider, elapsed, status, extraction in results:
            detail = ''
            if extraction:
                detail = f' | {extraction.event_type} dir={extraction.direction} inten={extraction.intensity}'
            print(f'  {provider:<10} {elapsed:>6.2f}s  {status}{detail}')


if __name__ == '__main__':
    asyncio.run(main())
