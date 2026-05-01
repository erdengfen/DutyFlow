# 本文件提供 prompt_config 包的命令行自测入口。

from dutyflow.config.prompt_config import _self_test


if __name__ == "__main__":
    _self_test()
    print("dutyflow prompt config package self-test passed")
