"""Configure Hermes to use aiquant-lite's local Finance Data MCP server.

The command is dry-run by default. Pass ``--apply`` to create backups and
update the Hermes MCP config, WeCom home channel, and the selected cron job.
No credential or channel value is printed.
"""

from __future__ import annotations

# ruff: noqa: E402

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.utf8 import UTF8_ERRORS, utf8_child_environment

SERVER_NAME = "finance_data"
MANAGED_START = "  # BEGIN AIQUANT-LITE MANAGED: finance_data MCP"
MANAGED_END = "  # END AIQUANT-LITE MANAGED: finance_data MCP"
TOOL_NAMES = (
    "get_stock_basic",
    "get_market_snapshot",
    "get_realtime_quote",
    "get_daily_bars",
    "get_financial_statement",
    "list_announcements",
    "search_finance_news",
    "verify_market_fact",
    "parse_market_scanner_query",
    "scan_a_share_market",
    "research_candidates",
    "create_explicit_decision",
    "get_experiment_report",
)

DAILY_REPORT_PROMPT = """生成 {date} 的自选股收盘日报。

自选股：新易盛(300502)、中际旭创(300308)、工业富联(601138)、胜宏科技(300476)、
宁德时代(300750)、隆基绿能(601012)、双星新材(002585)、华工科技(000988)、
阳光电源(300274)、东方财富(300059)。

数据规则：
1. 行情、K线、财务、公告和财经新闻只能使用 finance_data MCP 工具；不要使用
   web_search、Tavily 或凭模型记忆补数。
2. 每个标的先调用 get_realtime_quote；需要历史比较时调用 get_daily_bars。
3. 当日公告调用 list_announcements；当日新闻调用 search_finance_news。媒体报道必须标注
   “未经结构化来源验证”，不得写成已证实事实。
4. 涉及价格、涨跌幅、财务数字等关键结论时，调用 verify_market_fact；证据不足或冲突时
   明确写“未验证”或“来源冲突”，不要猜测。

输出要求：
- 中文 Markdown，先给数据状态和风险提示，再按股票逐项汇总。
- 每项列出收盘/最新价、涨跌幅、重要公告、重要新闻、证据来源与验证状态。
- 末尾给出“需人工复核”清单；不提供买卖建议，不承诺收益。
- 某个数据源失败时继续完成其他标的，并如实列出失败项。
"""


class IntegrationError(RuntimeError):
    """Raised when a safe, deterministic integration update is impossible."""


def _yaml_string(value: str) -> str:
    """Return a JSON string, which is also a valid YAML scalar."""
    return json.dumps(value, ensure_ascii=False)


def _managed_mcp_block(project_root: Path) -> str:
    python_path = project_root / ".venv" / "Scripts" / "python.exe"
    if os.name != "nt":
        python_path = project_root / ".venv" / "bin" / "python"

    lines = [
        MANAGED_START,
        f"  {SERVER_NAME}:",
        f"    command: {_yaml_string(str(python_path))}",
        "    args:",
        '      - "-m"',
        '      - "mcp_servers.finance_data.server"',
        "    env:",
        f"      PYTHONPATH: {_yaml_string(str(project_root))}",
        '      PYTHONUTF8: "1"',
        '      PYTHONIOENCODING: "utf-8:backslashreplace"',
        "    enabled: true",
        "    timeout: 180",
        "    connect_timeout: 60",
        "    supports_parallel_tool_calls: false",
        "    tools:",
        "      include:",
    ]
    lines.extend(f"        - {name}" for name in TOOL_NAMES)
    lines.extend(
        [
            "      resources: false",
            "      prompts: false",
            "    sampling:",
            "      enabled: false",
            MANAGED_END,
        ]
    )
    return "\n".join(lines)


