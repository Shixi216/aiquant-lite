from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
from time import perf_counter
from zoneinfo import ZoneInfo

from config.settings import settings
from trading.research.sentiment.v3.capital import (
    CapitalRawData,
    CapitalStockRow,
    DragonTigerEvent,
    DragonTigerRawData,
    MarginRawData,
    MarginRow,
)
from trading.research.sentiment.v3.market_providers import sanitize_error
from trading.research.sentiment.v3.schemas import QualityFlag

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
INSTITUTION_SEAT = "\u673a\u6784\u4e13\u7528"


def _number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if result != result else result


def _amount(value, multiplier: float) -> float | None:
    number = _number(value)
    return None if number is None else number * multiplier


def _net(row, buy: str, sell: str, multiplier: float = 1.0) -> float | None:
    buy_value = _amount(row.get(buy), multiplier)
    sell_value = _amount(row.get(sell), multiplier)
    return buy_value - sell_value if buy_value is not None and sell_value is not None else None


def _sanitize(exc: Exception) -> str:
    return f"{type(exc).__name__}: {sanitize_error(exc)[:120]}"


class _TushareBase:
    @staticmethod
    def _client():
        if not settings.tushare_token or not settings.tushare_token.strip():
            raise RuntimeError("Tushare token is not configured")
        import tushare as ts

        return ts.pro_api(settings.tushare_token.strip())

    @staticmethod
    def _timed(fn, **kwargs):
        started = perf_counter()
        value = fn(**kwargs)
        return value, perf_counter() - started

    @staticmethod
    def _calendar_dates(frame, key: str) -> tuple[str | None, str | None]:
        dates = sorted(str(value) for value in frame.get("cal_date", []).tolist())
        previous = [value for value in dates if value < key]
        following = [value for value in dates if value > key]
        return (previous[-1] if previous else None, following[0] if following else None)


