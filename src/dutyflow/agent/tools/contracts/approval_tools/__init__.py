# 本包负责审批相关工具的模型可见 contract 分组。


def _self_test() -> None:
    """验证审批工具 contract 分组包可正常导入。"""
    assert __doc__ is not None


if __name__ == "__main__":
    _self_test()
    print("dutyflow approval tool contracts package self-test passed")
