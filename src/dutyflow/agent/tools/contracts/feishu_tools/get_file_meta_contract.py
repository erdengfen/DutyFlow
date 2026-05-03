# 本文件定义 feishu_get_file_meta 工具的模型可见 contract 结构。

FEISHU_GET_FILE_META_TOOL_CONTRACT = {
    "type": "function",
    "function": {
        "name": "feishu_get_file_meta",
        "description": (
            "读取飞书云盘文件或文档的元信息（标题、所有者、创建时间、最后编辑时间），不读取正文。"
            "用于快速判断文件归属和时效，无需消耗大量 token 读取内容。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_token": {
                    "type": "string",
                    "description": "文件或文档 token，从飞书分享链接或用户消息中提取。",
                },
                "file_type": {
                    "type": "string",
                    "description": "文件类型，取值：doc / docx / sheet / bitable / folder / file。",
                    "enum": ["doc", "docx", "sheet", "bitable", "folder", "file"],
                },
            },
            "required": ["file_token", "file_type"],
        },
    },
}


def _self_test() -> None:
    assert FEISHU_GET_FILE_META_TOOL_CONTRACT["function"]["name"] == "feishu_get_file_meta"
    required = FEISHU_GET_FILE_META_TOOL_CONTRACT["function"]["parameters"]["required"]
    assert "file_token" in required
    assert "file_type" in required


if __name__ == "__main__":
    _self_test()
    print("dutyflow feishu_get_file_meta contract self-test passed")
