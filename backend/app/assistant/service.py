"""The fenced clinical assistant (docs/24; plan steps 3-4; owner decisions K1, K3, K6 of 2026-11-12).

    1. Access (a medical officer with access to the visit, facility scope), triage and AI-assistance consent.
    2. Azure must be available; otherwise the assistant says so. It never falls back to another model or a fake answer.
    3. A stored answer is reused while the record, the last stored summary, the model and the prompt are unchanged.
    4. Otherwise through the privacy gateway (app/privacy/gateway.py `submit_structured`): consent captured, the fixed
       question redacted, consent re-checked, then `_run_model`: Semantic Kernel with this visit's read-only tools,
       limited to the question's tools, at most MAX_TOOL_CALLS calls in MAX_ROUNDS tool rounds (plus one final request
       without tools, so at most MAX_ROUNDS + 1 model requests), within TIMEOUT_S. Every tool output is checked for
       identifier patterns before the model sees it; a hit stops the run (PolicyBlocked, nothing released).
    5. Inside the gateway's final transaction, after consent is re-checked: the answer gate, an append-only run row, and
       an audit event holding ids and counts only.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import aiosqlite
import anyio
from semantic_kernel import Kernel
from semantic_kernel.connectors.ai.function_choice_behavior import FunctionChoiceBehavior
from semantic_kernel.connectors.ai.open_ai import AzureChatPromptExecutionSettings
from semantic_kernel.contents import AuthorRole, ChatHistory, FunctionCallContent
from semantic_kernel.filters import FilterTypes
from semantic_kernel.functions import FunctionResult

from app import audit, consent
from app.ai import azure_chat
from app.ai import encounter_summary as es
from app.assistant import gate, prompts
from app.assistant.questions import QUESTIONS, Question
from app.auth import Principal
from app.config import Settings, get_settings
from app.database import transaction
from app.errors import ApiError
from app.privacy import gateway
from app.privacy.pii import residual_hit
from app.services.visit_tools import PLUGIN_NAME, TOOL_NAMES, Citable, VisitTools

PROMPT_VERSION = prompts.PROMPT_VERSION
SERVICE_ID = "sehat-ai-assistant"
MAX_TOOL_CALLS = 8
MAX_ROUNDS = 4
MAX_REFUSED = 16
TIMEOUT_S = 45.0
NOTICE = ("Written by an AI model from this visit's record and checked sentence by sentence against the items it cites; sentences that "
          "failed a check were dropped and are counted, not shown. Not a diagnosis. Urgency comes only from the rules engine, and a "
          "clinician decides.")


def chat_service(settings: Settings) -> tuple[Any | None, str | None]:
    """The Azure chat service from the one factory (app/ai/azure_chat.py), or None and the reason code."""
    reason = azure_chat.azure_unavailable_reason(settings)
    if reason is not None:
        return None, reason
    return azure_chat.azure_chat_service(settings, SERVICE_ID), None


@dataclass
class _Run:
    raw: str
    called: list[str]  # chosen by the model
    fetched: list[str]  # fetched by the server (the readable draft), not chosen by the model
    refused: list[str]
    limited: bool
    tokens: dict[str, int | None]
    citable: dict[str, Citable] = field(default_factory=dict)
    terms: frozenset[str] = frozenset()  # what the record names, for the gate; never sent to the model


def _without_ids(output: str) -> str:
    """A tool's output without its citation ids: those are server-made hashes (they can hold long digit runs), not
    record content. What remains is what the identifier check looks at."""
    def strip(x):
        if isinstance(x, dict):
            return {k: strip(v) for k, v in x.items() if k != "id"}
        if isinstance(x, list):
            return [strip(v) for v in x]
        return x
    try:
        return json.dumps(strip(json.loads(output)), ensure_ascii=False)
    except ValueError:
        return output


def _tokens(messages) -> dict[str, int | None]:
    prompt = completion = 0
    seen = False
    for m in messages:
        usage = (getattr(m, "metadata", None) or {}).get("usage")
        if usage is not None:
            seen = True
            prompt += int(getattr(usage, "prompt_tokens", 0) or 0)
            completion += int(getattr(usage, "completion_tokens", 0) or 0)
    return {"prompt": prompt if seen else None, "completion": completion if seen else None}


async def _run_model(service, conn: aiosqlite.Connection, principal: Principal, case_id: str, question: Question) -> _Run:
    """One answer. Runs inside the gateway after its consent check and redaction, with no database lock held."""
    tools = VisitTools(conn, principal, case_id)
    terms = await tools.server_terms()  # server side only: the gate's list of what this record names
    kernel = Kernel()
    kernel.add_service(service)
    kernel.add_plugin(tools, plugin_name=PLUGIN_NAME)  # the visit's read-only tools only: nothing else is on this kernel
    called: list[str] = []
    fetched: list[str] = []
    state = {"limited": False, "identifier": False}

    async def _trace(context, next):  # noqa: A002  (Semantic Kernel passes the continuation by this name)
        if len(called) >= MAX_TOOL_CALLS:  # stop the run: no answer is written after the limit
            state["limited"] = True
            context.terminate = True
            return
        called.append(context.function.name)
        await next(context)
        # The record reaches the model only without identifier patterns. On a hit this output is replaced before the model
        # sees it, the tool loop stops (other calls of the same turn finish first), and the run releases nothing below.
        if residual_hit(_without_ids(str(context.function_result.value))):
            state["identifier"] = True
            context.function_result = FunctionResult(function=context.function.metadata, value='{"withheld": "identifier pattern"}')
            context.terminate = True

    kernel.add_filter(FilterTypes.AUTO_FUNCTION_INVOCATION, _trace)
    allowed = sorted(f"{PLUGIN_NAME}-{t}" for t in question.tools)
    settings = AzureChatPromptExecutionSettings(temperature=0.1, max_tokens=900, response_format=prompts.RESPONSE_FORMAT)
    history = ChatHistory(system_message=prompts.SYSTEM)
    if question.mode == "fixed":  # the readable draft: the server fetches the facts; the model chooses no tool
        facts = await tools.get_visit_facts()
        fetched.append("get_visit_facts")
        if residual_hit(_without_ids(facts)):
            raise gateway.PolicyBlocked("input", "identifier_in_tool_output")
        history.add_user_message(prompts.user_message(question.text, facts))
    else:
        history.add_user_message(prompts.user_message(question.text))
        settings.function_choice_behavior = FunctionChoiceBehavior.Auto(filters={"included_functions": allowed},
                                                                        maximum_auto_invoke_attempts=MAX_ROUNDS)
    with anyio.fail_after(TIMEOUT_S):
        reply = await service.get_chat_message_content(history, settings, kernel=kernel)
    if state["identifier"]:
        raise gateway.PolicyBlocked("input", "identifier_in_tool_output")
    requested = [i.name for m in history.messages for i in m.items if isinstance(i, FunctionCallContent)]
    # A refused name is model-written text: only names from our own list are kept, anything else is "unlisted tool".
    refused = [n.removeprefix(f"{PLUGIN_NAME}-") if n.removeprefix(f"{PLUGIN_NAME}-") in TOOL_NAMES and n.startswith(f"{PLUGIN_NAME}-")
               else "unlisted tool" for n in requested if n not in allowed][:MAX_REFUSED]
    raw = "" if state["limited"] or reply is None or reply.role != AuthorRole.ASSISTANT or not isinstance(reply.content, str) else reply.content
    return _Run(raw=raw, called=called, fetched=fetched, refused=refused, limited=state["limited"],
                tokens=_tokens([*history.messages, reply]), citable=dict(tools.citable), terms=terms)


def _view(row, *, reused: bool, current_sha: str | None = None) -> dict:
    answer, tools = json.loads(row["answer_json"]), json.loads(row["tools_json"])
    return {"run_id": row["run_id"], "question_id": row["question_id"], "question": QUESTIONS[row["question_id"]].text, "status": row["status"],
            "reused": reused, "created_at": row["created_at"], "model": row["model"], "prompt_version": row["prompt_version"],
            "gate_version": row["gate_version"],
            "sentences": answer["kept"], "dropped": answer["dropped"], "tools": tools,
            "tokens": {"prompt": row["prompt_tokens"], "completion": row["completion_tokens"]},
            "is_stale": current_sha is not None and row["inventory_sha256"] != current_sha, "notice": NOTICE}


def _details(row, reused: bool) -> audit.AssistantAnsweredDetails:
    tools = json.loads(row["tools_json"])
    return audit.AssistantAnsweredDetails(run_id=row["run_id"], question_id=row["question_id"], status=row["status"], tool_calls=len(tools["called"]),
                                          refused_calls=len(tools["refused"]), sentences_kept=row["sentences_kept"],
                                          sentences_dropped=row["sentences_dropped"], reused=reused)


async def _summary_basis(conn, case_id: str) -> str | None:
    async with conn.execute("SELECT summary_id FROM encounter_summaries WHERE case_id = ? ORDER BY seq DESC LIMIT 1", (case_id,)) as cur:
        row = await cur.fetchone()
    return row["summary_id"] if row else None


async def _row(conn, run_id: str):
    async with conn.execute("SELECT * FROM assistant_runs WHERE run_id = ?", (run_id,)) as cur:
        return await cur.fetchone()


async def ask(conn: aiosqlite.Connection, principal: Principal, case_id: str, question_id: str, request_id=None) -> dict:
    question = QUESTIONS[question_id]
    service, reason = chat_service(get_settings())
    model = str(service.ai_model_id) if service is not None else None
    denial, stored, sha, basis = None, None, None, None
    try:
        async with transaction(conn):  # access, consent and the reuse lookup in one snapshot
            await consent.load_case(conn, principal, case_id, "triage")
            await consent.require(conn, case_id, "triage")
            await consent.require(conn, case_id, "ai_assist")  # the record goes to a model
            if service is not None:
                sha, _ = await es.inventory(conn, case_id)
                basis = await _summary_basis(conn, case_id)
                async with conn.execute("SELECT * FROM assistant_runs WHERE case_id = ? AND question_id = ? AND inventory_sha256 = ? "
                                        "AND summary_basis IS ? AND model = ? AND prompt_version = ? AND gate_version = ? AND status = 'answered' "
                                        "ORDER BY seq DESC LIMIT 1", (case_id, question_id, sha, basis, model, PROMPT_VERSION, gate.GATE_VERSION)) as cur:
                    stored = await cur.fetchone()
                if stored is not None:  # same record, model, prompt and gate: the stored answer, no new model call (cost)
                    await audit.record(conn, principal=principal, action="assistant_answered", outcome="success", case_id=case_id,
                                       request_id=request_id, details=_details(stored, reused=True))
    except consent.ConsentNotEffective as exc:
        denial = exc
    if denial is not None:
        raise await consent.audit_denied(conn, principal, case_id, denial, request_id)
    if service is None:
        raise ApiError(503, "ASSISTANT_UNAVAILABLE", f"The assistant is not available ({reason}). Nothing was sent to a model, and no other model "
                                                     "or fallback answer is used.")
    if stored is not None:
        return _view(stored, reused=True, current_sha=sha)

    async def run(_prompt) -> _Run:
        return await _run_model(service, conn, principal, case_id, question)

    async def persist(c: aiosqlite.Connection, _prompt, result: _Run, authz_seq: int) -> dict:
        checked = gate.Checked("tool_limit") if result.limited else gate.check(result.raw, result.citable, result.terms)
        run_id, now = str(uuid.uuid4()), datetime.now(timezone.utc).isoformat(timespec="microseconds")
        await c.execute(
            "INSERT INTO assistant_runs (run_id, case_id, question_id, status, model, prompt_version, gate_version, inventory_sha256, summary_basis, "
            "tools_json, answer_json, sentences_kept, sentences_dropped, prompt_tokens, completion_tokens, consent_seq, actor_id, actor_role, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, case_id, question_id, checked.status, model, PROMPT_VERSION, gate.GATE_VERSION, sha, basis,
             json.dumps({"called": result.called, "fetched_by_server": result.fetched, "refused": result.refused}),
             json.dumps({"kept": checked.kept, "dropped": checked.dropped}), len(checked.kept), len(checked.dropped),
             result.tokens["prompt"], result.tokens["completion"], authz_seq, principal.user_id, principal.role.value, now))
        row = await _row(c, run_id)
        await audit.record(c, principal=principal, action="assistant_answered", outcome="success", case_id=case_id, request_id=request_id,
                           details=_details(row, reused=False))
        return _view(row, reused=False, current_sha=sha)

    return await gateway.submit_structured(conn, principal, case_id, [("question", question.text)], run, persist, request_id)


async def view(conn: aiosqlite.Connection, principal: Principal, case_id: str, request_id=None) -> dict:
    """The questions, whether the assistant is available, and the latest stored answer to each question."""
    denial = None
    answers: list[dict] = []
    try:
        async with transaction(conn):
            await consent.load_case(conn, principal, case_id, "triage")
            await consent.require(conn, case_id, "triage")
            ai_ok = (await consent.snapshot(conn, case_id)).is_effective("ai_assist")
            if ai_ok:  # answers were written from AI-assisted data: hidden while that consent is not in effect
                sha, _ = await es.inventory(conn, case_id)
                async with conn.execute("SELECT * FROM assistant_runs WHERE seq IN (SELECT MAX(seq) FROM assistant_runs WHERE case_id = ? "
                                        "GROUP BY question_id) ORDER BY seq DESC", (case_id,)) as cur:
                    answers = [_view(r, reused=False, current_sha=sha) for r in await cur.fetchall()]
    except consent.ConsentNotEffective as exc:
        denial = exc
    if denial is not None:
        raise await consent.audit_denied(conn, principal, case_id, denial, request_id)
    service, reason = chat_service(get_settings())
    return {"available": service is not None, "reason": reason, "model": str(service.ai_model_id) if service is not None else None,
            "questions": [{"id": k, "text": q.text} for k, q in QUESTIONS.items()], "answers": answers, "consent_needed": not ai_ok,
            "limits": {"max_tool_calls": MAX_TOOL_CALLS, "max_sentences": gate.MAX_SENTENCES, "timeout_s": TIMEOUT_S}, "notice": NOTICE}
