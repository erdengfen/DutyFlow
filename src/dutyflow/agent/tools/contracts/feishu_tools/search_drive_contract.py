# 本文件定义 feishu_search_drive 工具的模型可见 contract 结构。

FEISHU_SEARCH_DRIVE_TOOL_CONTRACT = {
    "type": "function",
    "function": {
        "name": "feishu_search_drive",
        "description": (
            "在飞书云盘（个人云盘和共享空间）按关键词搜索文档和文件，"
            "返回匹配项的名称、token 和类型。"
            "适用场景：用户提到文档名称但未提供链接时，用此工具主动发现对应 token，"
            "再调 feishu_read_doc 读取 docx/doc/wiki 正文，"
            "或调 feishu_get_file_meta 获取 sheet/bitable/file 等类型的元信息。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词，支持文档名称或关键词模糊匹配。",
                },
                "count": {
                    "type": "integer",
                    "description": "最多返回条数，默认 10，最大 20。",
                },
            },
            "required": ["query"],
        },
    },
}


def _self_test() -> None:
    assert FEISHU_SEARCH_DRIVE_TOOL_CONTRACT["function"]["name"] == "feishu_search_drive"
    required = FEISHU_SEARCH_DRIVE_TOOL_CONTRACT["function"]["parameters"]["required"]
    assert "query" in required
    assert "count" not in required


if __name__ == "__main__":
    _self_test()
    print("dutyflow feishu_search_drive contract self-test passed")