class TushareCapitalSource(_TushareBase):
    """Four batch endpoints. Tushare moneyflow buckets remain vendor-derived."""

    name = "Tushare daily/daily_basic/moneyflow EOD"

    def fetch(self, trade_date: date) -> CapitalRawData:
        pro = self._client()
        key = trade_date.strftime("%Y%m%d")
        start = (trade_date - timedelta(days=14)).strftime("%Y%m%d")
        flags: set[QualityFlag] = {QualityFlag.EOD_ONLY, QualityFlag.AVAILABLE_TIME_INFERRED}
        timings = {}
        errors = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                "daily": pool.submit(self._timed, pro.daily, trade_date=key),
                "daily_basic": pool.submit(
                    self._timed,
                    pro.daily_basic,
                    trade_date=key,
                    fields="ts_code,trade_date,turnover_rate",
                ),
                "moneyflow": pool.submit(self._timed, pro.moneyflow, trade_date=key),
                "calendar": pool.submit(
                    self._timed,
                    pro.trade_cal,
                    exchange="",
                    start_date=start,
                    end_date=key,
                    is_open="1",
                ),
            }
            values = {}
            for name, future in futures.items():
                try:
                    values[name], timings[name] = future.result()
                except Exception as exc:
                    values[name] = None
                    errors.append(f"{name}:{_sanitize(exc)}")
        daily = values.get("daily")
        source_timestamp = datetime.combine(trade_date, time(15, 0), SHANGHAI_TZ)
        available_time = datetime.combine(trade_date, time(18, 0), SHANGHAI_TZ)
        if daily is None:
            return CapitalRawData(
                source=self.name,
                source_timestamp=source_timestamp,
                available_time=available_time,
                rows=None,
                quality_flags=(
                    QualityFlag.SOURCE_UNAVAILABLE,
                    QualityFlag.MISSING_CAPITAL_DATA,
                    QualityFlag.EOD_ONLY,
                ),
                field_provenance={"error": " | ".join(errors)},
                request_seconds=timings,
            )
        calendar = values.get("calendar")
        previous_daily = None
        if calendar is not None:
            previous_key, _ = self._calendar_dates(calendar, key)
            if previous_key:
                try:
                    previous_daily, timings["previous_daily"] = self._timed(
                        pro.daily, trade_date=previous_key
                    )
                except Exception as exc:
                    errors.append(f"previous_daily:{_sanitize(exc)}")
        if previous_daily is None:
            flags.add(QualityFlag.MISSING_PREVIOUS_QUOTE)
        basic = values.get("daily_basic")
        moneyflow = values.get("moneyflow")
        if basic is None:
            flags.add(QualityFlag.PARTIAL_DATA)
        if moneyflow is None:
            flags.update({QualityFlag.PARTIAL_DATA, QualityFlag.MISSING_CAPITAL_DATA})
        basic_map = (
            {}
            if basic is None
            else {str(row.ts_code): row for row in basic.itertuples(index=False)}
        )
        money_map = (
            {}
            if moneyflow is None
            else {str(row.ts_code): row._asdict() for row in moneyflow.itertuples(index=False)}
        )
        previous_map = (
            {}
            if previous_daily is None
            else {str(row.ts_code): row for row in previous_daily.itertuples(index=False)}
        )
        rows = []
        for row in daily.itertuples(index=False):
            symbol = str(row.ts_code)
            m = money_map.get(symbol)
            b = basic_map.get(symbol)
            prior = previous_map.get(symbol)
            rows.append(
                CapitalStockRow(
                    symbol=symbol,
                    turnover=_amount(row.amount, 1000.0),
                    previous_turnover=_amount(prior.amount, 1000.0) if prior is not None else None,
                    turnover_rate=(
                        _number(b.turnover_rate) / 100.0
                        if b is not None and _number(b.turnover_rate) is not None
                        else None
                    ),
                    vendor_small_net_flow=_net(m, "buy_sm_amount", "sell_sm_amount", 10000.0)
                    if m
                    else None,
                    vendor_medium_net_flow=_net(m, "buy_md_amount", "sell_md_amount", 10000.0)
                    if m
                    else None,
                    vendor_large_net_flow=_net(m, "buy_lg_amount", "sell_lg_amount", 10000.0)
                    if m
                    else None,
                    vendor_super_large_net_flow=_net(
                        m, "buy_elg_amount", "sell_elg_amount", 10000.0
                    )
                    if m
                    else None,
                    vendor_net_flow=_amount(m.get("net_mf_amount"), 10000.0) if m else None,
                )
            )
        buy_fields = ("buy_sm_amount", "buy_md_amount", "buy_lg_amount", "buy_elg_amount")
        sell_fields = ("sell_sm_amount", "sell_md_amount", "sell_lg_amount", "sell_elg_amount")
        total_buy = (
            sum(
                _amount(row.get(name), 10000.0) or 0.0
                for row in money_map.values()
                for name in buy_fields
            )
            if money_map
            else None
        )
        total_sell = (
            sum(
                _amount(row.get(name), 10000.0) or 0.0
                for row in money_map.values()
                for name in sell_fields
            )
            if money_map
            else None
        )
        provenance = {
            "market_turnover": "Tushare daily.amount normalized THOUSAND_CNY->CNY",
            "turnover_rate": "Tushare daily_basic.turnover_rate normalized PERCENT->DECIMAL",
            "vendor_flow": "Tushare moneyflow amount buckets normalized TEN_THOUSAND_CNY->CNY",
        }
        if errors:
            provenance["errors"] = " | ".join(errors)
        return CapitalRawData(
            source=self.name,
            source_timestamp=source_timestamp,
            available_time=available_time,
            rows=rows,
            vendor_total_buy_amount=total_buy,
            vendor_total_sell_amount=total_sell,
            quality_flags=tuple(sorted(flags, key=str)),
            field_provenance=provenance,
            request_seconds=timings,
        )


class TushareMarginSource(_TushareBase):
    """Batch margin_detail plus exchange totals; values are already CNY/shares."""

    name = "Tushare margin/margin_detail EOD"

    @staticmethod
    def _rows(frame) -> list[MarginRow]:
        if frame is None:
            return []
        return [
            MarginRow(
                symbol=str(row.ts_code),
                margin_balance=_number(row.rzye),
                margin_buy=_number(row.rzmre),
                margin_repay=_number(row.rzche),
                short_balance=_number(row.rqye),
                short_sell=_number(row.rqmcl),
                short_repay=_number(row.rqchl),
                total_margin_balance=_number(row.rzrqye),
            )
            for row in frame.itertuples(index=False)
        ]

    def fetch(self, trade_date: date) -> MarginRawData:
        pro = self._client()
        key = trade_date.strftime("%Y%m%d")
        start = (trade_date - timedelta(days=14)).strftime("%Y%m%d")
        end = (trade_date + timedelta(days=14)).strftime("%Y%m%d")
        timings = {}
        flags = {
            QualityFlag.EOD_ONLY,
            QualityFlag.NEXT_SESSION_AVAILABLE,
            QualityFlag.AVAILABLE_TIME_INFERRED,
        }
        calendar, timings["calendar"] = self._timed(
            pro.trade_cal, exchange="", start_date=start, end_date=end, is_open="1"
        )
        previous_key, next_key = self._calendar_dates(calendar, key)
        source_timestamp = datetime.combine(trade_date, time(15, 0), SHANGHAI_TZ)
        next_date = (
            datetime.strptime(next_key, "%Y%m%d").date()
            if next_key
            else trade_date + timedelta(days=1)
        )
        available_time = datetime.combine(next_date, time(9, 0), SHANGHAI_TZ)
        try:
            current, timings["margin_detail"] = self._timed(pro.margin_detail, trade_date=key)
        except Exception:
            return MarginRawData(
                source=self.name,
                source_timestamp=source_timestamp,
                available_time=available_time,
                rows=None,
                previous_rows=None,
                market_margin_total=None,
                quality_flags=(
                    QualityFlag.SOURCE_UNAVAILABLE,
                    QualityFlag.MISSING_MARGIN_DATA,
                    QualityFlag.EOD_ONLY,
                    QualityFlag.NEXT_SESSION_AVAILABLE,
                ),
                request_seconds=timings,
            )
        previous = None
        if previous_key:
            try:
                previous, timings["previous_margin_detail"] = self._timed(
                    pro.margin_detail, trade_date=previous_key
                )
            except Exception:
                flags.add(QualityFlag.PARTIAL_DATA)
        market_total = None
        try:
            market, timings["margin"] = self._timed(pro.margin, trade_date=key)
            values = [_number(value) for value in market["rzrqye"].tolist()]
            market_total = sum(value for value in values if value is not None) if values else None
        except Exception:
            flags.add(QualityFlag.PARTIAL_DATA)
        return MarginRawData(
            source=self.name,
            source_timestamp=source_timestamp,
            available_time=available_time,
            rows=self._rows(current),
            previous_rows=self._rows(previous),
            market_margin_total=market_total,
            quality_flags=tuple(sorted(flags, key=str)),
            request_seconds=timings,
        )


