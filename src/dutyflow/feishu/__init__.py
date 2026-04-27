# 本文件声明 DutyFlow 的飞书接入层包，集中放置 Step 5 的最小接入骨架。

from dutyflow.feishu.client import FeishuClient, FeishuClientResult
from dutyflow.feishu.events import FeishuEventAdapter, FeishuEventEnvelope
from dutyflow.feishu.runtime import FeishuIngressResult, FeishuIngressService

__all__ = [
    "FeishuClient",
    "FeishuClientResult",
    "FeishuEventAdapter",
    "FeishuEventEnvelope",
    "FeishuIngressResult",
    "FeishuIngressService",
]


def _self_test() -> None:
    """验证飞书接入层包导出的核心类型可被正常导入。"""
    assert FeishuClient is not None
    assert FeishuIngressService is not None


if __name__ == "__main__":
    _self_test()
    print("dutyflow feishu package self-test passed")
