"""Code boundaries of the assistant (docs/24 §5). Static checks over app/: who may call a chat model, how the assistant
reaches one (only through the privacy gateway), and what the assistant package may never import or write."""

import ast
from pathlib import Path

import app

APP_DIR = Path(app.__file__).parent
CHAT_CALLS = ("get_chat_message_content", "get_chat_message_contents", "get_streaming_chat_message_content",
              "get_streaming_chat_message_contents")
# The only modules that may call a chat service: extraction, image description, and the assistant.
CHAT_CALLERS = {"ai/azure_provider.py", "ocr/medgemma.py", "assistant/service.py"}


def _trees():
    for path in APP_DIR.rglob("*.py"):
        yield path.relative_to(APP_DIR).as_posix(), ast.parse(path.read_text(encoding="utf-8"))


def test_only_named_modules_call_a_chat_model():
    callers = {rel for rel, tree in _trees() for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr in CHAT_CALLS}
    assert callers == CHAT_CALLERS


def test_the_assistant_reaches_the_model_only_through_the_privacy_gateway():
    tree = ast.parse((APP_DIR / "assistant" / "service.py").read_text(encoding="utf-8"))
    calls = {f"{n.func.value.id}.{n.func.attr}" for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)}
    assert "gateway.submit_structured" in calls
    # the chat call sits inside the function the gateway runs, after its consent check and redaction
    run_fns = [f for f in ast.walk(tree) if isinstance(f, ast.AsyncFunctionDef) and any(
        isinstance(n, ast.Attribute) and n.attr in CHAT_CALLS for n in ast.walk(f))]
    assert [f.name for f in run_fns] == ["_run_model"]


def test_the_assistant_package_never_touches_urgency_or_review():
    for path in (APP_DIR / "assistant").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not {m for m in imported if m.startswith(("app.rules", "app.case_triage", "app.review_queue", "app.routes.triage"))}, path
        sql = " ".join(n.value.upper() for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str))
        for table in ("TRIAGE_RUNS", "REVIEW_EVENTS", "AI_FIELDS", "ENCOUNTER_SUMMARIES", "CONSENT_EVENTS"):
            assert f"INSERT INTO {table}" not in sql and f"UPDATE {table}" not in sql, (path, table)
        assert not any(isinstance(n, ast.Attribute) and n.attr in ("invoke", "invoke_prompt", "invoke_stream") for n in ast.walk(tree)), path
