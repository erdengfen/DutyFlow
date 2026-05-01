# 本文件验证静态 prompt 配置与动态 skills 注入边界保持分离。

from __future__ import annotations

from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.config.prompt_config import (  # noqa: E402
    get_background_subagent_system_prompt,
    get_main_agent_system_prompt,
)


class TestPromptConfig(unittest.TestCase):
    """验证主 Agent 和后台 subagent 的静态 prompt 已收束到配置层。"""

    def test_main_agent_prompt_is_static_preamble_only(self) -> None:
        """主 Agent 静态 prompt 不应包含运行时动态注入的 skills 清单。"""
        prompt = get_main_agent_system_prompt()
        self.assertIn("DutyFlow", prompt)
        self.assertIn("Always respond in Chinese", prompt)
        self.assertNotIn("Skills available:", prompt)

    def test_background_subagent_prompt_is_static_preamble_only(self) -> None:
        """后台 subagent 静态 prompt 不应包含任务动态限定的 skills 或 tools 清单。"""
        prompt = get_background_subagent_system_prompt()
        self.assertIn("background subagent", prompt)
        self.assertIn("Use only the tools and skills exposed to this task", prompt)
        self.assertNotIn("Skills available:", prompt)


if __name__ == "__main__":
    unittest.main()
