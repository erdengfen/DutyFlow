# 本文件定义 resume_after_approval 工具的模型可见 contract。

RESUME_AFTER_APPROVAL_TOOL_CONTRACT = {
    "type": "function",
    "function": {
        "name": "resume_after_approval",
        "description": "根据用户审批结果完成审批记录，并更新对应后台任务状态；仅 approved 会把任务重新放入等待恢复执行状态。",
        "parameters": {
            "type": "object",
            "properties": {
                "approval_id": {"type": "string", "description": "待恢复处理的审批稳定 ID。"},
                "decision_result": {
                    "type": "string",
                    "description": "审批结果，只允许 approved、rejected、deferred、expired。",
                },
                "decided_by": {"type": "string", "description": "审批决策来源，如 owner_open_id 或 user。"},
                "resume_token": {"type": "string", "description": "可选恢复 token；传入时必须与审批记录一致。"},
                "comment": {"type": "string", "description": "用户审批备注或系统处理说明。"},
            },
            "required": ["approval_id", "decision_result", "decided_by"],
        },
    },
}


def _self_test() -> None:
    """验证工具名稳定。"""
    assert RESUME_AFTER_APPROVAL_TOOL_CONTRACT["function"]["name"] == "resume_after_approval"


if __name__ == "__main__":
    _self_test()
    print("dutyflow resume_after_approval contract self-test passed")
