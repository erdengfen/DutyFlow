# 本文件负责为身份与来源相关测试写入稳定的 Markdown fixture。

from __future__ import annotations

from pathlib import Path


def write_identity_fixtures(root: Path) -> None:
    """一次性写入联系人索引、详情文件和来源索引。"""
    write_contact_index(root)
    write_contact_detail(
        root,
        contact_id="contact_001",
        display_name="张三",
        aliases="三哥, zhangsan",
        feishu_user_id="ou_001",
        feishu_open_id="open_001",
        feishu_union_id="union_001",
        department="产品部",
        org_level="manager",
        role_title="产品经理",
        relationship_to_user="manager",
        responsibility_scope="需求确认, 项目排期",
        trust_level="normal",
        identity_summary="核心项目负责人，负责需求判断和关键排期。",
        relationship_summary="与用户是直接汇报关系，关键需求和排期事项需要及时同步。",
        decision_snippet="涉及核心项目、排期和需求澄清时默认高优先。",
        responsibility_rows=(
            ("需求确认", "负责关键需求澄清与边界判断", "high"),
            ("项目排期", "负责跨团队排期决策", "high"),
        ),
    )
    write_contact_detail(
        root,
        contact_id="contact_002",
        display_name="张三",
        aliases="老张",
        feishu_user_id="ou_002",
        feishu_open_id="open_002",
        feishu_union_id="union_002",
        department="运营部",
        org_level="peer",
        role_title="运营经理",
        relationship_to_user="peer",
        responsibility_scope="运营排期",
        trust_level="normal",
        identity_summary="运营侧负责人，主要关注活动排期。",
        relationship_summary="与用户是跨部门协作关系，需要看事项类型再判断是否打断。",
        decision_snippet="普通运营同步优先进入摘要，不默认高优先提醒。",
        responsibility_rows=(("运营排期", "负责活动资源排期", "normal"),),
    )
    write_contact_detail(
        root,
        contact_id="contact_003",
        display_name="李四",
        aliases="小李, lisi",
        feishu_user_id="ou_003",
        feishu_open_id="open_003",
        feishu_union_id="union_003",
        department="研发部",
        org_level="direct_report",
        role_title="工程师",
        relationship_to_user="direct_report",
        responsibility_scope="缺陷修复, 技术实现",
        trust_level="normal",
        identity_summary="研发执行负责人，负责技术实现与缺陷处理。",
        relationship_summary="与用户是直接协作和执行关系，紧急故障需要及时处理。",
        decision_snippet="线上故障和阻塞问题应优先提醒。",
        responsibility_rows=(
            ("缺陷修复", "负责线上与测试缺陷处理", "high"),
            ("技术实现", "负责需求对应的技术落地", "normal"),
        ),
    )
    write_source_index(root)
    write_contact_knowledge_samples(root)


def write_contact_index(root: Path) -> None:
    """写入联系人索引文件。"""
    path = root / "data/identity/contacts/index.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        (
            "---\n"
            "schema: dutyflow.contact_index.v1\n"
            "id: contact_index\n"
            "updated_at: 2026-04-25T00:00:00+08:00\n"
            "---\n\n"
            "# Contact Index\n\n"
            "| contact_id | display_name | aliases | feishu_user_id | feishu_open_id | department | org_level | detail_file |\n"
            "|---|---|---|---|---|---|---|---|\n"
            "| contact_001 | 张三 | 三哥, zhangsan | ou_001 | open_001 | 产品部 | manager | people/contact_001.md |\n"
            "| contact_002 | 张三 | 老张 | ou_002 | open_002 | 运营部 | peer | people/contact_002.md |\n"
            "| contact_003 | 李四 | 小李, lisi | ou_003 | open_003 | 研发部 | direct_report | people/contact_003.md |\n"
        ),
        encoding="utf-8",
    )


