# 本文件标识 dutyflow 包，并提供项目版本信息。

__version__ = "0.1.0"


def _self_test() -> None:
    """验证包元信息可被导入。"""
    assert __version__ == "0.1.0"


if __name__ == "__main__":
    _self_test()
    print("dutyflow package self-test passed")
