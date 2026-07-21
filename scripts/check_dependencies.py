from importlib.metadata import PackageNotFoundError, version

packages = [
    "fastapi",
    "uvicorn",
    "httpx",
    "pydantic",
    "pydantic-settings",
    "tenacity",
    "PyYAML",
    "pandas",
    "duckdb",
    "akshare",
    "tushare",
    "baostock",
    "pytest",
    "ruff",
]

failed = False

for package in packages:
    try:
        print(f"{package:20} {version(package)}")
    except PackageNotFoundError:
        failed = True
        print(f"{package:20} NOT INSTALLED")
    except Exception as exc:
        failed = True
        print(f"{package:20} ERROR: {exc}")

if failed:
    raise SystemExit(1)

print("\n全部依赖版本检查通过")
