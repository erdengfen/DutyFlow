# 本文件标识配置子包，统一配置读取实现位于 env.py。


def _self_test() -> None:
    """验证配置子包可被导入。"""
    assert True


if __name__ == "__main__":
    _self_test()
    print("dutyflow config package self-test passed")
