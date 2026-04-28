# 本包负责后台任务入口工具的执行逻辑分组。


def _self_test() -> None:
    """验证后台任务 logic 分组包可正常导入。"""
    assert __doc__ is not None


if __name__ == "__main__":
    _self_test()
    print("dutyflow task tool logic package self-test passed")
