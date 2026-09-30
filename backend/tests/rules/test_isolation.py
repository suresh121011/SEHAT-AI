"""The rules engine must not depend on the LLM layer, the network, or storage."""

import ast
from pathlib import Path

import app.rules
from app.rules.registry import Rule
from app.rules.sources import SOURCES

RULES_DIR = Path(app.rules.__file__).parent
FORBIDDEN = ("app.services", "app.database", "app.routes", "semantic_kernel", "openai", "httpx", "requests", "aiohttp", "urllib", "socket", "sqlite3", "aiosqlite", "random")


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_rules_package_has_no_llm_network_or_io_imports():
    offenders = {
        str(p.relative_to(RULES_DIR)): sorted(n for n in _imports(p) if n.startswith(FORBIDDEN))
        for p in RULES_DIR.rglob("*.py")
    }
    assert {k: v for k, v in offenders.items() if v} == {}


def test_every_rule_cites_a_registered_source():
    from app.rules.atp import ATP_RULES
    from app.rules.scenarios import PACKS

    rules: list[Rule] = list(ATP_RULES) + [r for p in PACKS.values() for r in p.urgency_rules]
    assert rules
    for rule in rules:
        assert rule.source_id in SOURCES, rule.id
    ids = [r.id for r in rules]
    assert len(ids) == len(set(ids)), "rule IDs must be unique"
