# 本文件定义 web_read_link 工具的模型可见 contract 结构。

WEB_READ_LINK_TOOL_CONTRACT = {
    "type": "function",
    "function": {
        "name": "web_read_link",
        "description": (
            "从上一次 web_fetch 返回的页面链接中选择一个继续读取，"
            "实现多轮可溯源跳转阅读。"
            "只能访问已抓取页面中真实存在的链接，不能跳转到模型自行构造的 URL。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "page_id": {
                    "type": "string",
                    "description": "上一次 web_fetch 返回的 page_id。",
                },
                "link_id": {
                    "type": "string",
                    "description": "该页面 links 列表中的 link_id。",
                },
            },
            "required": ["page_id", "link_id"],
        },
    },
}


def _self_test() -> None:
    assert WEB_READ_LINK_TOOL_CONTRACT["function"]["name"] == "web_read_link"
    required = WEB_READ_LINK_TOOL_CONTRACT["function"]["parameters"]["required"]
    assert "page_id" in required
    assert "link_id" in required


if __name__ == "__main__":
    _self_test()
    print("dutyflow web_read_link contract self-test passed")
