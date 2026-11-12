"""Protocol evidence for fired rules (docs/23). Takes rule ids only, never case or patient data, so it is open to
staff roles and carries nothing about a patient. Patients are refused."""

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.auth import Principal, Role, require_roles
from app.evidence import corpus, gate
from app.rules.sources import SOURCES

router = APIRouter(tags=["evidence"])

_staff = require_roles(Role.ANM, Role.MEDICAL_OFFICER, Role.SUPERVISOR)
_IDS = r"^[A-Z0-9_]{3,64}(,[A-Z0-9_]{3,64}){0,49}$"
NOTE = ("Protocol passages for the rules that fired. A passage counts as checked only after a named reviewer checked it "
        "against the source; until then the source and the location as cited are shown. Nothing here sets or changes urgency.")


@router.get("/evidence/rules")
async def evidence_for_rules(ids: str = Query(..., pattern=_IDS), principal: Principal = Depends(_staff)) -> dict[str, Any]:
    catalogue = corpus.rule_catalogue()
    by_rule = corpus.load().by_rule
    rules = []
    for rule_id in dict.fromkeys(ids.split(",")):
        if rule_id not in catalogue:
            rules.append({"rule_id": rule_id, "known": False, "source": None, "passages": []})
            continue
        source_id = catalogue[rule_id][0]
        s = SOURCES[source_id]
        rules.append({"rule_id": rule_id, "known": True,
                      "source": {"id": s.id, "citation": s.citation, "reference": s.reference, "version": s.version},
                      "passages": [gate.view(p, source_id) for p in by_rule.get(rule_id, [])]})
    return {"rules": rules, "note": NOTE}
