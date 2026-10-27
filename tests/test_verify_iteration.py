"""Explicitly acknowledge exposed frozen cases without weakening the runner's default gate."""

import sys

import pytest

from conftest import load_module_from_path


@pytest.mark.parametrize("acknowledged", [False, True])
def test_verify_forwards_frozen_iteration_only_when_requested(monkeypatch, acknowledged):
    verify = load_module_from_path("verify_iteration_test", "scripts/verify.py")
    commands = []

    def record(name, command, **kwargs):
        commands.append((name, command))
        return verify.Result(name, "PASS")

    monkeypatch.setattr(verify, "run", record)
    monkeypatch.setattr(verify, "postgres_available", lambda: True)
    argv = ["verify.py", "--npm", "npm"]
    if acknowledged:
        argv.append("--frozen-used-for-iteration")
    monkeypatch.setattr(sys, "argv", argv)
    assert verify.main() == 0
    evaluation = next(
        command for name, command in commands if name == "PostgreSQL offline evaluation"
    )
    assert ("--frozen-used-for-iteration" in evaluation) == acknowledged
    assert "--real-model" not in evaluation
