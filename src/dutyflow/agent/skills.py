# 本文件负责技能文档的解析、注册和向模型暴露轻量元信息。

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SkillManifest:
    """表示模型侧默认可见的轻量 skill 元信息。"""

    name: str
    description: str

    def __post_init__(self) -> None:
        """校验当前阶段必需的最小字段。"""
        if not self.name:
            raise ValueError("SkillManifest.name is required")
        if not self.description:
            raise ValueError("SkillManifest.description is required")


@dataclass(frozen=True)
class SkillDocument:
    """表示包含完整正文的技能文档。"""

    manifest: SkillManifest
    body: str

    def __post_init__(self) -> None:
        """校验完整技能正文不能为空。"""
        if not self.body.strip():
            raise ValueError("SkillDocument.body cannot be empty")


class SkillRegistry:
    """统一管理本地 `skills/<skill_name>/SKILL.md` 的解析结果。"""

    def __init__(self, skills_dir: Path, eager_load: bool = True) -> None:
        """绑定技能目录，并在默认情况下完成初始化加载。"""
        self.skills_dir = Path(skills_dir)
        self._skills: dict[str, SkillDocument] = {}
        if eager_load:
            self._load_all()

    @classmethod
    def empty(cls, skills_dir: Path) -> "SkillRegistry":
        """创建一个不扫描磁盘的空注册表。"""
        return cls(skills_dir, eager_load=False)

    def has(self, name: str) -> bool:
        """返回指定技能名是否已存在。"""
        return name in self._skills

    def list_manifests(self) -> tuple[SkillManifest, ...]:
        """返回按名称排序的 manifest 列表。"""
        return tuple(self._skills[name].manifest for name in sorted(self._skills))

    def describe_available(self) -> str:
        """返回适合拼入 system message 的可用技能摘要。"""
        manifests = self.list_manifests()
        if not manifests:
            return "(none)"
        return "\n".join(f"- {item.name}: {item.description}" for item in manifests)

    def load_full_text(self, name: str) -> str:
        """按名称返回技能完整正文。"""
        return self.get(name).body

    def get(self, name: str) -> SkillDocument:
        """按名称返回完整技能文档。"""
        if name not in self._skills:
            raise KeyError(f"Skill is not registered: {name}")
        return self._skills[name]

    def system_prompt_text(self) -> str:
        """返回当前技能清单对应的 system message 文本。"""
        return "Skills available:\n" + self.describe_available()

    def _load_all(self) -> None:
        """扫描技能目录并一次性缓存全部技能。"""
        self._skills = {}
        if not self.skills_dir.exists():
            return
        for path in sorted(self.skills_dir.rglob("SKILL.md")):
            document = self._load_document(path)
            name = document.manifest.name
            if name in self._skills:
                raise ValueError(f"Duplicate skill name: {name}")
            self._skills[name] = document

    def _load_document(self, path: Path) -> SkillDocument:
        """读取并解析单个技能文档。"""
        meta, body = _parse_skill_markdown(path.read_text(encoding="utf-8"))
        return SkillDocument(
            manifest=SkillManifest(
                name=meta["name"],
                description=meta["description"],
            ),
            body=body,
        )


def _parse_skill_markdown(text: str) -> tuple[dict[str, str], str]:
    """解析 `SKILL.md` 的简单 frontmatter 和正文。"""
    if not text.startswith("---\n"):
        raise ValueError("Skill markdown must start with frontmatter")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise ValueError("Skill markdown frontmatter is not closed")
    raw_meta = text[4:end]
    body = text[end + 5 :].lstrip("\n")
    meta = _parse_skill_frontmatter(raw_meta)
    if "name" not in meta or "description" not in meta:
        raise ValueError("Skill frontmatter requires name and description")
    return meta, body


def _parse_skill_frontmatter(raw: str) -> dict[str, str]:
    """解析技能 frontmatter；当前只要求 name / description。"""
    meta: dict[str, str] = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        if line.startswith((" ", "-", "[")) or ":" not in line:
            raise ValueError("Complex skill frontmatter is not allowed")
        key, value = line.split(":", 1)
        normalized_key = key.strip()
        # 预留说明：后续 skill manifest 允许扩展其它字段，但解析层当前只把它们当作可选字符串，
        # 不允许这些字段成为当前阶段的必需字段。
        meta[normalized_key] = value.strip().strip('"').strip("'")
    return meta


def _self_test() -> None:
    """验证技能 Markdown 可解析为 manifest 和正文。"""
    meta, body = _parse_skill_markdown(
        "---\nname: demo-skill\ndescription: demo description\n---\n\n# Demo\n\nbody"
    )
    registry = SkillRegistry.empty(Path("skills"))
    assert meta["name"] == "demo-skill"
    assert body.startswith("# Demo")
    assert registry.describe_available() == "(none)"


if __name__ == "__main__":
    _self_test()
    print("dutyflow skill registry self-test passed")
