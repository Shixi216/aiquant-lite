from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from trading.research.fundamental.analysis import fundamental_signal
from trading.research.fundamental.metrics import (
    FUNDAMENTAL_METRICS_VERSION,
    calculate_fundamental_metrics,
)
from trading.research.fundamental.models import MarketValuationPoint
from trading.research.fundamental.repository import FundamentalRepository
from trading.schemas import FundamentalSnapshot


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MAIN_DB = ROOT / "database" / "hermes_opc.duckdb"
DEFAULT_REPLAY_DB = ROOT / "database" / "sentiment_v3_replay.duckdb"
DEFAULT_RESULTS_DB = ROOT / "Strategy_Lab_Results.duckdb"

TECHNICAL_VERSION = "technical_signal_exact_v2026_09_02"
SENTIMENT_VERSION = "sentiment-v3-s3c-composite-v1"
DATASET_VERSION = "strategy-lab-pit-dataset-v1"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _finite_ema(values: np.ndarray, period: int, window: int = 60) -> np.ndarray:
    """Match technical.analysis._ema(values[-60:], period) for every row."""
    output = np.full(values.size, np.nan, dtype=np.float64)
    if values.size < window:
        return output
    alpha = 2.0 / (period + 1.0)
    beta = 1.0 - alpha
    chronological = np.empty(window, dtype=np.float64)
    chronological[0] = beta ** (window - 1)
    chronological[1:] = alpha * beta ** np.arange(window - 2, -1, -1)
    output[window - 1 :] = np.convolve(
        values, chronological[::-1], mode="valid"
    )
    return output


def _technical_group(frame: pd.DataFrame, market_regime: str | None = None) -> pd.DataFrame:
    """向量化复刻 technical_signal（技术分）。

    market_regime：市况（RISING/SIDEWAYS/FALLING/UNKNOWN/None）。
      - None/UNKNOWN：无差别过热惩罚（历史默认行为，回测 PIT 基线）。
      - RISING：过热惩罚减半（0.5×）——与生产 technical_signal 一致
        （乖离惩罚 + RSI>70 惩罚均乘 penalty_scale）。
      - SIDEWAYS/FALLING：全额惩罚。
    """
    from trading.research.technical.analysis import _rsi

    penalty_scale = 0.5 if market_regime == "RISING" else 1.0

    closes = frame["close"].to_numpy(dtype=np.float64)
    highs = frame["high"].to_numpy(dtype=np.float64) if "high" in frame.columns else closes
    volumes = frame["volume"].to_numpy(dtype=np.float64)
    count = closes.size
    score = np.full(count, np.nan, dtype=np.float64)
    rsi14 = np.full(count, np.nan, dtype=np.float64)
    deviation = np.full(count, np.nan, dtype=np.float64)
    sma20 = np.full(count, np.nan, dtype=np.float64)
    sma60 = np.full(count, np.nan, dtype=np.float64)
    ema12 = _finite_ema(closes, 12)
    ema26 = _finite_ema(closes, 26)
    if count >= 60:
        cumulative = np.concatenate(([0.0], np.cumsum(closes)))
        indices = np.arange(59, count)
        sma60[indices] = (cumulative[indices + 1] - cumulative[indices - 59]) / 60.0
        sma20[indices] = (cumulative[indices + 1] - cumulative[indices - 19]) / 20.0

        # 逐个60根窗口复刻 production Wilder RSI，避免回测口径漂移。
        rsi = np.array(
            [_rsi(closes[row_index - 59 : row_index + 1].tolist()) for row_index in indices],
            dtype=np.float64,
        )
        rsi14[indices] = rsi
        dev = sma20[indices] / sma60[indices] - 1.0
        deviation[indices] = dev
        trend = np.clip(dev * 12.0, -1.0, 1.0)
        # 乖离>20%：每多10个百分点 trend 减 0.5×penalty_scale（与生产 122 行一致）
        trend = np.where(dev > 0.20, np.maximum(-1.0, trend - (dev - 0.20) * 5.0 * penalty_scale), trend)
        sma5 = (cumulative[indices + 1] - cumulative[indices - 4]) / 5.0
        sma10 = (cumulative[indices + 1] - cumulative[indices - 9]) / 10.0
        short_pressure = (
            (closes[indices] < sma5)
            & (closes[indices] < sma10)
            & (sma5 > sma20[indices])
        )
        trend = np.where(short_pressure, np.minimum(trend, -0.3), trend)
        momentum = np.clip((ema12[indices] / ema26[indices] - 1.0) * 25.0, -1.0, 1.0)
        for output_index, row_index in enumerate(indices):
            window_start = row_index - 9
            recent_high = float(np.max(highs[window_start : row_index + 1]))
            drawdown = (recent_high - closes[row_index]) / recent_high if recent_high else 0.0
            recent_closes = closes[window_start : row_index + 1]
            limit_down = bool(np.any(recent_closes[1:] / recent_closes[:-1] - 1.0 <= -0.095))
            if drawdown >= 0.15 and limit_down:
                momentum[output_index] = min(momentum[output_index], -0.5)
        rsi_score = np.clip((50.0 - np.abs(rsi - 50.0)) / 50.0, -1.0, 1.0)
        # RSI>70 惩罚：-0.5×penalty_scale（与生产 160 行一致，RISING 市减半）
        rsi_score = np.where(rsi > 70.0, -0.5 * penalty_scale, np.where(rsi < 25.0, 0.25, rsi_score))
        score[indices] = np.clip(trend * 0.5 + momentum * 0.35 + rsi_score * 0.15, -1.0, 1.0)

    return pd.DataFrame(
        {
            "symbol": frame["symbol"].to_numpy(),
            "feature_trade_date": frame["trade_date"].to_numpy(),
            "technical_score": score,
            "technical_confidence": np.where(np.isnan(score), np.nan, 0.67),
            "sma20": sma20,
            "sma60": sma60,
            "ema12": ema12,
            "ema26": ema26,
            "rsi14": rsi14,
            "deviation": deviation,
            "technical_available": ~np.isnan(score),
            "overbought_risk": rsi14 > 70.0,
            "zero_volume_risk": volumes == 0.0,
            "technical_algorithm_version": TECHNICAL_VERSION,
        }
    )


