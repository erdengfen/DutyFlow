# 本包负责身份、来源与责任上下文的结构化解析能力。


def _self_test() -> None:
    """验证 identity 包可正常导入。"""
    assert __name__ == "dutyflow.identity"


if __name__ == "__main__":
    _self_test()
    print("dutyflow identity package self-test passed")
