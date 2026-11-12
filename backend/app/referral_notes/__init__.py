"""Referral closure tracking (docs/21; architecture §16; docs/06 §2.5, §3.8, §4): a referral packet built from a
signed-off triage run, its status tracked to closure in append-only events, an overdue flag after 48 hours without an
update, a printable letter and a FHIR R4 export. New paths only: the unregistered work-in-progress router
`app/routes/referrals.py` and the legacy `referrals` table are neither used nor changed."""
