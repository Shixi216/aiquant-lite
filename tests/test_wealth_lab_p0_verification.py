"""四项 P0 的定向回归：临时独立库、无网络、无真实 AI 调用。"""
import asyncio
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException

from trading.wealth_lab import routes_v2 as routes
from trading.wealth_lab.backtest import run_strategy_backtest
from trading.wealth_lab.repository import WealthLabRepository
from trading.wealth_lab.strategies import get_strategy


def matrix():
    rng = np.random.default_rng(12)
    columns = ['510300.SH', '511010.SH', '518880.SH', '511380.SH']
    return pd.DataFrame(100*np.cumprod(1+rng.normal(0, [.012,.0001,.009,.003], (300,4)),axis=0),
                        index=pd.bdate_range('2023-01-02', periods=300), columns=columns)


def test_initial_cost_cash_and_daily_nav_agree():
    frame = matrix().iloc[:40] * 0 + 100
    strategy = get_strategy('S0_fixed_weight', weights={'511010.SH': .5})
    out = run_strategy_backtest(frame, strategy, start_date=frame.index[0].date(),
                               end_date=frame.index[-1].date(), cost_bps=10)
    np.testing.assert_allclose(out.nav, .9995)
    np.testing.assert_allclose(out.cash_weight, .5)
    np.testing.assert_allclose(out.nav, np.cumprod(1+out.daily_returns))
    assert out.rebalances[0].cost == 500
    assert len(out.rebalances) == len(out.weight_history) == 2
    assert out.rebalances[1].cost == 0
    assert out.drawdown[0] == pytest.approx(-.0005)


def test_cap_and_no_future_prices():
    frame = matrix()
    strategy = get_strategy('S4_inverse_volatility')
    assert max(strategy.target_weights(frame).values()) <= .6
    small = get_strategy('S4_inverse_volatility', max_weight=.2)
    assert sum(small.target_weights(frame.iloc[:0]).values()) == pytest.approx(.8)
    seen = []
    original = strategy.target_weights
    def spy(history):
        seen.append(history.index[-1] if len(history) else None)
        return original(history)
    strategy.target_weights = spy
    out = run_strategy_backtest(frame, strategy, start_date=frame.index[0].date(), end_date=frame.index[-1].date())
    assert seen[0] is None
    for cutoff, snap in zip(seen[1:], out.weight_history[1:]):
        assert cutoff < pd.Timestamp(snap['date'])
    altered = frame.copy()
    altered.iloc[200:] *= [10,.1,5,2]
    other = run_strategy_backtest(altered, get_strategy('S4_inverse_volatility'),
        start_date=frame.index[0].date(), end_date=frame.index[-1].date())
    assert [s for s in out.weight_history if s['date'] <= str(frame.index[200].date())] == [s for s in other.weight_history if s['date'] <= str(frame.index[200].date())]


@pytest.fixture
def setup_routes(tmp_path, monkeypatch):
    repo = WealthLabRepository(tmp_path/'wealth.duckdb')
    monkeypatch.setattr(routes, 'repository', repo)
    frame = matrix()
    monkeypatch.setattr(repo, 'read_close_matrix', lambda symbols: frame[symbols])
    monkeypatch.setattr(repo, 'asset_coverage', lambda symbol: {'rows':300, 'last':'2024-02-23'})
    import trading.wealth_lab.decision_packets as packet_module
    monkeypatch.setattr(packet_module, 'WEALTH_LAB_DB_PATH', tmp_path/'wealth.duckdb')
    run = routes._backtest('S2_cross_momentum', routes.StrategyBacktestBody(
        start_date=frame.index[20].date(), end_date=frame.index[260].date(), cost_bps=17,
        params_override={'lookback_days':21}))
    return repo, run