def patch_mcp_config(text: str, project_root: Path) -> str:
    """Add or replace the managed MCP server while preserving other YAML text."""
    project_root = project_root.resolve()
    block = _managed_mcp_block(project_root)

    if MANAGED_START in text or MANAGED_END in text:
        if text.count(MANAGED_START) != 1 or text.count(MANAGED_END) != 1:
            raise IntegrationError("Hermes config contains incomplete managed MCP markers")
        start = text.index(MANAGED_START)
        end = text.index(MANAGED_END, start) + len(MANAGED_END)
        patched = text[:start] + block + text[end:]
    else:
        try:
            parsed = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            raise IntegrationError("Hermes config.yaml is not valid YAML") from exc
        if not isinstance(parsed, dict):
            raise IntegrationError("Hermes config.yaml must contain a YAML mapping")
        mcp_servers = parsed.get("mcp_servers")
        if isinstance(mcp_servers, dict) and SERVER_NAME in mcp_servers:
            raise IntegrationError(
                "An unmanaged finance_data MCP entry already exists; refusing to overwrite it"
            )

        lines = text.splitlines()
        root_index = next(
            (index for index, line in enumerate(lines) if line.rstrip() == "mcp_servers:"),
            None,
        )
        if root_index is None:
            suffix = "" if not text or text.endswith("\n") else "\n"
            patched = f"{text}{suffix}\nmcp_servers:\n{block}\n"
        else:
            lines.insert(root_index + 1, block)
            patched = "\n".join(lines) + ("\n" if text.endswith("\n") else "")

    try:
        verified = yaml.safe_load(patched) or {}
        server = verified["mcp_servers"][SERVER_NAME]
    except (KeyError, TypeError, yaml.YAMLError) as exc:
        raise IntegrationError("Generated Hermes MCP configuration failed validation") from exc
    if tuple(server.get("tools", {}).get("include", [])) != TOOL_NAMES:
        raise IntegrationError("Generated MCP tool allowlist is not exact")
    return patched


def patch_env(text: str, key: str, value: str) -> str:
    """Set one dotenv value without logging it or reformatting unrelated lines."""
    replacement = f"{key}={value}"
    lines = text.splitlines()
    indexes = [
        index
        for index, line in enumerate(lines)
        if line.lstrip().startswith(f"{key}=") and not line.lstrip().startswith("#")
    ]
    if len(indexes) > 1:
        raise IntegrationError(f"Hermes .env contains duplicate {key} entries")
    if indexes:
        lines[indexes[0]] = replacement
    else:
        if lines and lines[-1]:
            lines.append("")
        lines.append(replacement)
    return "\n".join(lines) + "\n"


