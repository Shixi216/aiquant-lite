"""Run offline test modules in separate processes with disposable databases."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


def run_module(path: Path, output: Path) -> dict:
    label = path.relative_to(ROOT / "tests").with_suffix("").as_posix().replace("/", "_")
    folder = output / label
    folder.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    # Empty environment values also override credentials in a local .env file.
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        key, separator, _ = line.partition("=")
        key = key.strip()
        if separator and re.search(r"API_KEY|TOKEN|SECRET|PASSWORD", key, re.I):
            env[key] = ""
    env.update({
        "OPC_DATABASE_PATH": str(folder / "isolated-main.duckdb"),
        "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(ROOT),
        "QT_QPA_PLATFORM": "offscreen", "HERMES_ENV": "testing", "WEBUI_DEV_FIXTURES": "0",
    })
    log_path = folder / "pytest.log"
    xml_path = folder / "pytest.xml"
    with log_path.open("w", encoding="utf-8") as stream:
        completed = subprocess.run([
            sys.executable, "-m", "pytest", str(path), "-m", "not network and not local_state",
            "--tb=short", "--basetemp", str(folder / "temp"),
            "--junitxml", str(xml_path), "-o", f"cache_dir={folder / 'cache'}",
        ], cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT, check=False)
    result = {"module": path.relative_to(ROOT).as_posix(), "exit": completed.returncode,
              "passed": 0, "failures": 0, "errors": 0, "skipped": 0, "deselected": 0,
              "completed": False}
    try:
        suites = list(ET.parse(xml_path).getroot())
        for key in ("failures", "errors", "skipped"):
            result[key] = sum(int(s.attrib.get(key, 0)) for s in suites)
        total = sum(int(s.attrib.get("tests", 0)) for s in suites)
        result["passed"] = total - sum(result[k] for k in ("failures", "errors", "skipped"))
        result["completed"] = True
    except (OSError, ET.ParseError):
        result["errors"] = 1
    text = log_path.read_text(encoding="utf-8", errors="replace")
    matches = re.findall(r"(\d+) deselected", text)
    result["deselected"] = int(matches[-1]) if matches else 0
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("modules", nargs="*", help="Optional test module paths")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".build-cache/offline-tests")
    args = parser.parse_args()
    if not 1 <= args.workers <= 4:
        parser.error("workers must be between 1 and 4")
    test_root = (ROOT / "tests").resolve()
    paths = sorted({p.resolve() for pattern in ("test_*.py", "*_test.py") for p in test_root.rglob(pattern)})
    if args.modules:
        paths = sorted({(ROOT / name).resolve() for name in args.modules})
    if any(not p.is_file() or p.suffix != ".py" or not p.is_relative_to(test_root) for p in paths):
        parser.error("test modules must be Python files inside tests/")
    if not paths:
        parser.error("no test modules found")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda path: run_module(path, output), paths))
    totals = {key: sum(item[key] for item in results)
              for key in ("passed", "failures", "errors", "skipped", "deselected")}
    (output / "summary.json").write_text(json.dumps({"totals": totals, "modules": results}, indent=2), encoding="utf-8")
    print(json.dumps({"totals": totals}, indent=2), flush=True)
    valid = totals["passed"] > 0 and all(
        item["completed"] and item["exit"] in (0, 5)
        and item["failures"] == 0 and item["errors"] == 0 for item in results
    )
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