def write_contact_detail(
    root: Path,
    *,
    contact_id: str,
    display_name: str,
    aliases: str,
    feishu_user_id: str,
    feishu_open_id: str,
    feishu_union_id: str,
    department: str,
    org_level: str,
    role_title: str,
    relationship_to_user: str,
    responsibility_scope: str,
    trust_level: str,
    identity_summary: str,
    relationship_summary: str,
    decision_snippet: str,
    responsibility_rows: tuple[tuple[str, str, str], ...],
) -> None:
    """写入单人详情文件。"""
    path = root / "data/identity/contacts/people" / f"{contact_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        (
            "---\n"
            "schema: dutyflow.contact_detail.v1\n"
            f"id: {contact_id}\n"
            f"display_name: {display_name}\n"
            f"aliases: {aliases}\n"
            f"feishu_user_id: {feishu_user_id}\n"
            f"feishu_open_id: {feishu_open_id}\n"
            f"feishu_union_id: {feishu_union_id}\n"
            f"department: {department}\n"
            f"org_level: {org_level}\n"
            f"role_title: {role_title}\n"
            f"relationship_to_user: {relationship_to_user}\n"
            f"responsibility_scope: {responsibility_scope}\n"
            f"trust_level: {trust_level}\n"
            "updated_at: 2026-04-25T00:00:00+08:00\n"
            "---\n\n"
            f"# {display_name}\n\n"
            "## Identity Summary\n\n"
            f"{identity_summary}\n\n"
            "## Organization Context\n\n"
            f"- department: {department}\n"
            f"- org_level: {org_level}\n"
            f"- role_title: {role_title}\n"
            "- manager:\n"
            "- reports:\n\n"
            "## Relationship To User\n\n"
            f"{relationship_summary}\n\n"
            "## Responsibility Context\n\n"
            "| scope | description | default_weight |\n"
            "|---|---|---|\n"
            f"{_render_responsibility_rows(responsibility_rows)}\n\n"
            "## Matching Notes\n\n"
            "用于测试姓名、别名和飞书 ID 匹配。\n\n"
            "## Decision Snippets\n\n"
            f"{decision_snippet}\n"
        ),
        encoding="utf-8",
    )


def write_source_index(root: Path) -> None:
    """写入来源索引文件。"""
    path = root / "data/identity/sources/index.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        (
            "---\n"
            "schema: dutyflow.source_index.v1\n"
            "id: source_index\n"
            "updated_at: 2026-04-25T00:00:00+08:00\n"
            "---\n\n"
            "# Source Index\n\n"
            "| source_id | source_type | feishu_id | display_name | owner_contact_id | default_weight | notes |\n"
            "|---|---|---|---|---|---|---|\n"
            "| source_chat_001 | chat | oc_project_group | 核心项目群 | contact_001 | high | 核心项目群，排期和需求同步默认高优先。 |\n"
            "| source_doc_001 | doc | doc_spec_001 | PRD 文档 | contact_001 | normal | 需求文档更新入口。 |\n"
            "| source_dm_001 | direct_message | dm_003 | 李四私聊 | contact_003 | high | 紧急问题通常从私聊直接触达。 |\n"
        ),
        encoding="utf-8",
    )


