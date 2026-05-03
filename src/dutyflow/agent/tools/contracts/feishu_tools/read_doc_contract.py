# 本文件定义 feishu_read_doc 工具的模型可见 contract 结构。

FEISHU_READ_DOC_TOOL_CONTRACT = {
    "type": "function",
    "function": {
        "name": "feishu_read_doc",
        "description": (
            "读取一个飞书 docx 文档的完整正文。"
            "文档 token 必须来自用户分享的链接或消息中明确提及的文档，"
            "不允许模型自行构造或猜测 token。"
            "完整正文写入 Evidence Store，模型上下文只保留前 1000 字预览。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "doc_token": {
                    "type": "string",
                    "description": (
                        "飞书文档 token，从文档 URL（open.feishu.cn/docx/<token>）"
                        "或用户提供的分享链接中提取。"
                    ),
                },
            },
            "required": ["doc_token"],
        },
    },
}


def _self_test() -> None:
    assert FEISHU_READ_DOC_TOOL_CONTRACT["function"]["name"] == "feishu_read_doc"
    assert "doc_token" in FEISHU_READ_DOC_TOOL_CONTRACT["function"]["parameters"]["required"]


if __name__ == "__main__":
    _self_test()
    print("dutyflow feishu_read_doc contract self-test passed")
