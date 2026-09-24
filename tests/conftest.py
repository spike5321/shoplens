"""pytest 公共配置。

两件事：
1. 把 ``backend/`` 加进 ``sys.path``，这样测试里可以直接 ``import app``
   （真正的 pytest 配置在根目录 pyproject.toml 的 ``pythonpath``）。
2. 提供从文件路径加载脚本的辅助函数，让测试能直接验证
   ``scripts/learning_demo.py`` 里**用户真正运行的那份代码**，
   而不是复制一份到测试里各自漂移。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module_from_path(module_name: str, relative_path: str):
    """按文件路径加载一个模块，绕过「scripts 不是包」的问题。"""
    path = REPO_ROOT / relative_path
    if not path.exists():
        raise FileNotFoundError(f"模块文件不存在: {path}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError(f"无法为 {path} 创建加载器")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def learning_demo():
    """阶段 0 的学习样例模块（scripts/learning_demo.py）。"""
    return load_module_from_path("learning_demo", "scripts/learning_demo.py")
