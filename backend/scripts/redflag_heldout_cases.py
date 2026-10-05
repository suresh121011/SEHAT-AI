"""Held-out SYNTHETIC red-flag phrasings for the keyword suggester (docs/16 §2c). Written on 2026-10-05 BEFORE the
keyword rules in app/ai/redflag_keywords.py, and not edited afterwards to make them pass. Same author as the rules,
so this is a smoke check against the author's own blind spots only — not an independent or clinical evaluation.

(id, text, expected ATP flags). Negative cases (empty set) include negation traps and look-alike phrases.
"""

HELDOUT = [
    ("h01", "Bitten by a snake near the paddy field about two hours back.", {"poisoning_envenomation"}),
    ("h02", "Drank pesticide after a family fight, vomiting since then.", {"poisoning_envenomation"}),
    ("h03", "Nurse got a needlestick injury while recapping a syringe.", {"needle_prick_injury"}),
    ("h04", "Has not passed urine since yesterday evening, belly is tight.", {"urinary_retention"}),
    ("h05", "Severe headache came on suddenly while lifting a bucket.", {"sudden_headache", "severe_pain"}),
    ("h06", "Passed out in the kitchen and was unconscious for a few seconds.", {"syncope"}),
    ("h07", "Child is having convulsions right now, eyes rolling up.", {"active_seizure"}),
    ("h08", "Crushing pain in the chest since one hour, sweating.", {"chest_pain_acute_24h"}),
    ("h09", "Left side of face drooping and left arm weak since 2 hours.", {"stroke_suspected_24h", "limb_weakness_24h"}),
    ("h10", "Suddenly short of breath this morning, can only speak in single words.", {"sob_acute_12h", "incomplete_sentences"}),
    ("h11", "Vomiting blood twice since morning.", {"active_bleeding"}),
    ("h12", "Tongue swollen and rash all over after taking an injection.", {"angioedema_face", "allergic_reaction"}),
    ("h13", "Road traffic accident, hit by a truck while crossing.", {"dangerous_mechanism_trauma"}),
    ("h14", "Right foot is cold and pale since last night, very painful.", {"limb_ischaemia_48h"}),
    ("h15", "Boy of 15 with sudden testicular pain since 3 hours.", {"scrotal_pain_young_male"}),
    ("h16", "Denies fever but unable to pass urine for a day.", {"urinary_retention"}),
    ("h17", "Noisy breathing with stridor, drooling.", {"stridor"}),
    ("h18", "Very aggressive and violent, threatening staff.", {"agitated_violent"}),
    # Negatives: negation traps, history, look-alikes.
    ("n01", "No chest pain. No breathlessness. Came for sugar check.", set()),
    ("n02", "Denies fainting or seizures. Mild cold for 2 days.", set()),
    ("n03", "History of snake bite 5 years ago, now here for a knee pain.", set()),
    ("n04", "Patient denies vomiting blood. Burning in stomach after meals.", set()),
    ("n05", "Passing urine normally. No swelling of lips or face.", set()),
    ("n06", "Sprained wrist while playing cricket, pain is mild.", set()),
]
