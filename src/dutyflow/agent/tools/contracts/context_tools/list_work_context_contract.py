# 本文件定义 list_work_context 工具的模型可见 contract。

LIST_WORK_CONTEXT_TOOL_CONTRACT = {
    "type": "function",
    "function": {
        "name": "list_work_context",
        "description": (
            "只读枚举 DutyFlow 本地已落盘的工作上下文轻量 refs，"
            "用于回答“今天有什么事项”“项目卡在哪里”等短句。"
            "该工具不访问飞书 API，不读取项目外文件，返回结果可继续交给 read_context_ref 展开。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "可选日期，格式 YYYY-MM-DD；也支持 today 或 今天。",
                },
                "source_types": {
                    "type": "string",
                    "description": (
                        "可选英文逗号分隔过滤项，支持 direct_message、group_message、user_document、"
                        "task、approval、evidence、report。"
                    ),
                },
                "query": {
                    "type": "string",
                    "description": "可选关键词，在标题、摘要、ref_id 和来源类型中做轻量包含匹配。",
                },
                "task_status": {
                    "type": "string",
                    "description": "可选英文逗号分隔任务状态，如 queued、scheduled、completed、waiting_approval。",
                },
                "approval_status": {
                    "type": "string",
                    "description": "可选英文逗号分隔审批状态，如 waiting、approved、rejected。",
                },
                "limit": {
                    "type": "integer",
                    "description": "最多返回条数，默认 20，最大 50。",
                },
            },
            "required": [],
        },
    },
}


def _self_test() -> None:
    """验证工具名稳定。"""
    assert LIST_WORK_CONTEXT_TOOL_CONTRACT["function"]["name"] == "list_work_context"


if __name__ == "__main__":
    _self_test()
    print("dutyflow list_work_context contract self-test passed")
