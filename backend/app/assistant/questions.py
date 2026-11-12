"""The closed list of questions a medical officer may ask (owner decision K3, 2026-11-12): buttons, never a typed
question, so no free text reaches the model and the request carries an id only (docs/24 §2).

Each question names the visit tools the model may call for it; the kernel is offered those tools and refuses any
other. "summary" is the readable draft (plan step 3): a fixed sequence in which the server fetches the facts and the
model chooses no tools.
"""

from dataclasses import dataclass
from typing import Literal

QuestionId = Literal["summary", "check_first", "missing", "changed"]


@dataclass(frozen=True)
class Question:
    text: str
    mode: Literal["fixed", "tools"]
    tools: tuple[str, ...]


QUESTIONS: dict[str, Question] = {
    "summary": Question("Summarise this visit in plain sentences.", "fixed", ("get_visit_facts",)),
    "check_first": Question("What should I check first?", "tools",
                            ("get_visit_facts", "list_missing_information", "list_conflicts", "list_waiting_for_check", "get_urgency_record")),
    "missing": Question("What is missing or unclear?", "tools",
                        ("list_missing_information", "list_conflicts", "list_waiting_for_check", "suggest_follow_up_questions", "get_visit_facts")),
    "changed": Question("What changed since the last summary?", "tools", ("list_changes_since_last_summary", "get_visit_facts")),
}
