"""固定住「学习样例第 3 部分该不该跑」的判定规则。

为什么值得单独立一个测试文件：这段代码是全项目**唯一**会真的花 token 的地方，
它的开闭判定一旦写错，后果有两个方向，都很糟：

* 该跳过的时候跑了 —— 悄悄烧钱，而且没人知道；
* 该跑的时候跳过 —— 输出「未验证」，于是你以为自己没配 Key，去查一个不存在的问题。

真实犯过的错正是后者：判定读的是 ``os.environ``，而 Key 写在 ``.env`` 里，
于是同一个 Key，冒烟脚本读得到、学习样例读不到，被报成「未配置」。
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from conftest import REPO_ROOT


def _patch_settings(monkeypatch, **overrides):
    """把配置单例换成一份指定的配置，不碰真实 .env、不联网。"""
    import app.config as app_config
    from app.config import Settings

    monkeypatch.setattr(
        app_config,
        "_settings",
        Settings(_env_file=None, **overrides),  # type: ignore[call-arg]
        raising=False,
    )


def test_gate_reports_skipped_when_key_present_but_provider_offline(learning_demo, monkeypatch):
    """Key 配了、但 provider 仍是 offline → 必须是「跳过」，且不能发起网络调用。

    「跳过」和「未验证」是两件事：前者是「你不让我跑」，后者是「我没法跑」。
    混在一起会让人以为 Key 没配上（真踩过）。
    """
    _patch_settings(monkeypatch, deepseek_api_key="sk-fake-for-test", model_provider="offline")

    out = learning_demo.demo_prebuilt_agent_optional()

    assert out["create_agent_smoke"] == "skipped_provider_offline"


def test_gate_reports_unverified_without_key(learning_demo, monkeypatch):
    """真的没有 Key 时，才报「未验证」。"""
    _patch_settings(monkeypatch, deepseek_api_key="", model_provider="deepseek")

    out = learning_demo.demo_prebuilt_agent_optional()

    assert out["create_agent_smoke"] == "unverified_no_api_key"


def test_gate_still_reads_key_from_env_file_not_os_environ(learning_demo, monkeypatch):
    """判定必须走 Settings（会读 .env），不能直接读 os.environ。

    这条是防回退的：如果哪天有人把它改回 ``os.environ.get(...)``，
    Key 只写在 .env 里时就会被判成「未配置」——正是已经犯过一次的错。
    这里给一个「.env 想象中存在」的配置，且 os.environ 里**没有**这个变量，
    期望结果是「跳过」而不是「未验证」。
    """
    monkeypatch.delenv("SHOPLENS_DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("SHOPLENS_MODEL_PROVIDER", raising=False)
    _patch_settings(monkeypatch, deepseek_api_key="sk-from-dotenv-only", model_provider="offline")

    out = learning_demo.demo_prebuilt_agent_optional()

    assert out["create_agent_smoke"] == "skipped_provider_offline", (
        "判定绕过了 Settings、去读了 os.environ —— 那样 .env 里的 Key 会被忽略"
    )


@pytest.mark.slow
def test_script_is_runnable_from_repo_root():
    """`python scripts/learning_demo.py` 必须能独立跑起来。

    真实踩过的坑：Python 只把**脚本所在目录**（scripts/）放进 sys.path，
    不是当前工作目录。所以脚本里 `from app.config import ...` 会 ModuleNotFoundError。
    pytest 靠 pyproject.toml 的 pythonpath 绕过了这个问题，脚本必须自己解决 ——
    而「你自己敲的命令能跑」比「测试能跑」更重要。
    """
    result = subprocess.run(
        [sys.executable, "scripts/learning_demo.py"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert result.returncode == 0, result.stderr[-2000:]
    assert "ModuleNotFoundError" not in result.stderr