def test_research_review_packet_share_exact_evidence(setup_routes, monkeypatch):
    repo, run = setup_routes
    from trading.wealth_lab.ai.agents import WealthResearchAgent, WealthRiskReviewer
    seen = {}
    async def research(self, facts):
        seen['facts'] = facts
        return {'research_id':'test-research', 'summary':'research', 'model_call_ids':['call-test']}
    async def review(self, research, numbers):
        seen['review'] = research
        seen['numbers'] = numbers
        return {'review_id':'test-review', 'verdict':'SUPPORTED', 'model_call_ids':['review-test']}
    monkeypatch.setattr(WealthResearchAgent, 'run', research)
    monkeypatch.setattr(WealthRiskReviewer, 'run', review)
    monkeypatch.setattr(routes, '_backtest', lambda *a, **k: pytest.fail('must not rerun an explicit backtest'))
    result = asyncio.run(routes.v2_ai_research(routes.AiResearchBody(
        strategy_id='S2_cross_momentum', backtest_id=run['backtest_id'])))
    assert result['data']['used_backtest_id'] == run['backtest_id']
    asyncio.run(routes.v2_ai_risk_review(routes.AiRiskReviewBody(research_id='test-research')))
    assert seen['numbers'] == run['metrics']
    assert seen['review']['facts'] == seen['facts']
    packet = routes.v2_create_packet(routes.DecisionPacketBody(strategy_id='S2_cross_momentum',
        research_id='test-research', weights={'511010.SH':.6}))['data']
    assert packet['metrics'] == run['metrics']
    assert packet['evidence_ids'] == [run['backtest_id']]
    assert packet['backtest_window'] == {'start_date':run['series']['dates'][0], 'end_date':run['series']['dates'][-1]}
    assert packet['cost_assumptions']['cost_bps'] == 17
    assert packet['ai_risk_review']['review_id'] == 'test-review'
    assert packet['model_call_ids'] == ['call-test','review-test']
    with pytest.raises(HTTPException) as exc:
        routes.v2_create_packet(routes.DecisionPacketBody(strategy_id='S4_inverse_volatility',
            research_id='test-research', weights={'511010.SH':.6}))
    assert exc.value.status_code == 422


def test_missing_evidence_stays_warning(setup_routes):
    result = asyncio.run(routes.v2_ai_risk_review(routes.AiRiskReviewBody(research_id='unknown', claims=['年化100%'])))
    assert result['data']['verdict'] == 'NEEDS_MORE_EVIDENCE'
    assert 'MISSING_COMPUTED_EVIDENCE' in result['warnings']
    result = routes.v2_create_packet(routes.DecisionPacketBody(strategy_id='S2_cross_momentum', weights={'511010.SH':.6}))
    assert result['data']['metrics'] == {}
    assert result['data']['risk_check']['verdict'] == 'WARN'
    assert result['data']['backtest_window']['start_date'] is None


def test_report_uses_saved_request_not_current_data(setup_routes, monkeypatch):
    repo, run = setup_routes
    monkeypatch.setattr(repo, 'read_close_matrix', lambda *a, **k: pytest.fail('report must not reread latest prices'))
    report = routes.v2_report(run['backtest_id'])['data']
    assert report['metrics'] == run['metrics']
    record = repo.load_backtest(run['backtest_id'])
    assert record['params']['params_override'] == {'lookback_days':21}
    assert record['params']['cost_bps'] == 17
    repo.save_backtest('legacy', {'strategy_id':'S2_cross_momentum'}, run)
    with pytest.raises(HTTPException) as exc:
        routes.v2_report('legacy')
    assert exc.value.status_code == 409


def test_factor_percentile_matches_raw_score_order():
    from trading.wealth_lab.factors import score_factors
    frame = matrix()
    repo = SimpleNamespace(read_close_matrix=lambda symbols:frame,
        liquidity_20d_avg=lambda symbols, asof:{s:float(i+1)*1000 for i,s in enumerate(frame.columns)})
    result = score_factors(repo, list(frame.columns))
    rows = pd.DataFrame(result['factors']).set_index('symbol')
    raw = rows.momentum_126d.rank()+rows.trend_score.rank()+rows.liquidity_20d_avg_amount_cny.rank()-rows.volatility_63d.rank()-rows.max_drawdown_252d.abs().rank()
    np.testing.assert_allclose(rows.composite_rank, raw.rank(pct=True))
    assert rows.composite_rank.between(0,1).all()


def test_adjustment_factors_never_filled_from_future_or_one():
    from trading.wealth_lab.provider import DualSourceProvider, SourceUnavailableError
    daily = pd.DataFrame({'trade_date':['20240103','20240102'], 'close':[100.,100.]})
    partial = pd.DataFrame({'trade_date':['20240103'], 'adj_factor':[2.]})
    official = SimpleNamespace(ready=True, fund_daily=lambda *a:daily, fund_adj=lambda *a:partial)
    provider = DualSourceProvider(official=official, proxy=SimpleNamespace(ready=False))
    with pytest.raises(SourceUnavailableError):
        provider.fetch_fund_daily('A','20240102','20240103')
    official.fund_adj = lambda *a:pd.DataFrame({'trade_date':['20240103','20240102'], 'adj_factor':[2.,1.]})
    frame, _ = provider.fetch_fund_daily('A','20240102','20240103')
    assert frame.hfq_close.tolist() == [100.,200.]
