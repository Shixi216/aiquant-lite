from trading.experiments.core_timing_diagnostics import (
    bucket_performance, delay_distribution, outcome_groups, return_summary,
)


def test_return_summary_uses_real_gain_loss_ratio():
    result = return_summary([.10, .20, -.05, -.10])
    assert result["sample_count"] == 4
    assert result["win_rate"] == .5
    assert abs(result["profit_loss_ratio"] - 2.0) < 1e-12


def test_outcome_groups_use_twenty_day_return_and_ten_percent_tails():
    rows = [{"return_20": value / 100} for value in range(-10, 10)]
    result = outcome_groups(rows)
    assert len(result["top_10pct"]) == 2
    assert len(result["bottom_10pct"]) == 2
    assert result["top_10pct"][-1]["return_20"] == .09
    assert result["bottom_10pct"][0]["return_20"] == -.10


def test_delay_distribution_reports_requested_buckets():
    rows = [{"delay": value} for value in (0, 1, 2, 3, 4, 5, 8)]
    result = delay_distribution(rows, "delay")
    assert result["sample_count"] == 7
    assert result["median"] == 3
    assert result["proportions"]["5_plus"] == 2 / 7


def test_bucket_performance_keeps_horizons_separate():
    rows = [{"kind": "a", "return_5": .1, "return_10": -.1,
             "return_20": .2, "maximum_adverse_excursion": -.03}]
    result = bucket_performance(rows, lambda row: row["kind"])
    assert result["a"]["returns"]["5"]["average_return"] == .1
    assert result["a"]["returns"]["10"]["average_return"] == -.1
