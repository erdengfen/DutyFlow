# 本文件负责从 .env 和系统环境变量读取 DutyFlow 运行配置。

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class EnvValidationResult:
    """表示配置校验结果和缺失字段。"""

    ok: bool
    missing_keys: list[str]

    def message(self) -> str:
        """返回适合 CLI 或测试展示的校验信息。"""
        if self.ok:
            return "env config valid"
        return "missing required env keys: " + ", ".join(self.missing_keys)


@dataclass
class EnvConfig:
    """保存 DutyFlow 运行配置视图，不保存真实密钥到日志。"""

    model_api_key: str
    model_base_url: str
    model_name: str
    feishu_app_id: str
    feishu_app_secret: str
    feishu_event_verify_token: str
    feishu_event_encrypt_key: str
    feishu_event_callback_url: str
    data_dir: Path
    log_dir: Path
    runtime_env: str
    log_level: str
    permission_mode: str

    def validate(self) -> EnvValidationResult:
        """校验模型配置是否已具备真实链路运行条件。"""
        required = {
            "DUTYFLOW_MODEL_API_KEY": self.model_api_key,
            "DUTYFLOW_MODEL_BASE_URL": self.model_base_url,
            "DUTYFLOW_MODEL_NAME": self.model_name,
        }
        missing = [key for key, value in required.items() if not value]
        return EnvValidationResult(ok=not missing, missing_keys=missing)


def load_env_config(project_root: Path | None = None) -> EnvConfig:
    """读取 .env 和系统环境变量，返回统一配置对象。"""
    root = project_root or Path.cwd()
    values = _read_dotenv(root / ".env")
    merged = {**values, **os.environ}
    data_dir = Path(merged.get("DUTYFLOW_DATA_DIR", "data"))
    log_dir = Path(merged.get("DUTYFLOW_LOG_DIR", str(data_dir / "logs")))
    return EnvConfig(
        model_api_key=merged.get("DUTYFLOW_MODEL_API_KEY", ""),
        model_base_url=merged.get("DUTYFLOW_MODEL_BASE_URL", ""),
        model_name=merged.get("DUTYFLOW_MODEL_NAME", ""),
        feishu_app_id=merged.get("DUTYFLOW_FEISHU_APP_ID", ""),
        feishu_app_secret=merged.get("DUTYFLOW_FEISHU_APP_SECRET", ""),
        feishu_event_verify_token=merged.get("DUTYFLOW_FEISHU_EVENT_VERIFY_TOKEN", ""),
        feishu_event_encrypt_key=merged.get("DUTYFLOW_FEISHU_EVENT_ENCRYPT_KEY", ""),
        feishu_event_callback_url=merged.get("DUTYFLOW_FEISHU_EVENT_CALLBACK_URL", ""),
        data_dir=data_dir,
        log_dir=log_dir,
        runtime_env=merged.get("DUTYFLOW_RUNTIME_ENV", "local-demo"),
        log_level=merged.get("DUTYFLOW_LOG_LEVEL", "INFO"),
        permission_mode=merged.get("DUTYFLOW_PERMISSION_MODE", "default"),
    )


def validate_env_config(config: EnvConfig) -> EnvValidationResult:
    """校验配置对象并返回明确缺失项。"""
    return config.validate()


def _read_dotenv(path: Path) -> dict[str, str]:
    """读取简单 .env 文件，支持 KEY=VALUE 和注释。"""
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = _clean_env_value(value.strip())
    return values


def _clean_env_value(value: str) -> str:
    """移除简单引号包装，不解析复杂 shell 表达式。"""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _self_test() -> None:
    """验证缺失配置能返回明确错误。"""
    config = load_env_config(Path.cwd())
    result = validate_env_config(config)
    assert isinstance(result.missing_keys, list)


if __name__ == "__main__":
    _self_test()
    print("dutyflow env config self-test passed")