def write_contact_knowledge_samples(root: Path) -> None:
    """写入一组联系人知识测试样本。"""
    write_contact_knowledge_note(
        root,
        contact_id="contact_001",
        note_id="ckn_001",
        topic="escalation_preference",
        keywords="priority, escalation, async",
        confidence="high",
        status="active",
        source_refs="manual_input, evt_demo_001",
        summary="涉及核心项目排期时，对方希望先收到书面摘要，再决定是否立即开会。",
        structured_facts_markdown=(
            "| fact_key | fact_value | confidence | source_ref |\n"
            "|---|---|---|---|\n"
            "| escalation_style | 先看书面摘要再决定会议 | high | manual_input |\n"
            "| urgent_threshold | 影响当周上线或核心排期时立即同步 | high | evt_demo_001 |"
        ),
        decision_value="遇到排期冲突或高优先级需求时，先异步发一段摘要，只有达到紧急阈值再升级为即时提醒。",
    )
    write_contact_knowledge_note(
        root,
        contact_id="contact_002",
        note_id="ckn_002",
        topic="coordination_style",
        keywords="ops, summary, batching",
        confidence="medium",
        status="active",
        source_refs="manual_input",
        summary="对方偏好把运营类同步合并成固定时段处理，不希望被零散打断。",
        structured_facts_markdown=(
            "| fact_key | fact_value | confidence | source_ref |\n"
            "|---|---|---|---|\n"
            "| sync_window | 每天下午统一处理运营同步 | medium | manual_input |\n"
            "| interruption_rule | 非紧急事项默认进入摘要 | medium | manual_input |"
        ),
        decision_value="普通运营协作优先汇总成摘要，不要因为单条消息即时打断。",
    )
    write_contact_knowledge_note(
        root,
        contact_id="contact_003",
        note_id="ckn_003",
        topic="incident_response",
        keywords="bug, incident, immediate",
        confidence="high",
        status="active",
        source_refs="manual_input, evt_demo_003",
        summary="线上故障或阻塞缺陷需要立即同步，对方接受直接私聊或电话升级。",
        structured_facts_markdown=(
            "| fact_key | fact_value | confidence | source_ref |\n"
            "|---|---|---|---|\n"
            "| urgent_channel | 私聊优先，必要时电话升级 | high | manual_input |\n"
            "| response_expectation | 线上故障需要即时反馈 | high | evt_demo_003 |"
        ),
        decision_value="缺陷修复和线上事故要直接提醒，不进入延后摘要。",
    )


def write_contact_knowledge_note(
    root: Path,
    *,
    contact_id: str,
    note_id: str,
    topic: str,
    keywords: str,
    confidence: str,
    status: str,
    source_refs: str,
    summary: str,
    structured_facts_markdown: str,
    decision_value: str,
) -> None:
    """写入单条联系人知识记录。"""
    path = root / "data/knowledge/contacts" / contact_id / f"{note_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        (
            "---\n"
            "schema: dutyflow.contact_knowledge_note.v1\n"
            f"id: {note_id}\n"
            f"contact_id: {contact_id}\n"
            f"topic: {topic}\n"
            f"keywords: {keywords}\n"
            f"confidence: {confidence}\n"
            f"status: {status}\n"
            f"source_refs: {source_refs}\n"
            "created_at: 2026-04-25T00:00:00+08:00\n"
            "updated_at: 2026-04-25T00:00:00+08:00\n"
            "---\n\n"
            f"# Contact Knowledge {note_id}\n\n"
            "## Summary\n\n"
            f"{summary}\n\n"
            "## Structured Facts\n\n"
            f"{structured_facts_markdown}\n\n"
            "## Decision Value\n\n"
            f"{decision_value}\n\n"
            "## Change Log\n\n"
            "| at | action | note |\n"
            "|---|---|---|\n"
            "| 2026-04-25T00:00:00+08:00 | created | 初次记录 |\n"
        ),
        encoding="utf-8",
    )


def _render_responsibility_rows(rows: tuple[tuple[str, str, str], ...]) -> str:
    """把责任表行渲染成 Markdown 表格正文。"""
    return "\n".join(f"| {scope} | {description} | {weight} |" for scope, description, weight in rows)


def _self_test() -> None:
    """验证 fixture 写入后关键文件存在。"""
    import tempfile

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        write_identity_fixtures(root)
        assert (root / "data/identity/contacts/index.md").exists()
        assert (root / "data/identity/contacts/people/contact_001.md").exists()
        assert (root / "data/identity/sources/index.md").exists()
        assert (root / "data/knowledge/contacts/contact_001/ckn_001.md").exists()


if __name__ == "__main__":
    _self_test()
    print("identity fixture data self-test passed")
