# 本文件实现 create_skill 工具的受控写入逻辑。

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from dutyflow.agent.tools.contracts.skill_tools.create_skill_contract import CREATE_SKILL_TOOL_CONTRACT
from dutyflow.agent.tools.types import ToolCall, ToolResultEnvelope, error_envelope

# 关键边界：skill 名称最多 64 个字符，只允许安全目录名字符，防止路径穿越和过长文件名。
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class CreateSkillTool:
    """创建本地 skill 文档的敏感内部工具，只允许写入 skills 子目录。"""

    name = "create_skill"
    contract = CREATE_SKILL_TOOL_CONTRACT
    is_concurrency_safe = False
    requires_approval = True
    timeout_seconds = 30.0
    max_retries = 0
    retry_policy = "none"
    idempotency = "idempotent"
    degradation_mode = "escalate"
    fallback_tool_names = ()

    def handle(
        self,
        tool_call: ToolCall,
        tool_use_context,
    ) -> ToolResultEnvelope:
        """校验输入并创建新的 `skills/<name>/SKILL.md`。"""
        name = str(tool_call.tool_input["name"]).strip()
        description = str(tool_call.tool_input["description"]).strip()
        body = str(tool_call.tool_input["body"]).strip()
        validation_error = _validate_skill_input(name, description, body)
        if validation_error:
            return error_envelope(tool_call, "invalid_skill_input", validation_error)
        path = _skill_path(tool_use_context.cwd, name)
        if path.exists():
            return error_envelope(tool_call, "skill_already_exists", f"skill already exists: {name}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_skill_markdown(name, description, body), encoding="utf-8")
        return ToolResultEnvelope(
            tool_call.tool_use_id,
            tool_call.tool_name,
            True,
            f"created skill {name} at skills/{name}/SKILL.md",
            attachments=(str(path),),
        )


def _validate_skill_input(name: str, description: str, body: str) -> str:
    """校验 skill 名称、描述和正文是否符合当前写入边界。"""
    if not SKILL_NAME_PATTERN.fullmatch(name):
        return "skill name must match ^[a-z0-9][a-z0-9_-]{0,63}$"
    if not description or "\n" in description or "\r" in description:
        return "skill description must be a single non-empty line"
    if not body:
        return "skill body cannot be empty"
    return ""


def _skill_path(cwd: Path, name: str) -> Path:
    """返回受控 skill 写入路径，不接受外部路径参数。"""
    return Path(cwd) / "skills" / name / "SKILL.md"


def _render_skill_markdown(name: str, description: str, body: str) -> str:
    """渲染符合当前解析层要求的 SKILL.md 文本。"""
    return f"---\nname: {name}\ndescription: {description}\n---\n\n{body.rstrip()}\n"


def _self_test() -> None:
    """验证 CreateSkillTool 能在临时目录中创建 skill 文档。"""
    with tempfile.TemporaryDirectory() as temp_dir:
        call = ToolCall(
            "tool_1",
            "create_skill",
            {"name": "demo_skill", "description": "demo", "body": "# Demo"},
            0,
            0,
        )
        context = type("Context", (), {"cwd": Path(temp_dir)})()
        result = CreateSkillTool().handle(call, context)
        assert result.ok
        assert (Path(temp_dir) / "skills" / "demo_skill" / "SKILL.md").exists()


if __name__ == "__main__":
    _self_test()
    print("dutyflow agent create_skill logic self-test passed")
