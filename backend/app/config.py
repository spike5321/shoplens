"""ShopLens 配置。

设计约束（来自 PROJECT_PLAN.md 第 4 节）：
1. 密钥只从环境变量 / .env 读取，绝不写进源码。返回给接口的一律是布尔值，
   不是密钥本身，也不用 ``SecretStr`` 直接序列化。
2. 数据库凭据分三套，权限递增方向是 reader < app < admin：
   - reader：只读分析视图，给模型 SQL 执行用
   - app   ：读写应用状态表（runs / events / sessions）
   - admin ：建库、建角色、迁移、灌种子数据
   在阶段 0 只做连通性验证，三套角色在阶段 1/2 真正建立。
3. 图运行限制（修复次数、SQL 超时、结果行数上限）是服务端字段，
   放在配置里而不是提示词里，模型无法覆盖。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# .env 在仓库根目录，而不是 backend/ 目录。
# 如果写成相对路径 ".env"，pydantic-settings 会按**进程当前工作目录**去找：
# 从 backend/ 启动 uvicorn 时就会读不到，表现为"配置明明填了却连不上数据库"。
# 所以这里用模块自身位置反推仓库根目录，与启动目录无关。
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = _REPO_ROOT / ".env"


class Settings(BaseSettings):
    """从环境变量读取全部配置。

    环境变量前缀统一为 ``SHOPLENS_``，例如 ``SHOPLENS_PG_HOST`` -> ``pg_host``。
    """

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        env_prefix="SHOPLENS_",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- 运行环境 ----------
    env: str = "local"

    # ---------- 数据库 ----------
    pg_host: str = "127.0.0.1"
    # 刻意不用 5432：避免和机器上可能存在的其它 PostgreSQL 冲突
    pg_port: int = 55432
    pg_db: str = "shoplens"

    pg_admin_user: str = "postgres"
    pg_admin_password: SecretStr = SecretStr("")

    pg_app_user: str = "shoplens_app"
    pg_app_password: SecretStr = SecretStr("")

    pg_reader_user: str = "analytics_reader"
    pg_reader_password: SecretStr = SecretStr("")

    # ---------- 模型服务商 ----------
    # offline = 明确标识的离线假模型，无需任何 Key，可完成全部离线开发与测试
    # deepseek = 一个真实服务商，需要 SHOPLENS_DEEPSEEK_API_KEY
    model_provider: Literal["offline", "deepseek"] = "offline"

    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com/v1"

    # ---------- 图执行硬约束（服务端拥有，模型不可改）----------
    max_repair_count: int = Field(default=1, ge=0, le=1)
    sql_statement_timeout_ms: int = Field(default=3000, gt=0)
    sql_max_rows: int = Field(default=200, gt=0)

    # ---------- 演示参考日期 ----------
    # 「今天 / 上周」这类相对时间必须用固定演示日期解释，
    # 不允许悄悄使用机器当前时间（PROJECT_PLAN.md 第 3 节）。
    demo_reference_date: str = "2026-09-30"
    business_timezone: str = "Asia/Shanghai"

    # ---------- 派生属性 ----------
    @property
    def deepseek_api_key_configured(self) -> bool:
        """是否配置了真实 Key。只返回布尔值，避免任何密钥泄露路径。"""
        return bool(self.deepseek_api_key.get_secret_value().strip())

    def _build_dsn(self, user: str, password: SecretStr, *, db: str | None = None) -> str:
        pwd = quote_plus(password.get_secret_value())
        return f"postgresql://{user}:{pwd}@{self.pg_host}:{self.pg_port}/{db or self.pg_db}"

    def dsn(self, role: Literal["admin", "app", "reader"]) -> str:
        """返回带密码的连接串。只在建连接时使用，不要写日志。"""
        match role:
            case "admin":
                return self._build_dsn(self.pg_admin_user, self.pg_admin_password)
            case "app":
                return self._build_dsn(self.pg_app_user, self.pg_app_password)
            case "reader":
                return self._build_dsn(self.pg_reader_user, self.pg_reader_password)
            case _:  # pragma: no cover - Literal 已限制取值
                raise ValueError(f"unknown role: {role}")

    def safe_dsn(self, role: Literal["admin", "app", "reader"]) -> str:
        """去掉密码的连接串，可以安全地写日志或返回给前端。"""
        match role:
            case "admin":
                user = self.pg_admin_user
            case "app":
                user = self.pg_app_user
            case "reader":
                user = self.pg_reader_user
            case _:  # pragma: no cover
                raise ValueError(f"unknown role: {role}")
        return f"postgresql://{user}:***@{self.pg_host}:{self.pg_port}/{self.pg_db}"


_settings: Settings | None = None


def get_settings() -> Settings:
    """进程内单例，避免每次请求都重新解析 .env。"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
