"""Input and output schemas for the deterministic triage engine.

Units are fixed (°C, mmHg, /min, %). Physiologically impossible values are rejected, never coerced.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.rules.urgency import Urgency


class Scenario(str, Enum):
    """The seven SEHAT scenarios (docs/02 §Must-Have, PRD FR-2.4)."""

    OPD = "opd"
    MATERNAL = "maternal"
    CHRONIC_NCD = "chronic_ncd"
    HEALTH_CAMP = "health_camp"
    CAMPUS_FEVER = "campus_fever"
    OCCUPATIONAL = "occupational"
    REFERRAL = "referral"


class AtpFlag(str, Enum):
    """Non-numeric ATP RED criteria (ATP_2022 Supplementary Table 1), one per computable row."""

    # Altered physiology
    STRIDOR = "stridor"
    ANGIOEDEMA_FACE = "angioedema_face"
    ACTIVE_SEIZURE = "active_seizure"
    INCOMPLETE_SENTENCES = "incomplete_sentences"
    AUDIBLE_WHEEZE = "audible_wheeze"
    ACTIVE_BLEEDING = "active_bleeding"
    # Time-sensitive conditions
    CHEST_PAIN_ACUTE_24H = "chest_pain_acute_24h"
    LIMB_WEAKNESS_24H = "limb_weakness_24h"
    STROKE_SUSPECTED_24H = "stroke_suspected_24h"
    DANGEROUS_MECHANISM_TRAUMA = "dangerous_mechanism_trauma"
    SOB_ACUTE_12H = "sob_acute_12h"
    LIMB_ISCHAEMIA_48H = "limb_ischaemia_48h"
    ALLERGIC_REACTION = "allergic_reaction"
    SCROTAL_PAIN_YOUNG_MALE = "scrotal_pain_young_male"
    SEVERE_PAIN = "severe_pain"
    SUDDEN_ABDOMINAL_PAIN = "sudden_abdominal_pain"
    SUDDEN_HEADACHE = "sudden_headache"
    URINARY_RETENTION = "urinary_retention"
    FEVER_IMMUNOCOMPROMISED = "fever_immunocompromised"
    OUTSIDE_EVAL_TIME_SENSITIVE = "outside_eval_time_sensitive"
    SYNCOPE = "syncope"
    NEEDLE_PRICK_INJURY = "needle_prick_injury"
    # Other conditions with increased urgency
    ABD_PAIN_WITH_VAGINAL_BLEEDING = "abd_pain_with_vaginal_bleeding"
    AGITATED_VIOLENT = "agitated_violent"
    POISONING_ENVENOMATION = "poisoning_envenomation"
    THIRD_TRIMESTER_PAIN_OR_BLEEDING = "third_trimester_pain_or_bleeding"


class MaternalDangerSign(str, Enum):
    """Danger signs during pregnancy (MOHFW_MCP / NHSRC MPW-F guidebook)."""

    ANAEMIA_SYMPTOMS = "anaemia_symptoms"  # palpitations, easy fatigability, breathlessness at rest
    EXCESSIVE_VOMITING = "excessive_vomiting"
    HIGH_FEVER = "high_fever"
    HEADACHE_BLURRED_VISION = "headache_blurred_vision"
    CONVULSIONS = "convulsions"
    SWELLING_ALL_OVER = "swelling_all_over"
    PAINFUL_URINATION = "painful_urination"
    FOUL_DISCHARGE = "foul_discharge"
    HIGH_BLOOD_PRESSURE = "high_blood_pressure"
    LEAKING_OVER_24H = "leaking_over_24h"
    VAGINAL_BLEEDING = "vaginal_bleeding"
    REDUCED_FETAL_MOVEMENT = "reduced_fetal_movement"


Consciousness = Literal["A", "C", "V", "P", "U"]  # ACVPU (RCP_NEWS2_2017)


def _exact_int(v: Any) -> Any:
    """Literal[1, 2, ...] would accept True or 3.0 (they compare equal); clinical codes must be real ints."""
    if v is not None and (isinstance(v, bool) or not isinstance(v, int)):
        raise ValueError("must be an integer")
    return v


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Vitals(_Strict):
    model_config = ConfigDict(extra="forbid", strict=True)

    resp_rate: int | None = Field(default=None, ge=0, le=80, description="breaths/min")
    spo2: int | None = Field(default=None, ge=0, le=100, description="%")
    on_supplemental_oxygen: bool | None = None
    spo2_scale: Literal[1, 2] = Field(default=1, description="NEWS2 SpO2 Scale 2 only if prescribed for hypercapnic respiratory failure")
    pulse: int | None = Field(default=None, ge=0, le=300, description="beats/min")
    sbp: int | None = Field(default=None, ge=0, le=300, description="mmHg")
    dbp: int | None = Field(default=None, ge=0, le=200, description="mmHg")
    temp_c: float | None = Field(default=None, ge=25.0, le=45.0, description="°C")
    consciousness: Consciousness | None = None
    gcs: int | None = Field(default=None, ge=3, le=15)

    _scale_is_int = field_validator("spo2_scale", mode="before")(_exact_int)

    # temp_c is deliberately not rounded: rounding 39.04 to 39.0 would hide ATP "temperature >39 °C".

    @model_validator(mode="after")
    def _consistent(self) -> "Vitals":
        if self.sbp is not None and self.dbp is not None and self.dbp >= self.sbp:
            raise ValueError("dbp must be lower than sbp")
        if self.consciousness == "A" and self.gcs is not None and self.gcs < 15:
            raise ValueError("consciousness 'A' (alert) contradicts GCS < 15; record C/V/P/U")
        return self


class BpReading(_Strict):
    model_config = ConfigDict(extra="forbid", strict=True)

    sbp: int = Field(ge=0, le=300)
    dbp: int = Field(ge=0, le=200)

    @model_validator(mode="after")
    def _dbp_below_sbp(self) -> "BpReading":
        if self.dbp >= self.sbp:
            raise ValueError("dbp must be lower than sbp")
        return self


class MaternalData(_Strict):
    danger_sign_screen_completed: bool = Field(default=False, strict=True)
    danger_signs: list[MaternalDangerSign] = Field(default_factory=list)
    hb_g_dl: float | None = Field(default=None, ge=2.0, le=20.0, strict=True)


class ChronicNcdData(_Strict):
    bp_readings: list[BpReading] = Field(default_factory=list, max_length=10)


class HealthCampData(_Strict):
    cbac_score: int | None = Field(default=None, ge=0, le=30, strict=True)


class CampusFeverData(_Strict):
    cough: bool | None = Field(default=None, strict=True)
    onset_days: int | None = Field(default=None, ge=0, le=365, strict=True)


class OccupationalData(_Strict):
    sts_db_avg_2_3_4khz: float | None = Field(default=None, ge=-30, le=120, strict=True, description="shift vs baseline audiogram")


class ReferralData(_Strict):
    escort_arranged: bool | None = Field(default=None, strict=True)
    transport_arranged: bool | None = Field(default=None, strict=True)
    identity_confirmed: bool | None = Field(default=None, strict=True)
    consent_taken: bool | None = Field(default=None, strict=True)


_SCENARIO_BLOCKS = {
    Scenario.MATERNAL: "maternal",
    Scenario.CHRONIC_NCD: "chronic_ncd",
    Scenario.HEALTH_CAMP: "health_camp",
    Scenario.CAMPUS_FEVER: "campus_fever",
    Scenario.OCCUPATIONAL: "occupational",
    Scenario.REFERRAL: "referral",
}


class TriageInput(_Strict):
    scenario: Scenario
    age_years: int | None = Field(default=None, ge=0, le=120, strict=True)
    pregnant: bool = Field(default=False, strict=True)
    trimester: Literal[1, 2, 3] | None = None
    vitals: Vitals = Field(default_factory=Vitals)
    red_flag_screen_completed: bool = Field(default=False, strict=True)
    red_flags_present: list[AtpFlag] = Field(default_factory=list)
    suspected_infection: bool = Field(default=False, strict=True)

    maternal: MaternalData | None = None
    chronic_ncd: ChronicNcdData | None = None
    health_camp: HealthCampData | None = None
    campus_fever: CampusFeverData | None = None
    occupational: OccupationalData | None = None
    referral: ReferralData | None = None

    _trimester_is_int = field_validator("trimester", mode="before")(_exact_int)

    @model_validator(mode="after")
    def _consistent(self) -> "TriageInput":
        own = _SCENARIO_BLOCKS.get(self.scenario)
        for block in _SCENARIO_BLOCKS.values():
            if block != own and getattr(self, block) is not None:
                raise ValueError(f"'{block}' data is not valid for scenario '{self.scenario.value}'")
        if self.scenario == Scenario.MATERNAL and not self.pregnant:
            raise ValueError("maternal scenario requires pregnant=true")
        if self.trimester is not None and not self.pregnant:
            raise ValueError("trimester requires pregnant=true")
        return self


# ── Output ────────────────────────────────────────────────────


class EvidenceItem(BaseModel):
    value: Any
    threshold: str


class TriggeredRule(BaseModel):
    rule_id: str
    family: str
    urgency: Urgency
    reason: str
    source_id: str
    source: str
    evidence: dict[str, EvidenceItem]


class Advisory(BaseModel):
    code: str
    message: str
    source_id: str


class ScoreComponent(BaseModel):
    value: Any
    points: int | None


class News2Result(BaseModel):
    status: Literal["complete", "incomplete", "not_applicable"]
    reason: str | None = None
    total: int | None = None  # only when complete
    partial_total: int | None = None  # lower bound from available parameters when incomplete
    band: Literal["low", "low_medium", "medium", "high"] | None = None
    components: dict[str, ScoreComponent] = Field(default_factory=dict)


class QsofaResult(BaseModel):
    status: Literal["complete", "incomplete", "not_applicable"]
    reason: str | None = None
    total: int | None = None
    positive: bool | None = None
    components: dict[str, ScoreComponent] = Field(default_factory=dict)
    note: str = "qSOFA is a risk prompt, not a diagnosis of sepsis (SEPSIS3_2016)"


class Scores(BaseModel):
    news2: News2Result
    qsofa: QsofaResult


class TriageResult(BaseModel):
    urgency: Urgency
    determination: Literal["complete", "insufficient_data", "outside_validated_population"]
    needs_human_review: bool
    scenario: Scenario
    triggered_rules: list[TriggeredRule]
    scores: Scores
    missing_fields: list[str]
    advisories: list[Advisory]
    engine_version: str
    ruleset_version: str
    evaluated_at: datetime
    disclaimer: str = "Non-diagnostic decision support. A named human reviewer must sign off."


class FinalUrgency(BaseModel):
    deterministic_urgency: Urgency
    suggested_urgency: Urgency | None
    final_urgency: Urgency
    override_applied: bool
    override_reason: str | None
