# 本文件负责统一收口面向用户的飞书回馈出口，不向模型暴露自由发信能力。

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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


def _self_test() -> None:
    """验证状态文本生成逻辑。"""
    text = _build_status_text("处理中", "已进入后台任务")
    assert text == "【处理中】\n已进入后台任务"


if __name__ == "__main__":
    _self_test()
    print("dutyflow feedback gateway self-test passed")
