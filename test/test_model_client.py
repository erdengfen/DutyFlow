# 本文件验证模型适配层的响应解析和配置缺失错误。

from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.model_client import (  # noqa: E402
    OpenAICompatibleModelClient,
    parse_model_response,
)
from dutyflow.config.env import EnvConfig  # noqa: E402


class TestModelClient(unittest.TestCase):
    """验证 OpenAI-compatible 模型适配层。"""

    def test_parse_text_response(self) -> None:
        """文本响应应转换为 assistant text block。"""
        payload = {"choices": [{"message": {"content": "pong"}, "finish_reason": "stop"}]}
        response = parse_model_response(payload)
        self.assertEqual(response.assistant_blocks[0].type, "text")
        self.assertEqual(response.assistant_blocks[0].text, "pong")

    def test_parse_tool_call_response(self) -> None:
        """tool_calls 响应应转换为 tool_use block。"""
        payload = {"choices": [{"message": {"tool_calls": [_tool_call()]}}]}
        response = parse_model_response(payload)
        block = response.assistant_blocks[0]
        self.assertEqual(block.type, "tool_use")
        self.assertEqual(block.tool_name, "echo_text")
        self.assertEqual(block.tool_input["text"], "hello")

    def test_missing_model_config_fails_clearly(self) -> None:
        """缺失模型配置时真实客户端必须返回明确错误。"""
        with self.assertRaisesRegex(ValueError, "missing required env keys"):
            OpenAICompatibleModelClient(_empty_config())

    def test_endpoint_uses_base_url_as_is(self) -> None:
        """BASE_URL 应直接作为完整端点使用，不再自动拼接路径。"""
        client = OpenAICompatibleModelClient(_filled_config("https://example.com/custom-endpoint"))
        self.assertEqual(client._endpoint(), "https://example.com/custom-endpoint")


def _tool_call() -> dict:
    """构造 provider tool_call 响应。"""
    return {
        "id": "tool_1",
        "type": "function",
        "function": {"name": "echo_text", "arguments": "{\"text\": \"hello\"}"},
    }


def _empty_config() -> EnvConfig:
    """构造缺失模型配置的 EnvConfig。"""
    return EnvConfig(
        model_api_key="",
        model_base_url="",
        model_name="",
        feishu_app_id="",
        feishu_app_secret="",
        feishu_event_verify_token="",
        feishu_event_encrypt_key="",
        feishu_event_callback_url="",
        data_dir=Path("data"),
        log_dir=Path("data/logs"),
        runtime_env="test",
        log_level="INFO",
    )


def _filled_config(base_url: str) -> EnvConfig:
    """构造最小可用模型配置。"""
    return EnvConfig(
        model_api_key="key",
        model_base_url=base_url,
        model_name="demo-model",
        feishu_app_id="",
        feishu_app_secret="",
        feishu_event_verify_token="",
        feishu_event_encrypt_key="",
        feishu_event_callback_url="",
        data_dir=Path("data"),
        log_dir=Path("data/logs"),
        runtime_env="test",
        log_level="INFO",
    )


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestModelClient)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
