# 本文件定义 web_search 工具的模型可见 contract 结构。

WEB_SEARCH_TOOL_CONTRACT = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "在互联网上搜索关键词，返回候选 URL 列表和摘要片段。"
            "不读取页面全文；用 web_fetch 进一步读取具体页面。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词。",
                },
                "max_results": {
                    "type": "integer",
                    "description": "最多返回结果数，默认 5，上限 10。",
                    "default": 5,
                },
                "allowed_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "只返回这些域名的结果，可选。",
                },
                "blocked_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "排除这些域名的结果，可选。",
                },
                "time_range": {
                    "type": "string",
                    "description": "时间范围：d（天）/ w（周）/ m（月）/ y（年），可选。",
                },
            },
            "required": ["query"],
        },
    },
}


def _self_test() -> None:
    assert WEB_SEARCH_TOOL_CONTRACT["function"]["name"] == "web_search"
    assert "query" in WEB_SEARCH_TOOL_CONTRACT["function"]["parameters"]["required"]


if __name__ == "__main__":
    _self_test()
    print("dutyflow web_search contract self-test passed")
