"""Clinical source registry. Every rule cites exactly one of these IDs.

Details, access dates and verification notes: docs/10_Safety_Rules_Engine.md §Clinical sources.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    id: str
    citation: str
    reference: str
    version: str


SOURCES: dict[str, Source] = {
    s.id: s
    for s in (
        Source(
            "ATP_2022",
            "Singh SK, Sahu AK, et al. Prospective Validation of a Novel Triage System Developed in a Middle "
            "Income Country - AIIMS Triage Protocol. J Emerg Trauma Shock 2022;15(3):124-7, Supplementary Table 1",
            "doi:10.4103/jets.jets_146_21 (PMC9639733)",
            "2022",
        ),
        Source(
            "ATP_2020",
            "Sahu AK, Bhoi S, et al. All India Institute of Medical Sciences Triage Protocol (ATP): ATP of a Busy "
            "Emergency Department. J Emerg Trauma Shock 2020;13(2):107-9",
            "doi:10.4103/JETS.JETS_137_19",
            "2020",
        ),
        Source(
            "RCP_NEWS2_2017",
            "Royal College of Physicians. National Early Warning Score (NEWS) 2, Chart 1 (scoring) and Chart 2 "
            "(thresholds and triggers)",
            "https://www.rcp.ac.uk/resources/national-early-warning-score-news-2/",
            "2017",
        ),
        Source(
            "SEPSIS3_2016",
            "Singer M, et al. The Third International Consensus Definitions for Sepsis and Septic Shock (Sepsis-3). "
            "JAMA 2016;315(8):801-10 (qSOFA)",
            "doi:10.1001/jama.2016.0287",
            "2016",
        ),
        Source(
            "WHO_HB_2024",
            "WHO. Guideline on haemoglobin cutoffs to define anaemia in individuals and populations",
            "https://www.who.int/publications/i/item/9789240088542",
            "2024",
        ),
        Source(
            "MOHFW_MCP",
            "MoHFW/MoWCD Mother and Child Protection Card; NHSRC Guidebook for Multi Purpose Workers (Female) - "
            "danger signs during pregnancy",
            "https://nhsrcindia.org/sites/default/files/2021-06/Guidebook%20for%20Enhancing%20Performance%20of%20Multi%20Purpose%20Workers%20Female%20-%20English.pdf",
            "2018/2021",
        ),
        Source(
            "IHCI_HTN",
            "India Hypertension Control Initiative treatment protocol / NHM Standard Treatment Guidelines: "
            "Hypertension (BP >180/110: assess acute target organ damage, refer immediately)",
            "https://nhm.gov.in/images/pdf/guidelines/nrhm-guidelines/stg/Hypertension_full.pdf",
            "2019",
        ),
        Source(
            "NPCDCS_CBAC",
            "NPCDCS Community Based Assessment Checklist (score >4: prioritise for NCD screening)",
            "http://namayush.gov.in/sites/default/files/doc/Community_based_assessment_checklist_(CBAC)_form.pdf",
            "NPCDCS",
        ),
        Source(
            "WHO_ILI_2014",
            "WHO surveillance case definitions for ILI and SARI (ILI: measured fever >=38 C and cough, onset "
            "within the last 10 days)",
            "https://cdn.who.int/media/docs/default-source/influenza/who_ili_sari_case_definitions_2014.pdf",
            "2014",
        ),
        Source(
            "OSHA_1910_95",
            "29 CFR 1910.95(g)(10) Standard threshold shift (US OSHA; no Indian numeric equivalent located)",
            "https://www.osha.gov/laws-regs/regulations/standardnumber/1910/1910.95",
            "current",
        ),
        Source(
            "SEHAT_POLICY",
            "SEHAT AI project safety convention (not a clinical guideline); see docs/10 decision record",
            "docs/10_Safety_Rules_Engine.md",
            "1.0",
        ),
    )
}


def citation(source_id: str) -> str:
    return SOURCES[source_id].citation
