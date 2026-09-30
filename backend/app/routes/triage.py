from fastapi import APIRouter, Depends

from app.auth import Principal, Role, require_roles
from app.rules import TriageInput, TriageResult, evaluate_triage

router = APIRouter(prefix="/triage", tags=["triage"])


@router.post("/process", response_model=TriageResult)
async def process(
    body: TriageInput,
    _: Principal = Depends(require_roles(Role.ANM, Role.MEDICAL_OFFICER)),
) -> TriageResult:
    """Deterministic triage (rules only, no LLM). Stateless until persistence lands with the intake flow."""
    return evaluate_triage(body)
