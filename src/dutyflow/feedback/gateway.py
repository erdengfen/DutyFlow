# 本文件负责统一收口面向用户的飞书回馈出口，不向模型暴露自由发信能力。

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import Any, Mapping

if __package__ in {None, ""}:
    _SRC_ROOT = Path(__file__).resolve().parents[2]
    if str(_SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(_SRC_ROOT))

from dutyflow.config.env import EnvConfig
from dutyflow.feishu.client import FeishuClient, FeishuClientResult


@dataclass(frozen=True)
class FeedbackResult:
    """表示一次用户回馈动作的统一结果。"""

    ok: bool
    status: str
    detail: str
    payload: dict[str, Any] = field(default_factory=dict)


class FeedbackGateway:
    """统一封装文本回复和状态回馈的最小出口。"""

    def __init__(
        self,
        config: EnvConfig,
        *,
        client: FeishuClient | None = None,
    ) -> None:
        """绑定配置和底层飞书客户端。"""
        self.config = config
        self.client = client or FeishuClient(config)

    def send_text(self, chat_id: str, text: str) -> FeedbackResult:
        """向指定会话发送一条最小文本消息。"""
        return self._from_client_result(self.client.send_message(chat_id, text))

    def send_owner_text(self, text: str) -> FeedbackResult:
        """向默认 owner 汇报会话发送一条文本消息。"""
        chat_id = self.config.feishu_owner_report_chat_id
        if not chat_id:
            return FeedbackResult(
                ok=False,
                status="missing_owner_chat",
                detail="owner report chat id is not configured",
            )
        return self.send_text(chat_id, text)

    def send_status_update(self, chat_id: str, title: str, summary: str) -> FeedbackResult:
        """向指定会话发送一条稳定格式的状态更新。"""
        body = _build_status_text(title, summary)
        return self.send_text(chat_id, body)

    def send_owner_approval_card(self, approval: Mapping[str, str]) -> FeedbackResult:
        """向默认 owner 会话发送统一样式的审批卡片。"""
        chat_id = self.config.feishu_owner_report_chat_id
        if not chat_id:
            return FeedbackResult(
                ok=False,
                status="missing_owner_chat",
                detail="owner report chat id is not configured",
            )
        return self.send_approval_card(chat_id, approval)

    def send_approval_card(self, chat_id: str, approval: Mapping[str, str]) -> FeedbackResult:
        """向指定会话发送一张统一样式的审批卡片。"""
        return self._from_client_result(
            self.client.send_interactive_card(chat_id, _build_approval_card(approval))
        )

    def _from_client_result(self, result: FeishuClientResult) -> FeedbackResult:
        """把底层飞书客户端结果转换为统一回馈结果。"""
        return FeedbackResult(
            ok=result.ok,
            status=result.status,
            detail=result.detail,
            payload=dict(result.payload),
        )


def _build_status_text(title: str, summary: str) -> str:
    """生成简洁稳定的状态更新文本。"""
    clean_title = title.strip() or "状态更新"
    clean_summary = summary.strip()
    if not clean_summary:
        return f"【{clean_title}】"
    return f"【{clean_title}】\n{clean_summary}"


def _build_approval_card(approval: Mapping[str, str]) -> dict[str, Any]:
    """生成第一版统一审批卡片结构，按钮 value 固定交给系统解析。"""
    approval_id = str(approval.get("approval_id", "")).strip()
    resume_token = str(approval.get("resume_token", "")).strip()
    risk_level = str(approval.get("risk_level", "")).strip() or "unknown"
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange",
            "title": {"tag": "plain_text", "content": "DutyFlow 审批请求"},
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": _build_approval_card_text(approval, risk_level),
                },
            },
            {
                "tag": "action",
                "actions": [
                    _build_approval_button("批准", "primary", approval_id, resume_token, "approved"),
                    _build_approval_button("拒绝", "danger", approval_id, resume_token, "rejected"),
                    _build_approval_button("稍后处理", "default", approval_id, resume_token, "deferred"),
                ],
            },
        ],
    }


def _build_approval_card_text(approval: Mapping[str, str], risk_level: str) -> str:
    """生成审批卡片正文，保持简洁和可追溯。"""
    request = str(approval.get("request", "")).strip() or "需要用户确认后继续。"
    reason = str(approval.get("reason", "")).strip() or "该动作需要人工审批。"
    risk = str(approval.get("risk", "")).strip() or "未提供具体风险说明。"
    task_id = str(approval.get("task_id", "")).strip()
    return (
        f"**任务**：{task_id or '未指定'}\n"
        f"**风险等级**：{risk_level}\n"
        f"**申请内容**：{request}\n"
        f"**原因**：{reason}\n"
        f"**风险**：{risk}"
    )


def _build_approval_button(
    label: str,
    button_type: str,
    approval_id: str,
    resume_token: str,
    decision_result: str,
) -> dict[str, Any]:
    """构造审批按钮，确保所有按钮携带相同恢复字段。"""
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": label},
        "type": button_type,
        "value": {
            "dutyflow_action": "approval_decision",
            "approval_id": approval_id,
            "resume_token": resume_token,
            "decision_result": decision_result,
        },
    }


def _self_test() -> None:
    """验证状态文本生成逻辑。"""
    text = _build_status_text("处理中", "已进入后台任务")
    assert text == "【处理中】\n已进入后台任务"
    card = _build_approval_card({"approval_id": "approval_001", "resume_token": "resume_001"})
    assert card["elements"][1]["actions"][0]["value"]["decision_result"] == "approved"


if __name__ == "__main__":
    _self_test()
    print("dutyflow feedback gateway self-test passed")
