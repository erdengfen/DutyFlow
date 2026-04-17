# 本文件负责限制在项目工作区内的基础文件读写能力。

from __future__ import annotations

from pathlib import Path


class FileStore:
    """提供带工作区边界检查的本地文件操作。"""

    def __init__(self, root: Path) -> None:
        """设置文件存储根目录。"""
        self.root = root.resolve()

    def ensure_dir(self, path: str | Path) -> Path:
        """创建工作区内目录并返回绝对路径。"""
        resolved = self.resolve(path)
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved

    def read_text(self, path: str | Path) -> str:
        """读取工作区内文本文件。"""
        return self.resolve(path).read_text(encoding="utf-8")

    def write_text(self, path: str | Path, content: str) -> Path:
        """写入工作区内文本文件。"""
        resolved = self.resolve(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        return resolved

    def exists(self, path: str | Path) -> bool:
        """判断工作区内路径是否存在。"""
        return self.resolve(path).exists()

    def resolve(self, path: str | Path) -> Path:
        """解析路径并阻止逃逸项目工作区。"""
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError(f"Path escapes workspace: {path}")
        return resolved


def _self_test() -> None:
    """验证路径逃逸会被阻止。"""
    store = FileStore(Path.cwd())
    try:
        store.resolve("../outside-dutyflow")
    except ValueError:
        return
    raise AssertionError("path escape was not blocked")


if __name__ == "__main__":
    _self_test()
    print("dutyflow file store self-test passed")
