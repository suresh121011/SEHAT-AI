"""Static boundary checks: no path to an LLM adapter except the privacy gateway (docs/11 §A)."""

import ast
from pathlib import Path

import app
from app.main import create_app

APP_DIR = Path(app.__file__).parent


def _modules():
    for path in APP_DIR.rglob("*.py"):
        yield path.relative_to(APP_DIR).as_posix(), ast.parse(path.read_text())


def test_only_privacy_package_calls_adapter_complete_or_imports_adapters():
    offenders = []
    for rel, tree in _modules():
        if rel.startswith("privacy/"):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ("complete", "_complete"):
                offenders.append(f"{rel}: .{node.attr}")
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app.privacy.adapters"):
                offenders.append(f"{rel}: imports adapters")
    assert offenders == []


def test_only_kernel_smoke_test_invokes_the_llm():
    offenders = []
    for rel, tree in _modules():
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ("invoke_prompt", "invoke", "invoke_stream", "invoke_prompt_stream"):
                if rel != "services/kernel.py":
                    offenders.append(f"{rel}: .{node.attr}")
    assert offenders == []


def _unconstrained_strings(schema: dict, components: dict, path: str, seen: set[str]) -> list[str]:
    if "$ref" in schema:
        name = schema["$ref"].split("/")[-1]
        if name in seen:
            return []
        return _unconstrained_strings(components[name], components, path, seen | {name})
    found: list[str] = []
    for key in ("anyOf", "oneOf", "allOf"):
        for sub in schema.get(key, []):
            found += _unconstrained_strings(sub, components, path, seen)
    if schema.get("type") == "object":
        for prop, sub in schema.get("properties", {}).items():
            found += _unconstrained_strings(sub, components, f"{path}.{prop}", seen)
        if isinstance(schema.get("additionalProperties"), dict):
            found += _unconstrained_strings(schema["additionalProperties"], components, f"{path}.*", seen)
    if schema.get("type") == "array" and "items" in schema:
        found += _unconstrained_strings(schema["items"], components, f"{path}[]", seen)
    if schema.get("type") == "string" and not any(k in schema for k in ("pattern", "maxLength", "enum", "const", "format")):
        found.append(path)
    return found


def test_no_route_accepts_free_text_for_ai():
    """Every JSON request body is structured: no unconstrained string field could carry free text
    (and therefore raw PII) into the system. Checked via the generated OpenAPI schema."""
    spec = create_app().openapi()
    components = spec.get("components", {}).get("schemas", {})
    bodies = 0
    offenders: list[str] = []
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            content = op.get("requestBody", {}).get("content", {}).get("application/json")
            if content:
                bodies += 1
                offenders += _unconstrained_strings(content["schema"], components, f"{method.upper()} {path}", set())
    assert bodies >= 5  # the check really inspected the request bodies
    assert offenders == []


def test_rules_engine_has_no_privacy_or_consent_imports():
    for path in (APP_DIR / "rules").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith(("app.privacy", "app.consent", "app.audit", "app.database", "presidio", "spacy")), path


def test_only_non_json_body_is_constrained_voice_audio():
    """Phase 4: the voice upload is the only non-JSON request body. It is raw audio/wav (no multipart
    form fields that could carry free text), and its query parameters are enums or UUIDs."""
    spec = create_app().openapi()
    non_json = []
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            content = op.get("requestBody", {}).get("content", {})
            for ctype in content:
                if ctype != "application/json":
                    non_json.append((method.upper(), path, ctype))
                    for param in op.get("parameters", []):
                        if param["in"] == "header":
                            continue  # auth cross-check headers (app/auth.py), common to every route
                        schema = param.get("schema", {})
                        assert "enum" in schema or schema.get("format") == "uuid", (path, param["name"])
    assert non_json == [("POST", "/api/v1/cases/{case_id}/voice/transcriptions", "audio/wav")]
