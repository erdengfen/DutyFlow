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
    invalid_keys: list[str]

    def message(self) -> str:
        """返回适合 CLI 或测试展示的校验信息。"""
        if self.ok:
            return "env config valid"
        messages: list[str] = []
        if self.missing_keys:
            messages.append("missing required env keys: " + ", ".join(self.missing_keys))
        if self.invalid_keys:
            messages.append("invalid env values: " + ", ".join(self.invalid_keys))
        return "; ".join(messages)


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
    feishu_event_mode: str
    feishu_tenant_key: str
    feishu_owner_open_id: str
    feishu_owner_report_chat_id: str
    feishu_owner_user_id: str
    feishu_owner_union_id: str
    feishu_oauth_redirect_uri: str
    feishu_oauth_default_scopes: list[str]
    feishu_owner_user_access_token: str
    feishu_owner_user_refresh_token: str
    feishu_owner_user_token_expires_at: str
    data_dir: Path
    log_dir: Path
    runtime_env: str
    log_level: str
    permission_mode: str

    def validate(self) -> EnvValidationResult:
        """兼容现有模型调用链，只校验模型运行必需字段。"""
        return self.validate_model()

    def validate_model(self) -> EnvValidationResult:
        """校验模型配置是否已具备真实链路运行条件。"""
        required = {
            "DUTYFLOW_MODEL_API_KEY": self.model_api_key,
            "DUTYFLOW_MODEL_BASE_URL": self.model_base_url,
            "DUTYFLOW_MODEL_NAME": self.model_name,
        }
        missing = [key for key, value in required.items() if not value]
        return EnvValidationResult(
            ok=not missing,
            missing_keys=missing,
            invalid_keys=[],
        )

    def validate_feishu_ingress(self) -> EnvValidationResult:
        """按 Step 5 接入模式校验飞书接入所需字段。"""
        invalid = self._validate_feishu_event_mode()
        missing = self._missing_feishu_keys_for_mode()
        return EnvValidationResult(
            ok=not missing and not invalid,
            missing_keys=missing,
            invalid_keys=invalid,
        )

    def _validate_feishu_event_mode(self) -> list[str]:
        """限制飞书事件模式只使用当前规划允许的取值。"""
        if self.feishu_event_mode in {"fixture", "long_connection"}:
            return []
        return ["DUTYFLOW_FEISHU_EVENT_MODE"]

    def _missing_feishu_keys_for_mode(self) -> list[str]:
        """根据事件接入模式返回缺失的飞书字段。"""
        if self.feishu_event_mode != "long_connection":
            return []
        required = {
            "DUTYFLOW_FEISHU_APP_ID": self.feishu_app_id,
            "DUTYFLOW_FEISHU_APP_SECRET": self.feishu_app_secret,
            "DUTYFLOW_FEISHU_TENANT_KEY": self.feishu_tenant_key,
            "DUTYFLOW_FEISHU_OWNER_OPEN_ID": self.feishu_owner_open_id,
            "DUTYFLOW_FEISHU_OWNER_REPORT_CHAT_ID": self.feishu_owner_report_chat_id,
        }
        return [key for key, value in required.items() if not value]


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
        feishu_event_mode=merged.get("DUTYFLOW_FEISHU_EVENT_MODE", "fixture"),
        feishu_tenant_key=merged.get("DUTYFLOW_FEISHU_TENANT_KEY", ""),
        feishu_owner_open_id=merged.get("DUTYFLOW_FEISHU_OWNER_OPEN_ID", ""),
        feishu_owner_report_chat_id=merged.get("DUTYFLOW_FEISHU_OWNER_REPORT_CHAT_ID", ""),
        feishu_owner_user_id=merged.get("DUTYFLOW_FEISHU_OWNER_USER_ID", ""),
        feishu_owner_union_id=merged.get("DUTYFLOW_FEISHU_OWNER_UNION_ID", ""),
        feishu_oauth_redirect_uri=merged.get("DUTYFLOW_FEISHU_OAUTH_REDIRECT_URI", ""),
        feishu_oauth_default_scopes=_split_comma_separated_values(
            merged.get("DUTYFLOW_FEISHU_OAUTH_DEFAULT_SCOPES", "")
        ),
        feishu_owner_user_access_token=merged.get(
            "DUTYFLOW_FEISHU_OWNER_USER_ACCESS_TOKEN",
            "",
        ),
        feishu_owner_user_refresh_token=merged.get(
            "DUTYFLOW_FEISHU_OWNER_USER_REFRESH_TOKEN",
            "",
        ),
        feishu_owner_user_token_expires_at=merged.get(
            "DUTYFLOW_FEISHU_OWNER_USER_TOKEN_EXPIRES_AT",
            "",
        ),
        data_dir=data_dir,
        log_dir=log_dir,
        runtime_env=merged.get("DUTYFLOW_RUNTIME_ENV", "local-demo"),
        log_level=merged.get("DUTYFLOW_LOG_LEVEL", "INFO"),
        permission_mode=merged.get("DUTYFLOW_PERMISSION_MODE", "default"),
    )


def validate_env_config(config: EnvConfig) -> EnvValidationResult:
    """兼容现有模型链路，返回模型配置校验结果。"""
    return config.validate()


def validate_feishu_ingress_config(config: EnvConfig) -> EnvValidationResult:
    """返回 Step 5 飞书接入所需字段的分层校验结果。"""
    return config.validate_feishu_ingress()


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


def _split_comma_separated_values(value: str) -> list[str]:
    """把逗号分隔的环境变量解析为稳定字符串列表。"""
    if not value.strip():
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _self_test() -> None:
    """验证缺失配置和逗号分隔字段能返回稳定结果。"""
    config = load_env_config(Path.cwd())
    result = validate_env_config(config)
    assert isinstance(result.missing_keys, list)
    assert isinstance(config.feishu_oauth_default_scopes, list)


if __name__ == "__main__":
    _self_test()
    print("dutyflow env config self-test passed")
