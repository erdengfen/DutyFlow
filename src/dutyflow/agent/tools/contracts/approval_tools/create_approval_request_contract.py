# 本文件定义 create_approval_request 工具的模型可见 contract。

CREATE_APPROVAL_REQUEST_TOOL_CONTRACT = {
    "type": "function",
    "function": {
        "name": "create_approval_request",
        "description": "为现有后台任务创建一条审批请求，写入 data/approvals 下的审批记录与中断记录，并把任务更新为 waiting_approval。",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "需要进入审批等待态的任务稳定 ID。"},
                "requested_action": {"type": "string", "description": "待审批动作类型，如 knowledge_write、document_write、web_lookup。"},
                "risk_level": {"type": "string", "description": "风险等级，如 low、medium、high。"},
                "request": {"type": "string", "description": "展示给用户的审批请求正文。"},
                "reason": {"type": "string", "description": "为什么要发起审批。"},
                "risk": {"type": "string", "description": "如果继续执行，该动作的风险说明。"},
                "original_action_kind": {"type": "string", "description": "原动作类别，用于恢复链标记。"},
                "original_tool_name": {"type": "string", "description": "触发审批的原工具名。"},
                "original_tool_input_preview": {"type": "string", "description": "原工具输入的简短预览，不写入完整敏感参数。"},
                "expires_at": {"type": "string", "description": "ISO-8601 格式的审批超时时间。"},
                "original_action": {"type": "string", "description": "原动作描述；不传时默认使用 requested_action。"},
                "context_id": {"type": "string", "description": "相关上下文 ID。"},
                "trace_id": {"type": "string", "description": "相关 trace ID。"},
                "resume_point": {"type": "string", "description": "任务恢复点标识；不传时默认使用 original_action_kind。"},
                "resume_payload": {"type": "string", "description": "恢复原任务时所需的单行补充载荷。"},
            },
            "required": [
                "task_id",
                "requested_action",
                "risk_level",
                "request",
                "reason",
                "risk",
                "original_action_kind",
                "original_tool_name",
                "original_tool_input_preview",
                "expires_at",
            ],
        },
    },
}


def _self_test() -> None:
    """验证工具名稳定。"""
    assert CREATE_APPROVAL_REQUEST_TOOL_CONTRACT["function"]["name"] == "create_approval_request"


if __name__ == "__main__":
    _self_test()
    print("dutyflow create_approval_request contract self-test passed")
