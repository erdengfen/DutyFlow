# 本文件标识本地存储子包。


def _self_test() -> None:
    """验证存储子包可被导入。"""
    assert True


if __name__ == "__main__":
    _self_test()
    print("dutyflow storage package self-test passed")
