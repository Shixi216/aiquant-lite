from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from trading.research.strategy_lab.dataset_builder import (
    DEFAULT_RESULTS_DB,
    ROOT,
)
from trading.research.strategy_lab.evaluation import (
    PRIMARY_COST_BP,
    evaluate_candidate,
    period_metrics,
)


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    family: str
    parameters: dict[str, Any]
    score: np.ndarray
    risk_scale: float = 1.0


def _rank(frame: pd.DataFrame, column: str) -> np.ndarray:
    ranked = frame.groupby("trade_date", observed=True)[column].rank(pct=True, method="average")
    return (ranked.to_numpy(dtype=float) * 2.0 - 1.0)


def _candidate_id(family: str, parameters: dict[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(parameters, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:12]
    return f"{family.lower()}_{digest}"


def _constrained_weights(raw: np.ndarray, shrink: float) -> np.ndarray:
    baseline = np.array([0.60, 0.40, 0.00])
    positive = np.maximum(raw, 0.0)
    estimated = positive / positive.sum() if positive.sum() > 0 else baseline
    weights = shrink * baseline + (1.0 - shrink) * estimated
    lower = np.array([0.35, 0.20, 0.00])
    upper = np.array([0.70, 0.50, 0.30])
    weights = np.clip(weights, lower, upper)
    return weights / weights.sum()


def _daily_rank_ic(frame: pd.DataFrame, factors: list[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for trade_date, group in frame.groupby("trade_date", sort=True, observed=True):
        return_rank = group.return_20d.rank(pct=True)
        row: dict[str, Any] = {"trade_date": trade_date}
        for factor in factors:
            row[factor] = group[factor].corr(return_rank)
        rows.append(row)
    return pd.DataFrame(rows).set_index("trade_date")


def _dynamic_score(
    frame: pd.DataFrame,
    *,
    sentiment_column: str,
    window: int,
    shrink: float,
    method: str,
) -> np.ndarray:
    factors = ["tech_rank", "fund_rank", sentiment_column]
    daily = _daily_rank_ic(frame, factors)
    dates = list(daily.index)
    weights_by_date: dict[Any, np.ndarray] = {}
    for index, trade_date in enumerate(dates):
        end = index - 25
        start = end - window
        if start < 0 or end <= start:
            continue
        history = daily.iloc[start:end]
        means = history.mean().to_numpy(dtype=float)
        stds = history.std(ddof=1).replace(0, np.nan).to_numpy(dtype=float)
        if method == "ROLLING_RANK_IC":
            raw = means
        elif method == "RANK_IC_IR":
            raw = np.divide(means, stds, out=np.zeros_like(means), where=np.isfinite(stds))
        else:
            raw = np.divide(means, 1.0 + np.nan_to_num(stds, nan=1.0))
        weights_by_date[trade_date] = _constrained_weights(raw, shrink)
    output = np.full(len(frame), np.nan)
    for trade_date, indices in frame.groupby("trade_date", sort=False, observed=True).indices.items():
        weights = weights_by_date.get(trade_date)
        if weights is None:
            continue
        values = frame.iloc[indices][factors].to_numpy(dtype=float)
        output[indices] = values @ weights
    return output


def _ridge_score(frame: pd.DataFrame, sentiment_column: str, window: int) -> np.ndarray:
    x = np.column_stack(
        [
            frame.tech_rank,frame.fund_rank,frame[sentiment_column],
            frame.tech_rank * frame[sentiment_column],
            frame.fund_rank * frame[sentiment_column],
            frame.risk_reminder.astype(float),frame.has_event.astype(float),
        ]
    ).astype(float)
    y = frame.groupby("trade_date", observed=True).return_20d.rank(pct=True).to_numpy(dtype=float) * 2 - 1
    dates = sorted(frame.trade_date.unique())
    date_indices = {date: np.flatnonzero(frame.trade_date.to_numpy() == date) for date in dates}
    output = np.full(len(frame), np.nan)
    start = window + 25
    while start < len(dates):
        validation_dates = dates[start : start + 40]
        train_dates = dates[start - 25 - window : start - 25]
        train_index = np.concatenate([date_indices[item] for item in train_dates])
        validation_index = np.concatenate([date_indices[item] for item in validation_dates])
        train_x = x[train_index]
        means = train_x.mean(axis=0)
        stds = train_x.std(axis=0)
        stds[stds == 0] = 1.0
        normalized = (train_x - means) / stds
        design = np.column_stack([np.ones(len(normalized)), normalized])
        penalty = np.eye(design.shape[1])
        penalty[0, 0] = 0.0
        beta = np.linalg.solve(design.T @ design + penalty, design.T @ y[train_index])
        valid_x = (x[validation_index] - means) / stds
        output[validation_index] = np.column_stack([np.ones(len(valid_x)), valid_x]) @ beta
        start += 40
    return output


class StrategyLabRunner:
    def __init__(self, results_db: Path = DEFAULT_RESULTS_DB) -> None:
        self.results_db = results_db.resolve()
        self.protocol = json.loads((ROOT / "strategy_selection_protocol.json").read_text(encoding="utf-8"))

    def _load_frame(self, connection: duckdb.DuckDBPyConnection) -> pd.DataFrame:
        frame = connection.execute(
            """
            SELECT trade_date,symbol,technical_score,fundamental_score,
                   sentiment_zero_score,sentiment_available_score,
                   sentiment_event_gated_score,risk_reminder,has_event,
                   inst_rank,dt_rank,turnover_rank,gap_follow_rank,
                   market_breadth_strength,market_turnover_change,stock_return_pct,
                   is_limit_up,is_limit_down,is_broken_board,return_20d,mae_20d,mfe_20d,
                   entry_date_20d,exit_date_20d
            FROM aligned_historical_packets
            WHERE technical_available AND fundamental_available
              AND status_20d='COMPLETE' AND return_20d IS NOT NULL
            ORDER BY trade_date,symbol
            """
        ).fetchdf()
        counts = frame.groupby("trade_date", observed=True).size()
        valid_dates = counts[counts >= 100].index
        frame = frame[frame.trade_date.isin(valid_dates)].reset_index(drop=True)
        frame["tech_rank"] = _rank(frame, "technical_score")
        frame["fund_rank"] = _rank(frame, "fundamental_score")
        for mode in ("zero", "available", "event_gated"):
            frame[f"sent_{mode}_rank"] = _rank(frame, f"sentiment_{mode}_score")
        return frame

    @staticmethod
    def _regime(frame: pd.DataFrame) -> pd.Series:
        daily = frame.groupby("trade_date", observed=True).agg(
            breadth=("market_breadth_strength", "median"),
            market_return=("stock_return_pct", "mean"),
            limit_balance=("is_limit_up", "mean"),
            limit_down=("is_limit_down", "mean"),
        )
        breadth_reference = daily.breadth.expanding(min_periods=20).median().shift(1)
        risk_on = (daily.breadth > breadth_reference) & (daily.market_return > 0)
        risk_off = (daily.breadth < breadth_reference) & (daily.market_return < 0)
        regime = pd.Series("NEUTRAL", index=daily.index)
        regime[risk_on] = "RISK_ON"
        regime[risk_off] = "RISK_OFF"
        return frame.trade_date.map(regime)

    def _candidates(self, frame: pd.DataFrame) -> list[Candidate]:
        candidates: list[Candidate] = []
        tech = frame.tech_rank.to_numpy()
        fund = frame.fund_rank.to_numpy()
        sentiments = {
            "ZERO": frame.sent_zero_rank.to_numpy(),
            "AVAILABLE_RENORMALIZE": frame.sent_available_rank.to_numpy(),
            "EVENT_GATED": frame.sent_event_gated_rank.to_numpy(),
        }
        params = {"tech": 0.60, "fund": 0.40, "sentiment": 0.0}
        candidates.append(Candidate(_candidate_id("LEGACY", params), "LEGACY", params, 0.6 * tech + 0.4 * fund))

        grid = self.protocol["candidate_families"]["static"]
        for mode, sent in sentiments.items():
            for sent_weight in grid["sentiment_weights"]:
                for fund_weight in grid["fundamental_weights"]:
                    tech_weight = 1.0 - sent_weight - fund_weight
                    if tech_weight < grid["technical_minimum"]:
                        continue
                    params = {"mode": mode,"tech": tech_weight,"fund": fund_weight,"sentiment": sent_weight}
                    score = tech_weight * tech + fund_weight * fund + sent_weight * sent
                    candidates.append(Candidate(_candidate_id("STATIC", params), "STATIC", params, score))

        risk_grid = self.protocol["candidate_families"]["risk_overlay"]
        risk = frame.risk_reminder.to_numpy(dtype=float)
        legacy = 0.6 * tech + 0.4 * fund
        for penalty in risk_grid["risk_penalties"]:
            for scale in risk_grid["position_scales"]:
                params = {"penalty": penalty,"risk_scale": scale}
                candidates.append(Candidate(
                    _candidate_id("RISK_OVERLAY", params),"RISK_OVERLAY",params,
                    legacy - penalty * risk,risk_scale=scale,
                ))

        dynamic = self.protocol["candidate_families"]["dynamic"]
        for mode, _ in sentiments.items():
            sent_column = f"sent_{mode.lower().replace('renormalize','available').replace('available_available','available')}_rank"
            if mode == "AVAILABLE_RENORMALIZE":
                sent_column = "sent_available_rank"
            for method in dynamic["methods"][:3]:
                for window in self.protocol["walk_forward"]["train_windows_trading_days"]:
                    for shrink in dynamic["shrinkage_to_legacy"]:
                        params = {"mode": mode,"method": method,"window": window,"shrink": shrink}
                        score = _dynamic_score(
                            frame,sentiment_column=sent_column,window=window,shrink=shrink,method=method,
                        )
                        candidate = Candidate(_candidate_id("DYNAMIC", params),"DYNAMIC",params,score)
                        candidates.append(candidate)
                        if method == "RANK_IC_IR" and shrink == 0.5:
                            overlay_params = {**params,"penalty": 0.2,"risk_scale": 0.5}
                            candidates.append(Candidate(
                                _candidate_id("DYNAMIC_RISK", overlay_params),"DYNAMIC_RISK",overlay_params,
                                score - 0.2 * risk,risk_scale=0.5,
                            ))

        frame["regime"] = self._regime(frame)
        regime_profiles = (
            {"RISK_ON": (0.55, 0.25, 0.20),"NEUTRAL": (0.60, 0.35, 0.05),"RISK_OFF": (0.40, 0.50, 0.10)},
            {"RISK_ON": (0.50, 0.25, 0.25),"NEUTRAL": (0.55, 0.35, 0.10),"RISK_OFF": (0.35, 0.50, 0.15)},
            {"RISK_ON": (0.60, 0.25, 0.15),"NEUTRAL": (0.60, 0.40, 0.00),"RISK_OFF": (0.45, 0.45, 0.10)},
        )
        for profile_index, profile in enumerate(regime_profiles, start=1):
            for mode, sent in sentiments.items():
                score = np.full(len(frame), np.nan)
                for regime, weights in profile.items():
                    mask = frame.regime.to_numpy() == regime
                    score[mask] = weights[0] * tech[mask] + weights[1] * fund[mask] + weights[2] * sent[mask]
                params = {"profile": profile_index,"mode": mode,"weights": profile}
                candidates.append(Candidate(
                    _candidate_id("REGIME", params),"REGIME",params,score - 0.2 * risk,risk_scale=0.5,
                ))

        interactions = (("TECH_SENT", tech), ("FUND_SENT", fund))
        for mode, sent in sentiments.items():
            for name, base in interactions:
                interaction = base * sent
                for strength in (0.10, 0.20):
                    params = {"mode": mode,"interaction": name,"strength": strength}
                    score = legacy + 0.10 * sent + strength * interaction
                    candidates.append(Candidate(_candidate_id("INTERACTION", params),"INTERACTION",params,score))

        market_components = np.column_stack(
            [frame.turnover_rank.to_numpy(), frame.gap_follow_rank.to_numpy()]
        )
        market_counts = np.sum(np.isfinite(market_components), axis=1)
        market_average = np.divide(
            np.nansum(market_components, axis=1),
            market_counts,
            out=np.full(len(frame), np.nan),
            where=market_counts > 0,
        )
        dragon_components = np.column_stack(
            [frame.inst_rank.to_numpy(), frame.dt_rank.to_numpy()]
        )
        dragon_counts = np.sum(np.isfinite(dragon_components), axis=1)
        dragon_average = np.divide(
            np.nansum(dragon_components, axis=1),
            dragon_counts,
            out=np.zeros(len(frame)),
            where=dragon_counts > 0,
        )
        components = {
            "FULL_AVAILABLE": frame.sent_available_rank.to_numpy(),
            "NO_DRAGON": market_average,
            "TURNOVER_ONLY": frame.turnover_rank.fillna(0.0).to_numpy(),
            "GAP_ONLY": frame.gap_follow_rank.fillna(0.0).to_numpy(),
            "DRAGON_EVENT_ONLY": dragon_average,
        }
        for component, values in components.items():
            params = {"component": component,"tech": 0.35,"fund": 0.40,"sentiment": 0.25}
            candidates.append(Candidate(
                _candidate_id("ABLATION", params),"ABLATION",params,
                0.35 * tech + 0.40 * fund + 0.25 * values,
            ))

        for mode in sentiments:
            sent_column = "sent_available_rank" if mode == "AVAILABLE_RENORMALIZE" else f"sent_{mode.lower()}_rank"
            for window in self.protocol["walk_forward"]["train_windows_trading_days"]:
                params = {"mode": mode,"window": window,"alpha": 1.0,"model": "RIDGE_CLOSED_FORM"}
                score = _ridge_score(frame, sent_column, window)
                candidates.append(Candidate(_candidate_id("ML_RIDGE", params),"ML_RIDGE",params,score))
        return candidates

    @staticmethod
    def _create_tables(connection: duckdb.DuckDBPyConnection) -> None:
        for table in (
            "candidate_definitions","candidate_metrics","candidate_daily_diagnostics",
            "candidate_portfolio_periods","candidate_period_metrics","walk_forward_fold_metrics",
        ):
            connection.execute(f"DROP TABLE IF EXISTS {table}")

    @staticmethod
    def _write_frame(connection: duckdb.DuckDBPyConnection, table: str, frame: pd.DataFrame) -> None:
        connection.register("write_frame", frame)
        connection.execute(f"CREATE TABLE {table} AS SELECT * FROM write_frame")
        connection.unregister("write_frame")

    @staticmethod
    def _family_best(metrics: pd.DataFrame, definitions: pd.DataFrame) -> pd.DataFrame:
        primary = metrics[metrics.cost_bp == PRIMARY_COST_BP].merge(definitions[["candidate_id", "family"]], on="candidate_id")
        primary["selection_score"] = primary.sharpe.fillna(-999) + 0.25 * primary.rank_ic.fillna(0)
        return primary.sort_values("selection_score", ascending=False).groupby("family", observed=True).head(1)

    def _fold_metrics(self, frame: pd.DataFrame, candidates: list[Candidate], best_ids: set[str]) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        dates = sorted(frame.trade_date.unique())
        for candidate in candidates:
            if candidate.candidate_id not in best_ids:
                continue
            for window in self.protocol["walk_forward"]["train_windows_trading_days"]:
                start = window + 25
                fold = 0
                while start < len(dates):
                    validation = set(dates[start : start + 40])
                    mask = frame.trade_date.isin(validation).to_numpy()
                    if mask.sum() == 0:
                        break
                    metrics, _, _ = evaluate_candidate(
                        frame.loc[mask].reset_index(drop=True),candidate_id=candidate.candidate_id,
                        score=candidate.score[mask],risk_scale=candidate.risk_scale,
                        cost_scenarios=(PRIMARY_COST_BP,),
                    )
                    if metrics:
                        rows.append({
                            "candidate_id": candidate.candidate_id,"train_window_days": window,
                            "fold": fold,"validation_start": min(validation),"validation_end": max(validation),
                            **metrics[0],
                        })
                    start += 40
                    fold += 1
        return pd.DataFrame(rows)

    def run(self) -> dict[str, Any]:
        connection = duckdb.connect(str(self.results_db))
        try:
            frame = self._load_frame(connection)
            candidates = self._candidates(frame)
            definitions = pd.DataFrame([
                {"candidate_id": item.candidate_id,"family": item.family,
                 "parameters_json": json.dumps(item.parameters, ensure_ascii=False, sort_keys=True),
                 "research_only": True,"creates_trade": False}
                for item in candidates
            ])
            metric_rows: list[dict[str, Any]] = []
            daily_frames: list[pd.DataFrame] = []
            portfolio_frames: list[pd.DataFrame] = []
            period_rows: list[dict[str, Any]] = []
            for candidate in candidates:
                metrics, daily, portfolios = evaluate_candidate(
                    frame,candidate_id=candidate.candidate_id,score=candidate.score,
                    risk_scale=candidate.risk_scale,
                )
                metric_rows.extend(metrics)
                if not daily.empty:
                    daily_frames.append(daily)
                if not portfolios.empty:
                    portfolio_frames.append(portfolios)
                    period_rows.extend(period_metrics(portfolios, candidate.candidate_id))
            metrics_frame = pd.DataFrame(metric_rows)
            daily_frame = pd.concat(daily_frames, ignore_index=True)
            portfolios_frame = pd.concat(portfolio_frames, ignore_index=True)
            periods_frame = pd.DataFrame(period_rows)
            best = self._family_best(metrics_frame, definitions)
            fold_frame = self._fold_metrics(frame, candidates, set(best.candidate_id))
            self._create_tables(connection)
            self._write_frame(connection, "candidate_definitions", definitions)
            self._write_frame(connection, "candidate_metrics", metrics_frame)
            self._write_frame(connection, "candidate_daily_diagnostics", daily_frame)
            self._write_frame(connection, "candidate_portfolio_periods", portfolios_frame)
            self._write_frame(connection, "candidate_period_metrics", periods_frame)
            self._write_frame(connection, "walk_forward_fold_metrics", fold_frame)
            connection.execute("CHECKPOINT")
            return {
                "run_at": datetime.now().astimezone().isoformat(),"candidate_count": len(candidates),
                "formal_observations": len(frame),"formal_dates": int(frame.trade_date.nunique()),
                "best_by_family": best.replace({np.nan: None}).to_dict(orient="records"),
            }
        finally:
            connection.close()


__all__ = ["StrategyLabRunner"]
