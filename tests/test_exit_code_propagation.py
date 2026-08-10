"""退出码传播专项测试（收口任务 一）

验证：
- pytest 成功 → 包装器返回 0
- pytest 失败 → 包装器返回 非0
- pytest 异常 → 包装器返回 非0
- subprocess 必须检查并传播 returncode
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_tests.py"


def _run_runner(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RUNNER), *args],
        capture_output=True, text=True, encoding="utf-8",
        errors="backslashreplace", cwd=str(ROOT), timeout=120,
    )


class TestExitCodePropagation:
    def test_success_returns_zero(self):
        """pytest 成功 → 包装器返回 0"""
        # 用一个必然通过的测试
        r = _run_runner("tests/test_trading_calendar.py::TestWeekend::test_sunday_latest_is_friday", "-q")
        assert r.returncode == 0, f"成功测试应返回0，实际{r.returncode}"

    def test_failure_returns_nonzero(self):
        """pytest 失败 → 包装器返回 非0"""
        # 用一个必然失败的测试（断言 False）
        marker = "tests/test_exit_code_probe.py::test_always_fail"
        # 创建临时失败测试
        probe = ROOT / "tests" / "test_exit_code_probe.py"
        probe.write_text(
            "def test_always_fail():\n    assert False, 'probe failure'\n",
            encoding="utf-8",
        )
        try:
            r = _run_runner(marker, "-q")
            assert r.returncode != 0, f"失败测试应返回非0，实际{r.returncode}"
            assert "1" in str(r.returncode) or r.returncode >= 1
        finally:
            probe.unlink(missing_ok=True)

    def test_exception_returns_nonzero(self):
        """pytest 异常（collection error）→ 包装器返回 非0"""
        # 创建一个语法错误/import 错误的测试文件
        probe = ROOT / "tests" / "test_exit_code_probe.py"
        probe.write_text("import nonexistent_module_xyz\n", encoding="utf-8")
        try:
            r = _run_runner("tests/test_exit_code_probe.py", "-q")
            assert r.returncode != 0, f"异常应返回非0，实际{r.returncode}"
        finally:
            probe.unlink(missing_ok=True)

    def test_pytest_rc_recorded(self):
        """输出文件记录 pytest 原始退出码"""
        probe = ROOT / "tests" / "test_exit_code_probe.py"
        probe.write_text(
            "def test_always_fail():\n    assert False, 'probe failure'\n",
            encoding="utf-8",
        )
        out = ROOT / "pytest_rc_report.txt"
        try:
            r = _run_runner("tests/test_exit_code_probe.py", "-q", "--output=./pytest_rc_report.txt")
            assert r.returncode != 0
            assert out.exists()
            content = out.read_text(encoding="utf-8")
            assert "pytest 退出码: 1" in content
        finally:
            probe.unlink(missing_ok=True)
            out.unlink(missing_ok=True)


class TestBashWrapperNoPipeEat:
    """验证 bash 管道不会吞掉 pytest 退出码（使用 PIPESTATUS）"""

    def test_bash_pipestatus_preserves_rc(self):
        """bash 中用 PIPESTATUS 保留 pytest 退出码"""
        probe = ROOT / "tests" / "test_exit_code_probe.py"
        probe.write_text(
            "def test_always_fail():\n    assert False, 'probe failure'\n",
            encoding="utf-8",
        )
        try:
            bash = shutil.which("bash")
            if bash:
                script = (
                    f"cd {ROOT} && "
                    f"{sys.executable} -m pytest tests/test_exit_code_probe.py -q 2>&1 | tail -1; "
                    "rc=${PIPESTATUS[0]}; "
                    "echo \"PIPESTATUS_RC=$rc\""
                )
                command = [bash, "-c", script]
            else:
                powershell = shutil.which("pwsh") or shutil.which("powershell")
                assert powershell is not None, "未找到 bash 或 PowerShell，无法验证管道退出码"
                root = str(ROOT).replace("'", "''")
                python = sys.executable.replace("'", "''")
                script = (
                    f"Set-Location -LiteralPath '{root}'; "
                    f"& '{python}' -m pytest tests/test_exit_code_probe.py -q 2>&1 | "
                    "Select-Object -Last 1; "
                    "$rc = $LASTEXITCODE; "
                    'Write-Output "PIPESTATUS_RC=$rc"'
                )
                command = [powershell, "-NoProfile", "-NonInteractive", "-Command", script]
            r = subprocess.run(
                command, capture_output=True, text=True,
                encoding="utf-8", errors="backslashreplace", timeout=120,
            )
            assert "PIPESTATUS_RC=1" in r.stdout, (
                f"PIPESTATUS 应保留 pytest 退出码1，实际: {r.stdout[-200:]}"
            )
        finally:
            probe.unlink(missing_ok=True)
