"""Group guides: shared prompts hold only their own rules; how to use mail, files or
reports comes from each group, only for the groups at hand."""

from __future__ import annotations

from datetime import date

from agent_loop.prompts import system_prompt
from common.tools import discover
from common.tools.guides import collect
from compiler.prompts import RULES, rules


MANIFESTS = {name: spec.manifest() for name, spec in discover().items()}
GROUPS = ("fs.", "docs.", "xlsx.", "mail.", "data.", "charts.", "llm.")


def test_shared_prompts_name_no_group_tools():
    agent = system_prompt(date(2026, 10, 20), [])
    rules_without_examples = RULES[: RULES.index("6. Для обработки")] + RULES[RULES.index("7. Пути") : RULES.index("Пример задачи")]

    assert not any(group in agent for group in GROUPS)
    assert not any(group in rules_without_examples for group in GROUPS)


def test_agent_sees_guides_of_the_offered_tools_only():
    mail_only = [m for name, m in MANIFESTS.items() if name.startswith("mail.")]

    prompt = system_prompt(date(2026, 10, 20), collect(mail_only, "agent"))

    assert "подтверждения отправки" in prompt
    assert "xlsx.aggregate" not in prompt


def test_one_guide_per_group():
    texts = collect(list(MANIFESTS.values()), "agent")

    assert len(texts) == len(set(texts))


def test_compiler_rules_carry_the_groups_task_guides():
    text = rules(MANIFESTS)

    assert "mail.send (не mail.create_draft)" in text
    assert "data.map_rows" in text


def test_core_tools_come_from_the_tools_themselves():
    assert {name for name, m in MANIFESTS.items() if m["core"]} == {"fs.list", "docs.read"}