@dataclass(frozen=True)
class DatasetBuildResult:
    results_db: str
    packet_count: int
    technical_available: int
    fundamental_available: int
    sentiment_available: int
    registry_content_hash: str
    source_hashes: dict[str, str]
    safety_before: dict[str, int]


class StrategyLabDatasetBuilder:
    """Create immutable PIT packets; never imports or invokes trading execution code."""

    def __init__(
        self,
        *,
        main_db: Path = DEFAULT_MAIN_DB,
        replay_db: Path = DEFAULT_REPLAY_DB,
        results_db: Path = DEFAULT_RESULTS_DB,
    ) -> None:
        self.main_db = main_db.resolve()
        self.replay_db = replay_db.resolve()
        self.results_db = results_db.resolve()

    def safety_counts(self) -> dict[str, int]:
        connection = duckdb.connect(str(self.main_db), read_only=True)
        try:
            tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
            targets = (
                "trading_orders",
                "trading_fills",
                "real_positions",
                "broker_orders",
                "execution_requests",
            )
            return {
                table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
                for table in targets
                if table in tables
            }
        finally:
            connection.close()

    def _initialize(self) -> duckdb.DuckDBPyConnection:
        if self.results_db.exists():
            raise FileExistsError(
                f"refusing to overwrite existing research database: {self.results_db}"
            )
        connection = duckdb.connect(str(self.results_db))
        connection.execute(f"ATTACH '{self.replay_db.as_posix()}' AS replay (READ_ONLY)")
        connection.execute(f"ATTACH '{self.main_db.as_posix()}' AS main_db (READ_ONLY)")
        connection.execute(
            """
            CREATE TABLE strategy_lab_metadata(
                key VARCHAR PRIMARY KEY,
                value_json JSON NOT NULL
            )
            """
        )
        return connection

    @staticmethod
    def _create_source_packets(connection: duckdb.DuckDBPyConnection) -> None:
        connection.execute(
            """
            CREATE TABLE source_observations AS
            SELECT
                p.packet_id,p.symbol,p.trade_date,p.feature_trade_date,
                p.decision_cutoff,p.cutoff_policy_version,p.record_source,
                p.registry_version,p.registry_content_hash,p.research_set_hash,
                p.universe_id,p.universe_version,p.quality_status,
                CAST(p.quality_flags_json AS VARCHAR) quality_flags_json,
                p.feature_lineage_hash,
                p.capital__dragon_tiger__has_dragon_tiger has_dragon_tiger,
                p.capital__dragon_tiger__institution_net_amount institution_net_amount,
                p.capital__dragon_tiger__net_amount dragon_tiger_net_amount,
                p.capital__stock__turnover stock_turnover,
                p.stock__gap_and_follow_through gap_and_follow_through,
                p.stock__return_pct stock_return_pct,
                p.stock__volume_ratio volume_ratio,
                p.stock__is_limit_up is_limit_up,
                p.stock__is_limit_down is_limit_down,
                p.stock__is_broken_board is_broken_board,
                p.market__breadth_strength market_breadth_strength,
                p.market__market_turnover_change market_turnover_change,
                l.exec_1d__adjusted_return return_1d,
                l.exec_1d__status status_1d,
                l.exec_5d__adjusted_return return_5d,
                l.exec_5d__status status_5d,
                l.exec_10d__adjusted_return return_10d,
                l.exec_10d__status status_10d,
                l.exec_20d__adjusted_return return_20d,
                l.exec_20d__mae mae_20d,
                l.exec_20d__mfe mfe_20d,
                l.exec_20d__entry_date entry_date_20d,
                l.exec_20d__exit_date exit_date_20d,
                l.exec_20d__status status_20d,
                l.label_spec_set_hash,
                l.return_algorithm_version,
                l.price_adjustment_method
            FROM replay.replay_feature_packets p
            JOIN replay.replay_labels_wide l USING(packet_id)
            WHERE p.record_source='REPLAY'
              AND CAST(p.quality_flags_json AS VARCHAR) NOT LIKE '%LOW_COVERAGE%'
            """
        )
        connection.execute("CREATE INDEX source_packet_idx ON source_observations(packet_id)")
        connection.execute("CREATE INDEX source_symbol_date_idx ON source_observations(symbol,trade_date)")

    @staticmethod
    def _create_sentiment_packets(connection: duckdb.DuckDBPyConnection) -> None:
        connection.execute(
            f"""
            CREATE TABLE sentiment_historical_packets AS
            WITH ranked AS (
                SELECT *,
                    CASE WHEN institution_net_amount IS NOT NULL THEN
                      (2.0*(RANK() OVER(PARTITION BY trade_date ORDER BY institution_net_amount NULLS LAST)-1)
                       /NULLIF(COUNT(institution_net_amount) OVER(PARTITION BY trade_date)-1,0)-1.0)
                    END inst_rank,
                    CASE WHEN dragon_tiger_net_amount IS NOT NULL THEN
                      (2.0*(RANK() OVER(PARTITION BY trade_date ORDER BY dragon_tiger_net_amount NULLS LAST)-1)
                       /NULLIF(COUNT(dragon_tiger_net_amount) OVER(PARTITION BY trade_date)-1,0)-1.0)
                    END dt_rank,
                    CASE WHEN stock_turnover IS NOT NULL THEN -1.0*
                      (2.0*(RANK() OVER(PARTITION BY trade_date ORDER BY stock_turnover NULLS LAST)-1)
                       /NULLIF(COUNT(stock_turnover) OVER(PARTITION BY trade_date)-1,0)-1.0)
                    END turnover_rank,
                    CASE WHEN gap_and_follow_through IS NOT NULL THEN -1.0*
                      (2.0*(RANK() OVER(PARTITION BY trade_date ORDER BY gap_and_follow_through NULLS LAST)-1)
                       /NULLIF(COUNT(gap_and_follow_through) OVER(PARTITION BY trade_date)-1,0)-1.0)
                    END gap_follow_rank
                FROM source_observations
            ), scored AS (
                SELECT *,
                    ((inst_rank IS NOT NULL)::INTEGER+(dt_rank IS NOT NULL)::INTEGER+
                     (turnover_rank IS NOT NULL)::INTEGER+(gap_follow_rank IS NOT NULL)::INTEGER) observed_count,
                    ((turnover_rank IS NOT NULL)::INTEGER+(gap_follow_rank IS NOT NULL)::INTEGER) market_observed_count
                FROM ranked
            )
            SELECT
                packet_id,symbol,trade_date,decision_cutoff,has_dragon_tiger,
                institution_net_amount,dragon_tiger_net_amount,stock_turnover,
                gap_and_follow_through,inst_rank,dt_rank,turnover_rank,gap_follow_rank,
                observed_count,observed_count/4.0 research_confidence,
                (COALESCE(inst_rank,0)+COALESCE(dt_rank,0)+COALESCE(turnover_rank,0)+COALESCE(gap_follow_rank,0))/4.0
                    sentiment_zero_score,
                CASE WHEN observed_count>=2 THEN
                    (COALESCE(inst_rank,0)+COALESCE(dt_rank,0)+COALESCE(turnover_rank,0)+COALESCE(gap_follow_rank,0))/observed_count
                END sentiment_available_score,
                CASE WHEN has_dragon_tiger IS TRUE AND observed_count>=2 THEN
                    (COALESCE(inst_rank,0)+COALESCE(dt_rank,0)+COALESCE(turnover_rank,0)+COALESCE(gap_follow_rank,0))/observed_count
                WHEN market_observed_count>=1 THEN
                    (COALESCE(turnover_rank,0)+COALESCE(gap_follow_rank,0))/market_observed_count
                END sentiment_event_gated_score,
                CASE WHEN has_dragon_tiger IS TRUE
                          AND (COALESCE(institution_net_amount,0)<0 OR COALESCE(dragon_tiger_net_amount,0)<0)
                          AND turnover_rank<0 AND gap_follow_rank>0
                     THEN TRUE ELSE FALSE END risk_reminder,
                CASE WHEN has_dragon_tiger IS TRUE THEN TRUE ELSE FALSE END has_event,
                '{SENTIMENT_VERSION}' sentiment_algorithm_version,
                TRUE research_only,FALSE creates_trade
            FROM scored
            """
        )
        connection.execute("CREATE INDEX sentiment_packet_idx ON sentiment_historical_packets(packet_id)")

    @staticmethod
    def _build_technical_frame(connection: duckdb.DuckDBPyConnection) -> pd.DataFrame:
        bars = connection.execute(
            """
            SELECT symbol,trade_date,high,close,volume
            FROM replay.replay_price_bars
            WHERE close IS NOT NULL
            ORDER BY symbol,trade_date
            """
        ).fetchdf()
        frames = []
        for _, group in bars.groupby("symbol", sort=False, observed=True):
            frames.append(_technical_group(group.reset_index(drop=True)))
        return pd.concat(frames, ignore_index=True)

    @staticmethod
    def _create_technical_packets(
        connection: duckdb.DuckDBPyConnection, technical: pd.DataFrame
    ) -> None:
        connection.register("technical_frame", technical)
        connection.execute(
            """
            CREATE TABLE technical_historical_packets AS
            SELECT p.packet_id,p.symbol,p.trade_date,p.feature_trade_date,p.decision_cutoff,
                   t.technical_score,t.technical_confidence,t.sma20,t.sma60,t.ema12,t.ema26,
                   t.rsi14,t.deviation,COALESCE(t.technical_available,FALSE) technical_available,
                   COALESCE(t.overbought_risk,FALSE) overbought_risk,
                   COALESCE(t.zero_volume_risk,FALSE) zero_volume_risk,
                   t.technical_algorithm_version,TRUE research_only,FALSE creates_trade
            FROM source_observations p
            LEFT JOIN technical_frame t
              ON t.symbol=p.symbol AND t.feature_trade_date=p.feature_trade_date
            """
        )
        connection.unregister("technical_frame")
        connection.execute("CREATE INDEX technical_packet_idx ON technical_historical_packets(packet_id)")

    @staticmethod
    def _record_rows(connection: duckdb.DuckDBPyConnection) -> list[tuple[Any, ...]]:
        return connection.execute(
            """
            SELECT canonical_record_id, symbol, data_type, report_period,
                   announcement_time,data_available_time,event_time,primary_source,
                   verification_status,source_record_ids_json,confidence,statement_type,
                   statement_version,revision_of_record_id,accounting_scope,period_type,
                   source_type,disclosure_time_source,payload_json
            FROM main_db.canonical_financial_records
            WHERE data_available_time IS NOT NULL
              AND verification_status <> 'CONFLICT'
            ORDER BY symbol,data_available_time,event_time,canonical_record_id
            """
        ).fetchall()

    @staticmethod
    def _fundamental_score(records: list[Any], close: float | None, symbol: str, cutoff: datetime) -> dict[str, Any]:
        valuation = None
        if close is not None and math.isfinite(close) and close > 0:
            valuation = MarketValuationPoint(
                canonical_record_id=f"replay-price:{symbol}:{cutoff.date().isoformat()}",
                source_record_ids=[],event_time=cutoff,close=float(close),
            )
        metrics, missing, flags, selected = calculate_fundamental_metrics(
            records=records, valuation_point=valuation
        )
        snapshot = FundamentalSnapshot(
            as_of=cutoff.date(),pe_ttm=metrics.pe_ttm,pb=metrics.pb,roe=metrics.roe,
            revenue_growth=metrics.revenue_growth,net_profit_growth=metrics.net_profit_growth,
            debt_ratio=metrics.debt_ratio,operating_cash_flow=metrics.operating_cash_flow,
            operating_cash_flow_positive=metrics.operating_cash_flow_positive,peg=metrics.peg,
            report_period=metrics.report_period,statement_types=metrics.statement_types,
            data_available_time=max(
                (item.data_available_time for item in selected if item.data_available_time is not None),
                default=None,
            ),
            primary_source=selected[0].primary_source if selected else None,
            verification_status="MIXED" if selected else None,source_type="CANONICAL" if selected else None,
            evidence_refs=[item.canonical_record_id for item in selected],
        )
        signal = fundamental_signal(snapshot)
        available = bool(selected) and signal.confidence > 0.2
        return {
            "fundamental_score": signal.score if available else None,
            "fundamental_confidence": signal.confidence if available else None,
            "fundamental_available": available,
            "report_period": metrics.report_period,
            "missing_fields_json": _json(missing),
            "risk_flags_json": _json([flag.value for flag in flags]),
            "selected_record_ids_json": _json([item.canonical_record_id for item in selected]),
            "selected_record_hash": hashlib.sha256(
                "|".join(sorted(item.canonical_record_id for item in selected)).encode()
            ).hexdigest(),
            "fundamental_algorithm_version": FUNDAMENTAL_METRICS_VERSION,
        }

    def _build_fundamental_frame(self, connection: duckdb.DuckDBPyConnection) -> pd.DataFrame:
        grouped_records: dict[str, list[Any]] = defaultdict(list)
        for row in self._record_rows(connection):
            grouped_records[str(row[1])].append(FundamentalRepository._financial_record(row))
        packet_rows = connection.execute(
            """
            SELECT DISTINCT p.symbol,p.trade_date,p.feature_trade_date,p.decision_cutoff,b.close
            FROM source_observations p
            LEFT JOIN replay.replay_price_bars b
              ON b.symbol=p.symbol AND b.trade_date=p.feature_trade_date
            WHERE p.symbol IN (SELECT UNNEST(?::VARCHAR[]))
            ORDER BY p.symbol,p.decision_cutoff
            """,
            [sorted(grouped_records)],
        ).fetchall()
        by_symbol_packets: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
        for row in packet_rows:
            by_symbol_packets[str(row[0])].append(row)
        output: list[dict[str, Any]] = []
        for symbol, packets in by_symbol_packets.items():
            records = grouped_records[symbol]
            eligible: list[Any] = []
            index = 0
            cached: dict[str, Any] | None = None
            for _, trade_date, feature_date, cutoff, close in packets:
                changed = False
                while index < len(records):
                    record = records[index]
                    available = record.data_available_time
                    event = record.event_time
                    if available > cutoff or event > cutoff:
                        break
                    eligible.append(record)
                    index += 1
                    changed = True
                if changed or cached is None:
                    cached = self._fundamental_score(eligible, close, symbol, cutoff)
                output.append(
                    {
                        "symbol": symbol,
                        "trade_date": trade_date,
                        "feature_trade_date": feature_date,
                        "decision_cutoff": cutoff,
                        **cached,
                    }
                )
        return pd.DataFrame(output)

    @staticmethod
    def _create_fundamental_packets(
        connection: duckdb.DuckDBPyConnection, fundamental: pd.DataFrame
    ) -> None:
        connection.register("fundamental_frame", fundamental)
        connection.execute(
            """
            CREATE TABLE fundamental_historical_packets AS
            SELECT p.packet_id,p.symbol,p.trade_date,p.feature_trade_date,p.decision_cutoff,
                   f.fundamental_score,f.fundamental_confidence,
                   COALESCE(f.fundamental_available,FALSE) fundamental_available,
                   f.report_period,f.missing_fields_json,f.risk_flags_json,
                   f.selected_record_ids_json,f.selected_record_hash,
                   f.fundamental_algorithm_version,TRUE research_only,FALSE creates_trade
            FROM source_observations p
            LEFT JOIN fundamental_frame f
              ON f.symbol=p.symbol AND f.trade_date=p.trade_date
             AND f.decision_cutoff=p.decision_cutoff
            """
        )
        connection.unregister("fundamental_frame")
        connection.execute("CREATE INDEX fundamental_packet_idx ON fundamental_historical_packets(packet_id)")

    @staticmethod
    def _create_aligned_view(connection: duckdb.DuckDBPyConnection) -> None:
        connection.execute(
            """
            CREATE VIEW aligned_historical_packets AS
            SELECT s.*,t.technical_score,t.technical_confidence,t.technical_available,
                   f.fundamental_score,f.fundamental_confidence,f.fundamental_available,
                   f.report_period,f.selected_record_hash,
                   v.sentiment_zero_score,v.sentiment_available_score,
                   v.sentiment_event_gated_score,v.research_confidence sentiment_confidence,
                   v.risk_reminder,v.has_event,v.observed_count,
                   v.inst_rank,v.dt_rank,v.turnover_rank,v.gap_follow_rank
            FROM source_observations s
            JOIN technical_historical_packets t USING(packet_id)
            JOIN fundamental_historical_packets f USING(packet_id)
            JOIN sentiment_historical_packets v USING(packet_id)
            """
        )

    def build(self) -> DatasetBuildResult:
        safety_before = self.safety_counts()
        connection = self._initialize()
        try:
            self._create_source_packets(connection)
            self._create_sentiment_packets(connection)
            technical = self._build_technical_frame(connection)
            self._create_technical_packets(connection, technical)
            del technical
            fundamental = self._build_fundamental_frame(connection)
            self._create_fundamental_packets(connection, fundamental)
            del fundamental
            self._create_aligned_view(connection)
            registry = connection.execute(
                "SELECT registry_content_hash,COUNT(*) FROM source_observations GROUP BY 1"
            ).fetchall()
            if len(registry) != 1:
                raise RuntimeError(f"registry drift detected: {registry}")
            counts = connection.execute(
                """
                SELECT COUNT(*),COUNT(*) FILTER(WHERE technical_available),
                       COUNT(*) FILTER(WHERE fundamental_available),
                       COUNT(*) FILTER(WHERE sentiment_available_score IS NOT NULL)
                FROM aligned_historical_packets
                """
            ).fetchone()
            source_hashes = {
                "main_db_sha256": _sha256_file(self.main_db),
                "replay_db_sha256": _sha256_file(self.replay_db),
                "protocol_sha256": _sha256_file(ROOT / "strategy_selection_protocol.json"),
            }
            metadata = {
                "dataset_version": DATASET_VERSION,
                "built_at": datetime.now().astimezone().isoformat(),
                "research_only": True,
                "creates_trade": False,
                "source_hashes": source_hashes,
                "safety_before": safety_before,
                "registry_content_hash": registry[0][0],
                "packet_count": int(counts[0]),
            }
            for key, value in metadata.items():
                connection.execute(
                    "INSERT INTO strategy_lab_metadata VALUES (?,?)", [key, _json(value)]
                )
            connection.execute("CHECKPOINT")
            return DatasetBuildResult(
                results_db=str(self.results_db),packet_count=int(counts[0]),
                technical_available=int(counts[1]),fundamental_available=int(counts[2]),
                sentiment_available=int(counts[3]),registry_content_hash=str(registry[0][0]),
                source_hashes=source_hashes,safety_before=safety_before,
            )
        finally:
            connection.close()


__all__ = ["DatasetBuildResult", "StrategyLabDatasetBuilder", "_technical_group"]
