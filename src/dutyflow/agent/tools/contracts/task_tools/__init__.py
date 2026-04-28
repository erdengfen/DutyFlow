# 本包负责后台任务入口工具的模型可见 contract 分组。


def _self_test() -> None:
    """验证后台任务 contract 分组包可正常导入。"""
    assert __doc__ is not None


if __name__ == "__main__":
    _self_test()
    print("dutyflow task tool contracts package self-test passed")
