# 本文件定义 schedule_background_task 工具的模型可见 contract。

SCHEDULE_BACKGROUND_TASK_TOOL_CONTRACT = {
    "type": "function",
    "function": {
        "name": "schedule_background_task",
        "description": "创建一条在未来指定时间执行的一次性后台任务，写入 data/tasks 下的结构化 Markdown 任务文件。",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "任务标题，需直接说明任务主题。"},
                "goal": {"type": "string", "description": "任务目标，说明后台完成后要达到什么结果。"},
                "success_criteria": {"type": "string", "description": "成功标准，说明什么结果可视为任务完成。"},
                "scheduled_for": {
                    "type": "string",
                    "description": (
                        "带时区的 ISO-8601 绝对执行时间，必须晚于当前消息接收时间和当前系统时间。"
                        "如果用户说“明天”“稍后”“2分钟后”等相对时间，必须先换算成绝对时间。"
                    ),
                },
                "user_visible_summary": {"type": "string", "description": "给用户看的简洁任务摘要；不传时默认使用 goal。"},
                "context_refs": {"type": "string", "description": "英文逗号分隔的上下文引用，如 perception_id、event_id、task_id。"},
                "capability_requirements": {"type": "string", "description": "英文逗号分隔的能力类别，如 identity_lookup、web_lookup、knowledge_write。"},
                "preferred_skills": {"type": "string", "description": "英文逗号分隔的优先技能名，必须已在 skills/ 下注册。"},
                "preferred_tools": {"type": "string", "description": "英文逗号分隔的优先工具名，系统会再次校验是否允许进入后台执行面。"},
            },
            "required": ["title", "goal", "success_criteria", "scheduled_for"],
        },
    },
}


def _self_test() -> None:
    """验证工具名稳定。"""
    assert SCHEDULE_BACKGROUND_TASK_TOOL_CONTRACT["function"]["name"] == "schedule_background_task"


if __name__ == "__main__":
    _self_test()
    print("dutyflow schedule_background_task contract self-test passed")