def discover_wecom_channel(directory_path: Path) -> str:
    """Return the sole WeCom channel ID from Hermes runtime state."""
    try:
        payload = json.loads(directory_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntegrationError("Unable to read Hermes channel_directory.json") from exc

    candidates: list[str] = []
    platform_entries = payload.get("platforms", {}).get("wecom", [])
    if isinstance(platform_entries, list):
        for entry in platform_entries:
            if not isinstance(entry, dict):
                continue
            candidate = entry.get("id")
            if isinstance(candidate, str) and candidate.strip():
                candidates.append(candidate.strip())

    def visit(value: Any, path: tuple[str, ...] = ()) -> None:
        if isinstance(value, dict):
            platform = str(value.get("platform", "")).lower()
            if platform == "wecom":
                for field in ("channel_id", "channel", "target", "id"):
                    candidate = value.get(field)
                    if isinstance(candidate, str) and candidate.strip():
                        candidates.append(candidate.strip())
                        break
            for key, child in value.items():
                if key.lower() == "wecom" and isinstance(child, dict):
                    for candidate in child.keys():
                        if isinstance(candidate, str) and candidate.strip():
                            candidates.append(candidate.strip())
                visit(child, (*path, str(key)))
        elif isinstance(value, list):
            for child in value:
                visit(child, path)

    # Retain support for alternate directory layouts used by older Hermes builds.
    if not candidates:
        visit(payload)
    unique = list(dict.fromkeys(candidates))
    if len(unique) != 1:
        raise IntegrationError(
            f"Expected exactly one WeCom home channel, found {len(unique)}; "
            "pass --wecom-channel explicitly"
        )
    return unique[0]


def build_cron_updates(project_root: Path) -> dict[str, Any]:
    return {
        "deliver": "wecom",
        "enabled_toolsets": [SERVER_NAME],
        "workdir": str(project_root.resolve()),
        "prompt": DAILY_REPORT_PROMPT,
    }


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    ) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _find_hermes_python(hermes_home: Path) -> Path:
    candidates = (
        hermes_home / "hermes-agent" / "venv" / "Scripts" / "python.exe",
        hermes_home / "hermes-agent" / "venv" / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise IntegrationError("Hermes virtual-environment Python was not found")


def _update_cron_job(
    hermes_home: Path, job_name: str, updates: dict[str, Any]
) -> dict[str, Any]:
    python = _find_hermes_python(hermes_home)
    agent_root = hermes_home / "hermes-agent"
    child_code = (
        "import json,sys; "
        "from cron.jobs import resolve_job_ref,update_job; "
        "p=json.loads(sys.stdin.buffer.read().decode('utf-8')); "
        "j=resolve_job_ref(p.pop('job_name')); "
        "assert j is not None, 'cron job was not found'; "
        "r=update_job(j['id'],p); "
        "print(json.dumps({'name':r.get('name'),'deliver':r.get('deliver'),"
        "'enabled_toolsets':r.get('enabled_toolsets'),'workdir':r.get('workdir')}))"
    )
    environment = utf8_child_environment()
    environment["HERMES_HOME"] = str(hermes_home)
    payload = {"job_name": job_name, **updates}
    result = subprocess.run(
        [str(python), "-c", child_code],
        cwd=agent_root,
        env=environment,
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors=UTF8_ERRORS,
        timeout=30,
        check=False,
    )
    if result.returncode:
        error = (result.stderr or result.stdout).strip().splitlines()
        detail = error[-1] if error else "unknown error"
        raise IntegrationError(f"Hermes cron update failed: {detail}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise IntegrationError("Hermes cron update returned invalid JSON") from exc


def _backup_files(paths: list[Path], hermes_home: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root = hermes_home / "backups" / f"aiquant-lite-integration-{timestamp}"
    backup_root.mkdir(parents=True, exist_ok=False)
    for path in paths:
        shutil.copy2(path, backup_root / path.name)
    return backup_root


def configure(args: argparse.Namespace) -> dict[str, Any]:
    hermes_home = args.hermes_home.resolve()
    project_root = args.project_root.resolve()
    config_path = hermes_home / "config.yaml"
    env_path = hermes_home / ".env"
    jobs_path = hermes_home / "cron" / "jobs.json"
    channel_path = hermes_home / "channel_directory.json"

    for required in (config_path, env_path, jobs_path):
        if not required.is_file():
            raise IntegrationError(f"Required Hermes file is missing: {required.name}")
    project_python = _managed_mcp_block(project_root).splitlines()[2].split(": ", 1)[1]
    if not Path(json.loads(project_python)).is_file():
        raise IntegrationError("Project .venv Python is missing; run uv sync --dev first")

    channel = args.wecom_channel or discover_wecom_channel(channel_path)
    config_text = config_path.read_text(encoding="utf-8")
    env_text = env_path.read_text(encoding="utf-8")
    patched_config = patch_mcp_config(config_text, project_root)
    patched_env = patch_env(env_text, "WECOM_HOME_CHANNEL", channel)
    updates = build_cron_updates(project_root)

    summary: dict[str, Any] = {
        "mode": "apply" if args.apply else "dry-run",
        "mcp_server": SERVER_NAME,
        "tool_count": len(TOOL_NAMES),
        "wecom_home_channel": "configured (value hidden)",
        "cron_job": args.job_name,
        "cron_delivery": updates["deliver"],
        "cron_toolsets": updates["enabled_toolsets"],
    }
    if not args.apply:
        return summary

    backup_root = _backup_files([config_path, env_path, jobs_path], hermes_home)
    try:
        _atomic_write(config_path, patched_config)
        _atomic_write(env_path, patched_env)
        summary["cron_result"] = _update_cron_job(hermes_home, args.job_name, updates)
    except Exception:
        shutil.copy2(backup_root / config_path.name, config_path)
        shutil.copy2(backup_root / env_path.name, env_path)
        shutil.copy2(backup_root / jobs_path.name, jobs_path)
        raise
    summary["backup"] = str(backup_root)
    return summary


def parse_args() -> argparse.Namespace:
    default_home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes-home", type=Path, default=default_home)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).parents[1])
    parser.add_argument("--job-name", default="自选股收盘日报")
    parser.add_argument("--wecom-channel", help="Explicit channel value; never printed")
    parser.add_argument("--apply", action="store_true", help="Back up files and apply changes")
    return parser.parse_args()


def main() -> int:
    try:
        print(json.dumps(configure(parse_args()), ensure_ascii=False, indent=2))
    except IntegrationError as exc:
        print(f"Configuration not changed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