class TushareDragonTigerSource(_TushareBase):
    """One top_list and one top_inst call; no seat identity inference."""

    name = "Tushare top_list/top_inst EOD event"

    def fetch(self, trade_date: date) -> DragonTigerRawData:
        pro = self._client()
        key = trade_date.strftime("%Y%m%d")
        timings = {}
        flags = {QualityFlag.EOD_ONLY, QualityFlag.AVAILABLE_TIME_INFERRED}
        source_timestamp = datetime.combine(trade_date, time(15, 0), SHANGHAI_TZ)
        available_time = datetime.combine(trade_date, time(18, 0), SHANGHAI_TZ)
        try:
            top_list, timings["top_list"] = self._timed(pro.top_list, trade_date=key)
        except Exception:
            return DragonTigerRawData(
                source=self.name,
                source_timestamp=source_timestamp,
                available_time=available_time,
                provider_succeeded=False,
                events=[],
                quality_flags=(
                    QualityFlag.SOURCE_UNAVAILABLE,
                    QualityFlag.MISSING_DRAGON_TIGER_DATA,
                    QualityFlag.EOD_ONLY,
                ),
                request_seconds=timings,
            )
        try:
            top_inst, timings["top_inst"] = self._timed(pro.top_inst, trade_date=key)
        except Exception:
            top_inst = None
            flags.add(QualityFlag.PARTIAL_DATA)
        events = []
        for symbol, listed in top_list.groupby("ts_code", sort=True):
            first = listed.iloc[0]
            seats = (
                None
                if top_inst is None
                else top_inst[top_inst["ts_code"].astype(str) == str(symbol)]
            )
            if seats is None:
                seat_count = None
                inst_count = None
                inst_buy = None
                inst_sell = None
                buyers = ()
                sellers = ()
            else:
                seat_count = len(seats)
                institutions = seats[
                    seats["exalter"]
                    .astype(str)
                    .str.contains(INSTITUTION_SEAT, regex=False, na=False)
                ]
                inst_count = len(institutions)
                inst_buy = float(institutions["buy"].fillna(0).sum())
                inst_sell = float(institutions["sell"].fillna(0).sum())
                buyers = tuple(
                    seats.sort_values("buy", ascending=False)["exalter"].astype(str).head(5)
                )
                sellers = tuple(
                    seats.sort_values("sell", ascending=False)["exalter"].astype(str).head(5)
                )
            events.append(
                DragonTigerEvent(
                    symbol=str(symbol),
                    reasons=tuple(sorted(set(listed["reason"].dropna().astype(str)))),
                    buy_amount=_number(first.get("l_buy")),
                    sell_amount=_number(first.get("l_sell")),
                    net_amount=_number(first.get("net_amount")),
                    institution_buy_amount=inst_buy,
                    institution_sell_amount=inst_sell,
                    institution_net_amount=(
                        inst_buy - inst_sell
                        if inst_buy is not None and inst_sell is not None
                        else None
                    ),
                    seat_count=seat_count,
                    institution_seat_count=inst_count,
                    top_buyers=buyers,
                    top_sellers=sellers,
                )
            )
        return DragonTigerRawData(
            source=self.name,
            source_timestamp=source_timestamp,
            available_time=available_time,
            provider_succeeded=True,
            events=events,
            quality_flags=tuple(sorted(flags, key=str)),
            request_seconds=timings,
        )
