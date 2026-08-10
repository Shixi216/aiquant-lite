# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)


project_root = Path(SPECPATH).resolve().parents[1]
entrypoint = project_root / "desktop" / "entrypoint.py"
hiddenimports = []
for package in (
    "config",
    "database.migrations",
    "data_hub",
    "desktop",
    "manual_tracking",
    "mcp_servers",
    "router",
    "trading",
):
    hiddenimports.extend(collect_submodules(package))
hiddenimports.extend(
    [
        "pytz",
        "scripts.run_router_api",
        "scripts.run_data_api",
    ]
)
hiddenimports.extend(collect_submodules("pymupdf"))

pymupdf_binaries = collect_dynamic_libs("pymupdf")

datas = [
    (str(project_root / "pyproject.toml"), "."),
    (str(project_root / ".env.example"), "."),
    (str(project_root / "README.md"), "."),
    (str(project_root / "README.zh-CN.md"), "."),
    (
        str(project_root / "build" / "manifests" / "build-manifest.json"),
        ".",
    ),
    (str(project_root / "docs" / "delivery"), "docs/delivery"),
]
datas.extend(
    (str(path), "database/migrations")
    for path in sorted((project_root / "database" / "migrations").glob("*.py"))
)
datas.extend(collect_data_files("akshare"))

analysis = Analysis(
    [str(entrypoint)],
    pathex=[str(project_root)],
    binaries=pymupdf_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "pytest_asyncio", "ruff"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)

desktop_exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="HermesOPC",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
)

service_exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="hermes-opc-service",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=True,
)

bundle = COLLECT(
    desktop_exe,
    service_exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="HermesOPC",
)
