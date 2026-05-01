# 本文件提供 context 包的命令行自测入口。

from dutyflow.context import _self_test


if __name__ == "__main__":
    _self_test()
    print("dutyflow context package self-test passed")
