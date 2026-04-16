# 本文件标识 CLI 子包，CLI 控制台实现位于 main.py。


def _self_test() -> None:
    """验证 CLI 子包可被导入。"""
    assert True


if __name__ == "__main__":
    _self_test()
    print("dutyflow cli package self-test passed")
