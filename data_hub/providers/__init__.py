"""数据提供者包（延迟加载优化）

用 PEP 562 模块级 __getattr__ 惰性加载 Provider，避免 import 本包时
触发 akshare（~1.9s）等重型依赖加载。仅当真正访问对应类时才导入。
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "AKShareProvider",
    "BaoStockProvider",
    "TushareProvider",
    "AKShareBatchProvider",
    "BaoStockBatchProvider",
    "ProviderBatchResult",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "AKShareProvider": ("data_hub.providers.akshare_provider", "AKShareProvider"),
    "BaoStockProvider": ("data_hub.providers.baostock_provider", "BaoStockProvider"),
    "TushareProvider": ("data_hub.providers.tushare_provider", "TushareProvider"),
    "AKShareBatchProvider": ("data_hub.providers.full_market", "AKShareBatchProvider"),
    "BaoStockBatchProvider": ("data_hub.providers.full_market", "BaoStockBatchProvider"),
    "ProviderBatchResult": ("data_hub.providers.full_market", "ProviderBatchResult"),
}


def __getattr__(name: str) -> Any:
    """惰性加载：首次访问时才 import 具体 Provider 模块"""
    if name in _LAZY_IMPORTS:
        module_name, attr_name = _LAZY_IMPORTS[name]
        import importlib
        module = importlib.import_module(module_name)
        attr = getattr(module, attr_name)
        # 缓存到模块命名空间（后续访问直接命中）
        globals()[name] = attr
        return attr
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return list(__all__)
