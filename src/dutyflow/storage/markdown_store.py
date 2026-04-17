# 本文件负责结构化 Markdown 文件的 frontmatter 读写和章节抽取。

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dutyflow.storage.file_store import FileStore


@dataclass
class MarkdownDocument:
    """表示一个带简单 frontmatter 的 Markdown 文档。"""

    frontmatter: dict[str, str]
    body: str


class MarkdownStore:
    """读写本地 Markdown 结构化数据文件。"""

    def __init__(self, file_store: FileStore) -> None:
        """绑定底层文件存储。"""
        self.file_store = file_store

    def exists(self, path: str | Path) -> bool:
        """判断 Markdown 文件是否存在。"""
        return self.file_store.exists(path)

    def read_document(self, path: str | Path) -> MarkdownDocument:
        """读取 Markdown 文档并解析简单 frontmatter。"""
        text = self.file_store.read_text(path)
        return self._parse(text)

    def write_document(self, path: str | Path, document: MarkdownDocument) -> Path:
        """写入 Markdown 文档并校验 frontmatter 简单性。"""
        self._validate_frontmatter(document.frontmatter)
        return self.file_store.write_text(path, self._render(document))

    def extract_section(self, path: str | Path, heading: str) -> str:
        """按二级标题抽取正文片段。"""
        document = self.read_document(path)
        lines = document.body.splitlines()
        collected: list[str] = []
        in_section = False
        target = f"## {heading}".strip()
        for line in lines:
            if line.strip() == target:
                in_section = True
                continue
            if in_section and line.startswith("## "):
                break
            if in_section:
                collected.append(line)
        return "\n".join(collected).strip()

    def _parse(self, text: str) -> MarkdownDocument:
        """解析 Markdown 文本中的简单 frontmatter。"""
        if not text.startswith("---\n"):
            return MarkdownDocument(frontmatter={}, body=text)
        end = text.find("\n---\n", 4)
        if end == -1:
            raise ValueError("Markdown frontmatter is not closed")
        raw_meta = text[4:end]
        body = text[end + 5 :].lstrip("\n")
        return MarkdownDocument(self._parse_frontmatter(raw_meta), body)

    def _parse_frontmatter(self, raw: str) -> dict[str, str]:
        """解析只允许 key: value 的 frontmatter。"""
        meta: dict[str, str] = {}
        for line in raw.splitlines():
            if not line.strip():
                continue
            if line.startswith((" ", "-", "[")) or ":" not in line:
                raise ValueError("Complex frontmatter is not allowed")
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip().strip('"').strip("'")
        return meta

    def _render(self, document: MarkdownDocument) -> str:
        """渲染 Markdown 文档。"""
        meta = "\n".join(f"{key}: {value}" for key, value in document.frontmatter.items())
        return f"---\n{meta}\n---\n\n{document.body}"

    def _validate_frontmatter(self, frontmatter: dict[str, str]) -> None:
        """禁止列表、字典等复杂 frontmatter 值。"""
        for key, value in frontmatter.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError("Only string frontmatter keys and values are allowed")
            if "\n" in value or value.strip().startswith(("[", "{", "-")):
                raise ValueError("Complex frontmatter values are not allowed")


def _self_test() -> None:
    """验证 Markdown 文档可渲染和解析。"""
    store = MarkdownStore(FileStore(Path.cwd()))
    parsed = store._parse("---\nschema: demo\n---\n\n# Demo\n\n## A\n\nbody")
    assert parsed.frontmatter["schema"] == "demo"
    assert "Demo" in parsed.body


if __name__ == "__main__":
    _self_test()
    print("dutyflow markdown store self-test passed")
