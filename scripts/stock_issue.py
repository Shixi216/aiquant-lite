# ruff: noqa: E402
# Standalone CLI bootstrap configures paths or runtime before project imports.
# -*- coding: utf-8 -*-
"""
股票增发查询工具（正式工具，2026-09-17 固化）
用法: .venv/Scripts/python.exe scripts/stock_issue.py <代码如600354.SH> [--limit 5]
功能: 调 Tushare stk_seasoned(股票增发) 查该股增发明细，输出格式化(进程/类别/募集/目的/发行价)。
      融入个股分析的事件面/政策面——增发是重要事件: 定增预案/稀释股本/募资投向/审批不确定性。
      权限: 需 Tushare 2000 积分(以数据源当前授权为准)。数据源直连 api.tushare.pro 防劫持。
"""
import os
import sys
import argparse
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
os.environ['NO_PROXY'] = '*'
import tushare as ts
import tushare.pro.client as _tc
_orig = _tc.DataApi.__init__
def _p(self, *a, **kw):
    _orig(self, *a, **kw)
    self._DataApi__http_url = 'https://api.tushare.pro/dataapi'
_tc.DataApi.__init__ = _p

def _ts_token() -> str:
    """2026-09-28 S2修复：token改读settings/.env，不再硬编码。"""
    import os
    import sys
    tok = os.environ.get("TUSHARE_TOKEN", "")
    if not tok:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from config.settings import settings
            tok = (settings.tushare_token or "").strip()
        except Exception:
            tok = ""
    if not tok:
        raise RuntimeError("TUSHARE_TOKEN 未配置（环境变量或 .env）")
    return tok



# 展示用关键字段(中文名)
FIELDS = [
    ('ann_date', '公告日'), ('first_ann_date', '首次公告日'),
    ('fo_type', '类别'), ('cur_stage', '进程'),
    ('fo_raise_total', '预计募资(元)'), ('fo_raise_total_act', '实际募资(元)'),
    ('fo_purpose', '目的'), ('pricing_method', '定价方式'),
    ('fo_price_high', '预计发行价上限'), ('fo_price_low', '预计发行价下限'),
    ('fo_price_act', '发行价'), ('fo_vol_act', '发行量(股)'),
    ('plan_chg_type', '方案变动'), ('plan_chg_ann_dt', '变动公告日'),
    ('apply_date', '申购日'), ('new_share_list_dt', '新增上市日'),
    ('fo_investor', '发行对象'), ('issue_obj_type', '发行对象类型'),
    ('pe_issue', '增发PE'), ('fo_exp_total', '发行费用(元)'),
]


def _fmt_num(v):
    """数字转友好格式: 金额显示为亿/万"""
    if v is None or v == '' or v == 'nan':
        return None
    try:
        f = float(v)
        if abs(f) >= 1e8:
            return f"{f/1e8:.2f}亿"
        if abs(f) >= 1e4:
            return f"{f/1e4:.0f}万"
        return f"{f:g}"
    except (TypeError, ValueError):
        return str(v)


def fetch_issue(ts_code, limit=5):
    """查单只票增发记录，返回 list[dict] 按公告日降序"""
    try:
        pro = ts.pro_api(_ts_token())
        df = pro.stk_seasoned(ts_code=ts_code)
    except Exception as e:
        # 空结果或权限不足
        err = str(e)
        if '权限' in err or '积分' in err or '没有' in err:
            return {'error': f"增发接口权限不足: {err[:80]}"}
        if '不存在' in err or len(err) < 5:
            return {'error': None, 'records': []}
        return {'error': f"stk_seasoned 调用失败: {err[:80]}"}
    if df is None or len(df) == 0:
        return {'error': None, 'records': []}
    df = df.sort_values('ann_date', ascending=False)
    records = []
    for _, row in df.head(limit).iterrows():
        rec = {}
        for col, label in FIELDS:
            v = row.get(col)
            if col.startswith('fo_raise') or col == 'fo_exp_total' or col == 'fo_price_act':
                v = _fmt_num(v)
            elif col in ('fo_vol_act', 'fo_vol_high', 'fo_vol_low'):
                v = _fmt_num(v)
            if v is not None and str(v).strip() not in ('', 'nan', 'None'):
                rec[label] = str(v).strip()
        records.append(rec)
    return {'error': None, 'records': records}


def render(records, name=''):
    """格式化输出，无 * # 符号"""
    if isinstance(records, dict) and records.get('error'):
        return f"⚠️ {records['error']}"
    recs = records.get('records', []) if isinstance(records, dict) else records
    if not recs:
        return f"📄 {name} 近期无增发记录" if name else "📄 近期无增发记录"
    lines = [f"📄 股票增发 ({name} 最近{len(recs)}笔)"]
    for i, r in enumerate(recs, 1):
        lines.append(f"  {i}. " + " | ".join(f"{k}:{v}" for k, v in r.items()))
    return "\n".join(lines)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('code')
    ap.add_argument('--limit', type=int, default=5)
    args = ap.parse_args()
    code = args.code
    if '.' not in code:
        code = code + '.SH' if code.startswith(('6', '9')) else code + '.SZ'
    result = fetch_issue(code, args.limit)
    print(render(result, code))
