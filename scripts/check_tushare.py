import tushare as ts

from config.settings import settings


if not settings.tushare_token:
    raise RuntimeError("TUSHARE_TOKEN 未配置")

print("正在连接 Tushare……", flush=True)

pro = ts.pro_api(settings.tushare_token.strip())

data = pro.daily(
    ts_code="600172.SH",
    start_date="20260701",
    end_date="20260715",
)

if data.empty:
    raise RuntimeError("Tushare 返回空数据，可能是积分权限、日期范围或接口问题")

latest = data.sort_values("trade_date", ascending=False).iloc[0]

print("Tushare 测试成功")
print(f"记录数：{len(data)}")
print(f"股票代码：{latest['ts_code']}")
print(f"交易日期：{latest['trade_date']}")
print(f"收盘价：{latest['close']}")
