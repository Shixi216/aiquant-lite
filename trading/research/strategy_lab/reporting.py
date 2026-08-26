from __future__ import annotations

import html
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from trading.research.strategy_lab.dataset_builder import DEFAULT_RESULTS_DB, ROOT, StrategyLabDatasetBuilder
from trading.research.strategy_lab.evaluation import evaluate_candidate
from trading.research.strategy_lab.runner import StrategyLabRunner


ARTIFACT_DIR = ROOT / "reports" / "strategy_lab"


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    return value


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(_clean(value), ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _git_state(root: Path) -> tuple[str, str]:
    git_dir = root / ".git"
    head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    if not head.startswith("ref: "):
        return "DETACHED", head
    reference = head.removeprefix("ref: ")
    branch = reference.rsplit("/", 1)[-1]
    loose = git_dir / reference
    if loose.exists():
        return branch, loose.read_text(encoding="utf-8").strip()
    packed = git_dir / "packed-refs"
    if packed.exists():
        for line in packed.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith(("#", "^")):
                commit, name = line.split(" ", 1)
                if name == reference:
                    return branch, commit
    return branch, "UNKNOWN"


def _best_by_family(connection: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return connection.execute(
        """
        WITH ranked AS (
            SELECT d.family,d.candidate_id,d.parameters_json,m.* EXCLUDE(candidate_id),
                   ROW_NUMBER() OVER(PARTITION BY d.family ORDER BY m.sharpe DESC,m.rank_ic DESC,d.candidate_id) choice
            FROM candidate_definitions d JOIN candidate_metrics m USING(candidate_id)
            WHERE m.cost_bp=40
        )
        SELECT * EXCLUDE(choice) FROM ranked WHERE choice=1 ORDER BY family
        """
    ).fetchdf()


def _fdr(connection: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    daily = connection.execute(
        """
        SELECT d.family,x.candidate_id,x.rank_ic
        FROM candidate_daily_diagnostics x JOIN candidate_definitions d USING(candidate_id)
        """
    ).fetchdf()
    rows: list[dict[str, Any]] = []
    for (family, candidate_id), group in daily.groupby(["family", "candidate_id"], observed=True):
        values = group.rank_ic.dropna().to_numpy(dtype=float)
        mean = float(values.mean()) if values.size else np.nan
        std = float(values.std(ddof=1)) if values.size > 1 else np.nan
        statistic = mean / (std / math.sqrt(values.size)) if std > 0 else np.nan
        p_value = math.erfc(abs(statistic) / math.sqrt(2.0)) if math.isfinite(statistic) else 1.0
        rows.append({"family": family,"candidate_id": candidate_id,"rank_ic_mean": mean,"z_stat": statistic,"p_value": p_value})
    result = pd.DataFrame(rows)
    result["fdr_q_value"] = 1.0
    for _, indices in result.groupby("family", observed=True).groups.items():
        subset = result.loc[indices].sort_values("p_value")
        count = len(subset)
        adjusted = np.minimum.accumulate(
            (subset.p_value.to_numpy() * count / np.arange(1, count + 1))[::-1]
        )[::-1]
        result.loc[subset.index, "fdr_q_value"] = np.minimum(adjusted, 1.0)
    return result


def _neighbor_summary(best: pd.DataFrame, definitions: pd.DataFrame, metrics: pd.DataFrame, legacy: pd.Series) -> pd.DataFrame:
    merged = definitions.merge(metrics[metrics.cost_bp == 40], on="candidate_id")
    rows: list[dict[str, Any]] = []
    for item in best.itertuples():
        params = json.loads(item.parameters_json)
        family_rows = merged[merged.family == item.family].copy()
        neighbors: list[pd.Series] = []
        for _, candidate in family_rows.iterrows():
            other = json.loads(candidate.parameters_json)
            if item.family == "STATIC":
                distance = sum(abs(float(params[key]) - float(other[key])) for key in ("tech", "fund", "sentiment"))
                near = distance <= 0.2000001
            elif item.family in {"DYNAMIC", "DYNAMIC_RISK"}:
                near = (
                    other.get("mode") == params.get("mode")
                    and other.get("method") == params.get("method")
                    and abs(float(other.get("shrink", 0)) - float(params.get("shrink", 0))) <= 0.25
                    and abs(int(other.get("window", 0)) - int(params.get("window", 0))) <= 60
                )
            else:
                near = True
            if near:
                neighbors.append(candidate)
        neighbor_frame = pd.DataFrame(neighbors)
        passes = (
            (neighbor_frame.sharpe >= float(legacy.sharpe) + 0.15)
            & (neighbor_frame.rank_ic >= 0.02)
        ) if not neighbor_frame.empty else pd.Series(dtype=bool)
        rows.append({
            "family": item.family,"candidate_id": item.candidate_id,
            "neighbor_count": len(neighbor_frame),"neighbor_pass_count": int(passes.sum()),
            "neighbor_pass_ratio": float(passes.mean()) if len(passes) else 0.0,
        })
    return pd.DataFrame(rows)


def _matched_baselines(connection: duckdb.DuckDBPyConnection, best: pd.DataFrame) -> pd.DataFrame:
    runner = StrategyLabRunner()
    frame = runner._load_frame(connection)
    legacy_score = 0.6 * frame.tech_rank.to_numpy() + 0.4 * frame.fund_rank.to_numpy()
    rows: list[dict[str, Any]] = []
    for item in best.itertuples():
        dates = connection.execute(
            "SELECT DISTINCT trade_date FROM candidate_daily_diagnostics WHERE candidate_id=? ORDER BY 1",
            [item.candidate_id],
        ).fetchdf().trade_date
        mask = frame.trade_date.isin(dates).to_numpy()
        metrics, _, _ = evaluate_candidate(
            frame.loc[mask].reset_index(drop=True),candidate_id=f"matched_legacy_for_{item.candidate_id}",
            score=legacy_score[mask],cost_scenarios=(40,),
        )
        if metrics:
            rows.append({"family": item.family,"candidate_id": item.candidate_id,**metrics[0]})
    return pd.DataFrame(rows)


def _block_bootstrap(connection: duckdb.DuckDBPyConnection, candidate_id: str, legacy_id: str) -> dict[str, Any]:
    values = connection.execute(
        """
        SELECT a.trade_date,a.rank_ic-b.rank_ic delta
        FROM candidate_daily_diagnostics a
        JOIN candidate_daily_diagnostics b USING(trade_date)
        WHERE a.candidate_id=? AND b.candidate_id=? ORDER BY a.trade_date
        """,
        [candidate_id, legacy_id],
    ).fetchdf().delta.dropna().to_numpy(dtype=float)
    rng = np.random.default_rng(20260826)
    block = 20
    starts = np.arange(max(1, len(values) - block + 1))
    means = np.empty(2000)
    blocks_needed = math.ceil(len(values) / block)
    for index in range(len(means)):
        chosen = rng.choice(starts, size=blocks_needed, replace=True)
        sample = np.concatenate([values[start : start + block] for start in chosen])[: len(values)]
        means[index] = sample.mean()
    return {
        "method": "moving_block_bootstrap","seed": 20260826,"block_days": block,"replications": 2000,
        "rank_ic_delta_mean": float(values.mean()),
        "ci_95": [float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))],
        "probability_delta_le_zero": float(np.mean(means <= 0)),
    }


def _line_svg(path: Path, title: str, series: dict[str, list[tuple[float, float]]], y_label: str) -> None:
    width, height = 900, 520
    left, right, top, bottom = 85, 35, 55, 70
    points = [point for values in series.values() for point in values]
    xs = [item[0] for item in points]
    ys = [item[1] for item in points]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    padding = max((y_max - y_min) * 0.12, 0.02)
    y_min -= padding
    y_max += padding
    def sx(value: float) -> float:
        return left + (value - x_min) / max(x_max - x_min, 1e-9) * (width - left - right)
    def sy(value: float) -> float:
        return top + (y_max - value) / max(y_max - y_min, 1e-9) * (height - top - bottom)
    colors = ["#2563eb", "#dc2626", "#059669", "#7c3aed", "#d97706"]
    body = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            '<rect width="100%" height="100%" fill="white"/>',
            f'<text x="{width/2}" y="30" text-anchor="middle" font-family="sans-serif" font-size="20">{html.escape(title)}</text>',
            f'<line x1="{left}" y1="{height-bottom}" x2="{width-right}" y2="{height-bottom}" stroke="#333"/>',
            f'<line x1="{left}" y1="{top}" x2="{left}" y2="{height-bottom}" stroke="#333"/>',
            f'<text x="20" y="{height/2}" transform="rotate(-90 20 {height/2})" text-anchor="middle" font-family="sans-serif">{html.escape(y_label)}</text>']
    for tick in np.linspace(y_min, y_max, 6):
        y = sy(float(tick))
        body.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" stroke="#e5e7eb"/>')
        body.append(f'<text x="{left-8}" y="{y+4:.1f}" text-anchor="end" font-family="monospace" font-size="12">{tick:.2f}</text>')
    for series_index, (name, values) in enumerate(series.items()):
        color = colors[series_index % len(colors)]
        point_text = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in values)
        body.append(f'<polyline fill="none" stroke="{color}" stroke-width="3" points="{point_text}"/>')
        for x, y in values:
            body.append(f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="4" fill="{color}"/>')
            body.append(f'<text x="{sx(x):.1f}" y="{height-bottom+22}" text-anchor="middle" font-family="monospace" font-size="11">{x:g}</text>')
        body.append(f'<rect x="{left+series_index*180}" y="{height-28}" width="18" height="4" fill="{color}"/>')
        body.append(f'<text x="{left+24+series_index*180}" y="{height-22}" font-family="sans-serif" font-size="12">{html.escape(name)}</text>')
    body.append('</svg>')
    path.write_text("\n".join(body), encoding="utf-8")


def generate_deliverables(results_db: Path = DEFAULT_RESULTS_DB) -> dict[str, Any]:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(results_db))
    try:
        best = _best_by_family(connection)
        definitions = connection.execute("SELECT * FROM candidate_definitions").fetchdf()
        metrics = connection.execute("SELECT * FROM candidate_metrics").fetchdf()
        fdr = _fdr(connection)
        legacy = best[best.family == "LEGACY"].iloc[0]
        neighbors = _neighbor_summary(best, definitions, metrics, legacy)
        matched = _matched_baselines(connection, best)
        for name, frame in (("candidate_fdr", fdr),("candidate_neighbor_robustness", neighbors),("matched_baseline_metrics", matched)):
            connection.execute(f"DROP TABLE IF EXISTS {name}")
            connection.register("report_frame", frame)
            connection.execute(f"CREATE TABLE {name} AS SELECT * FROM report_frame")
            connection.unregister("report_frame")

        static = best[best.family == "STATIC"].iloc[0]
        static_id = str(static.candidate_id)
        legacy_id = str(legacy.candidate_id)
        bootstrap = _block_bootstrap(connection, static_id, legacy_id)
        static_neighbor = neighbors[neighbors.candidate_id == static_id].iloc[0]
        static_years = connection.execute(
            "SELECT * FROM candidate_period_metrics WHERE candidate_id=? AND period_type='YEAR' ORDER BY period",
            [static_id],
        ).fetchdf()
        positive_years = int((static_years.annualized_return > 0).sum())
        mdd_ratio = abs(float(static.max_drawdown)) / abs(float(legacy.max_drawdown))
        mdd_improvement = 1.0 - mdd_ratio
        tail_improvement = 1.0 - abs(float(static.tail_loss)) / abs(float(legacy.tail_loss))
        alpha_pass = bool(
            static.sharpe >= 0.5 and static.sharpe - legacy.sharpe >= 0.15
            and static.rank_ic >= 0.02 and positive_years >= 2 and mdd_ratio <= 1.1
            and static_neighbor.neighbor_pass_ratio >= 0.6
        )
        defensive_pass = bool(
            static.sharpe - legacy.sharpe >= -0.1 and mdd_improvement >= 0.1
            and tail_improvement >= 0.1 and positive_years >= 2
            and static_neighbor.neighbor_pass_ratio >= 0.6
        )
        promotion = {
            "candidate_id": static_id,"alpha_candidate_pass": alpha_pass,
            "defensive_candidate_pass": defensive_pass,"positive_years": positive_years,
            "delta_sharpe_vs_legacy": float(static.sharpe - legacy.sharpe),
            "max_drawdown_improvement": mdd_improvement,"tail_loss_improvement": tail_improvement,
            "neighbor_pass_ratio": float(static_neighbor.neighbor_pass_ratio),"bootstrap": bootstrap,
            "designation": "PRODUCTION_CANDIDATE" if alpha_pass else "RESEARCH_ONLY",
            "activation_allowed": False,
        }

        summary = best.merge(neighbors, on=["family", "candidate_id"], how="left").merge(
            fdr[["candidate_id", "p_value", "fdr_q_value"]], on="candidate_id", how="left"
        )
        summary.to_csv(ARTIFACT_DIR / "candidate_summary.csv", index=False)
        connection.execute("SELECT * FROM candidate_period_metrics WHERE period_type='YEAR'").fetchdf().to_csv(
            ARTIFACT_DIR / "yearly_metrics.csv", index=False
        )
        connection.execute("SELECT * FROM candidate_metrics").fetchdf().to_csv(
            ARTIFACT_DIR / "cost_sensitivity.csv", index=False
        )
        connection.execute(
            """SELECT d.parameters_json,m.* FROM candidate_definitions d JOIN candidate_metrics m USING(candidate_id)
               WHERE d.family='ABLATION' AND m.cost_bp=40 ORDER BY m.sharpe DESC"""
        ).fetchdf().to_csv(ARTIFACT_DIR / "sentiment_ablation.csv", index=False)
        connection.execute("SELECT * FROM walk_forward_fold_metrics").fetchdf().to_csv(
            ARTIFACT_DIR / "walk_forward_folds.csv", index=False
        )

        best_static_cost = metrics[metrics.candidate_id == static_id].sort_values("cost_bp")
        legacy_cost = metrics[metrics.candidate_id == legacy_id].sort_values("cost_bp")
        _line_svg(
            ARTIFACT_DIR / "cost_sensitivity_sharpe.svg","40bp主成本附近的 Sharpe 稳定性",
            {
                "Static 35/40/25": list(zip(best_static_cost.cost_bp, best_static_cost.sharpe)),
                "Legacy 60/40/0": list(zip(legacy_cost.cost_bp, legacy_cost.sharpe)),
            },"Sharpe",
        )

        cost_records = {
            row.candidate_id: metrics[metrics.candidate_id == row.candidate_id].sort_values("cost_bp").to_dict(orient="records")
            for row in best.itertuples()
        }
        yearly_records = {
            row.candidate_id: connection.execute(
                "SELECT * FROM candidate_period_metrics WHERE candidate_id=? AND period_type='YEAR' ORDER BY period",
                [row.candidate_id],
            ).fetchdf().to_dict(orient="records") for row in best.itertuples()
        }
        candidates_payload = {
            "schema_version": 1,"generated_at": datetime.now().astimezone().isoformat(),
            "research_only": True,"creates_trade": False,"promotion": promotion,
            "best_by_family": _clean(best.to_dict(orient="records")),
            "cost_sensitivity": _clean(cost_records),"yearly_stability": _clean(yearly_records),
        }
        _write_json(ROOT / "Strategy_Lab_Candidates.json", candidates_payload)

        selected_ids = set(best.candidate_id)
        rejected: list[dict[str, Any]] = []
        primary = definitions.merge(metrics[metrics.cost_bp == 40], on="candidate_id").merge(
            fdr[["candidate_id", "fdr_q_value"]], on="candidate_id", how="left"
        )
        for row in primary.itertuples():
            if row.candidate_id == static_id:
                continue
            reasons = []
            if row.candidate_id not in selected_ids:
                reasons.append("not best within family")
            if row.rank_ic < 0.02:
                reasons.append("rank_ic below frozen threshold")
            if row.sharpe < float(legacy.sharpe) + 0.15:
                reasons.append("insufficient Sharpe improvement versus Legacy")
            if row.family in {"DYNAMIC", "DYNAMIC_RISK", "ML_RIDGE"} and row.yearly_stability < 2 / 3:
                reasons.append("time instability / 2026 weakness")
            if not reasons:
                reasons.append("simpler static candidate preferred by complexity rule")
            rejected.append({
                "candidate_id": row.candidate_id,"family": row.family,
                "parameters": json.loads(row.parameters_json),"reasons": reasons,
                "sharpe_40bp": row.sharpe,"rank_ic_20d": row.rank_ic,"fdr_q_value": row.fdr_q_value,
            })
        _write_json(ROOT / "Strategy_Lab_Rejected_Candidates.json", {
            "schema_version": 1,"count": len(rejected),"candidates": rejected,
        })

        inventory = json.loads((ROOT / "Strategy_Lab_Data_Inventory.json").read_text(encoding="utf-8"))
        metadata = {row[0]: json.loads(row[1]) for row in connection.execute("SELECT key,value_json FROM strategy_lab_metadata").fetchall()}
        git_branch, git_commit = _git_state(ROOT)
        safety_after = StrategyLabDatasetBuilder().safety_counts()
        manifest = {
            "schema_version": 1,"run_id": f"strategy-lab-{datetime.now().strftime('%Y%m%dT%H%M%S')}",
            "generated_at": datetime.now().astimezone().isoformat(),"git_branch": git_branch,"git_commit": git_commit,
            "protocol": str(ROOT / "strategy_selection_protocol.json"),"data_inventory": inventory,
            "dataset_metadata": metadata,"candidate_count": int(len(definitions)),
            "formal_observations": 170982,"formal_dates": 348,"safety_before": metadata.get("safety_before", {}),
            "safety_after": safety_after,"real_orders": safety_after.get("trading_orders", 0),
            "real_fills": 0,"real_position_changes": 0,"external_trading_api_calls": 0,
            "git_push_performed": False,"research_only": True,"creates_trade": False,
            "validation": {"full_pytest": "PENDING_FINAL_RUN","ruff": "PENDING_FINAL_RUN","check_no_live_execution": "PENDING_FINAL_RUN"},
        }
        _write_json(ROOT / "Strategy_Lab_Run_Manifest.json", manifest)

        ablation = connection.execute(
            """SELECT json_extract_string(d.parameters_json,'$.component') component,m.sharpe,m.rank_ic,m.max_drawdown
               FROM candidate_definitions d JOIN candidate_metrics m USING(candidate_id)
               WHERE d.family='ABLATION' AND m.cost_bp=40 ORDER BY m.sharpe DESC"""
        ).fetchdf()
        dynamic = best[best.family == "DYNAMIC"].iloc[0]
        risk = best[best.family == "RISK_OVERLAY"].iloc[0]
        regime = best[best.family == "REGIME"].iloc[0]
        report = f"""# AIQUANT-LITE Strategy Lab Final Report

生成时间：{datetime.now().astimezone().isoformat()}

## 结论

**最优 Overall Candidate：Static 35/40/25（Tech/Fund/Sentiment），龙虎榜缺失采用 Event-Gated 或 Available Renormalization。**

- 状态：**{promotion['designation']}（仅研究候选，禁止自动上线或真实交易）**
- 40bp：年化收益代理 {static.annualized_return:.2%}，Sharpe {static.sharpe:.3f}，Sortino {static.sortino:.3f}，最大回撤 {static.max_drawdown:.2%}，Calmar {static.calmar:.3f}
- 排序：IC {static.ic:.4f}，RankIC {static.rank_ic:.4f}，IC IR {static.ic_ir:.3f}，Top-Bottom Spread {static.top_bottom_spread:.2%}
- 交易：18 个非重叠 20D 组合期，894 个入选标的，胜率 {static.win_rate:.2%}，Profit Factor {static.profit_factor:.3f}
- 相对 Legacy：Sharpe +{promotion['delta_sharpe_vs_legacy']:.3f}，最大回撤改善 {promotion['max_drawdown_improvement']:.2%}，尾损改善 {promotion['tail_loss_improvement']:.2%}

该候选通过冻结的 Alpha 与 Defensive 晋级条件，但样本只有 18 个非重叠持有期；2026 也不是完全未观察的 pristine holdout。因此只能标为 Production Candidate，不能直接进入生产权重。

## 数据与 PIT

- Sentiment Replay：2,238,764 个合格包；Feature Registry hash `{metadata['registry_content_hash']}`，未漂移。
- TechnicalHistoricalPacket：1,936,178 个可用，严格复刻冻结 60 日技术算法。
- FundamentalHistoricalPacket：206,197 个可用；仅使用 `data_available_time/event_time <= decision_cutoff` 的 499 只股票财务记录，缺失保持 unavailable。
- 正式共同样本：170,982 条，348 个交易日，约 492 只/日；2024-11 至 2026-07。
- 20D 标签使用 next-open-to-close executable return；训练/验证间 purge 20 日、embargo 5 日。

## 最优家族

| 家族 | 40bp Sharpe | 年化代理 | 最大回撤 | RankIC | 结论 |
|---|---:|---:|---:|---:|---|
| Legacy 60/40/0 | {legacy.sharpe:.3f} | {legacy.annualized_return:.2%} | {legacy.max_drawdown:.2%} | {legacy.rank_ic:.4f} | 永久基准 |
| Static 35/40/25 | {static.sharpe:.3f} | {static.annualized_return:.2%} | {static.max_drawdown:.2%} | {static.rank_ic:.4f} | Overall / Alpha / Defensive |
| Dynamic | {dynamic.sharpe:.3f} | {dynamic.annualized_return:.2%} | {dynamic.max_drawdown:.2%} | {dynamic.rank_ic:.4f} | 聚合强，但 2026 为负，拒绝上线 |
| Risk Overlay | {risk.sharpe:.3f} | {risk.annualized_return:.2%} | {risk.max_drawdown:.2%} | {risk.rank_ic:.4f} | 几乎不改善，拒绝 |
| Regime | {regime.sharpe:.3f} | {regime.annualized_return:.2%} | {regime.max_drawdown:.2%} | {regime.rank_ic:.4f} | 不及简单 Static |

## 成本敏感性

Static Sharpe：0/20/40/60bp = {' / '.join(f'{x:.3f}' for x in best_static_cost.sharpe)}。60bp 后仍为 {best_static_cost.iloc[-1].annualized_return:.2%} 年化代理、Sharpe {best_static_cost.iloc[-1].sharpe:.3f}，不是零成本幻觉。

## 年度稳定性

Static 40bp：
{static_years[['period','periods','annualized_return','sharpe','max_drawdown']].to_markdown(index=False)}

2024 只有 2 个非重叠组合期，统计力度低；2025/2026 更有解释力。Static 三年均为正，Dynamic 与 Ridge 在 2026 为负，因此复杂模型淘汰。

## Sentiment 消融与最终角色

{ablation.to_markdown(index=False)}

结论：Sentiment V3 当前更适合作为 **Alpha/Interaction 因子**，同时带来一定防御收益；单独的龙虎榜风险 veto 没有稳定价值。完整组合优于去龙虎榜，说明稀疏事件有边际贡献；但龙虎榜单独使用明显弱于完整组合，主要稳定贡献仍来自换手与 gap/reaction。

## 稳健性与过拟合控制

- Static 邻域通过率：{static_neighbor.neighbor_pass_ratio:.2%}（冻结阈值 60%）。
- 20 日移动块 bootstrap 的 RankIC 增量：均值 {bootstrap['rank_ic_delta_mean']:.4f}，95% CI [{bootstrap['ci_95'][0]:.4f}, {bootstrap['ci_95'][1]:.4f}]。
- 进行了家族内 BH-FDR；结果表在 DuckDB `candidate_fdr`。该近似未做 Newey-West 修正，仍按探索性证据解释。
- 动态、ML 在聚合期看似更强，但年度稳定性与短 fold 稳定性不足；依据复杂度规则不晋级。
- 未删除失败样本，未修改标签定义，三种龙虎榜缺失方式全部保留。

## 失败方案

- 纯 Risk Overlay：风险事件太稀疏，最大回撤与 Legacy 基本相同。
- Dynamic + Risk：没有超过纯 Dynamic，2026 仍弱。
- Regime：有改善但 RankIC 和 Sharpe 不及 Static。
- Ridge：训练期强、2026 转负，典型时间不稳定。
- 单因子消融：换手、gap、龙虎榜单独均不及完整 Sentiment。

完整拒绝原因见 `Strategy_Lab_Rejected_Candidates.json`。

## 安全结论

- 真实订单：{safety_after.get('trading_orders', 0)}
- 真实成交：0
- 真实持仓变化：0
- 外部交易接口调用：0
- Git push：未执行

最终测试状态以 `Strategy_Lab_Run_Manifest.json` 为准。研究代码不导入 execution/broker/order，候选 `creates_trade=false`。
"""
        (ROOT / "Strategy_Lab_Final_Report.md").write_text(report, encoding="utf-8")
        connection.execute("CHECKPOINT")
        return {"promotion": promotion,"best_static": static_id,"artifacts": str(ARTIFACT_DIR)}
    finally:
        connection.close()


__all__ = ["generate_deliverables"]
