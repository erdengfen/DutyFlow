# 本文件定义 web_fetch 工具的模型可见 contract 结构。

WEB_FETCH_TOOL_CONTRACT = {
    "type": "function",
    "function": {
        "name": "web_fetch",
        "description": (
            "读取一个明确 URL 的页面正文和内部链接。"
            "URL 必须来自用户提供、web_search 结果或上一次 web_fetch 返回的 links，"
            "不允许访问模型自行构造的任意 URL。"
            "页面完整正文写入 Evidence Store，模型上下文只保留摘要和链接索引。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "目标 URL，必须是 http 或 https。",
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "最大读取字节数，默认 204800（200KB），上限 1048576（1MB）。",
                    "default": 204800,
                },
                "timeout": {
                    "type": "number",
                    "description": "超时秒数，默认 15，上限 30。",
                    "default": 15,
                },
                "extract_links": {
                    "type": "boolean",
                    "description": "是否从页面提取内部链接，默认 true。",
                    "default": True,
                },
            },
            "required": ["url"],
        },
    },
}


def _self_test() -> None:
    assert WEB_FETCH_TOOL_CONTRACT["function"]["name"] == "web_fetch"
    assert "url" in WEB_FETCH_TOOL_CONTRACT["function"]["parameters"]["required"]


if __name__ == "__main__":
    _self_test()
    print("dutyflow web_fetch contract self-test passed")
