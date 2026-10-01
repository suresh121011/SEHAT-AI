"""Each language's voice_cloud wording must state the same material facts (docs/13). This checks that
the facts are present, not that a translation is correct: hi/or remain unreviewed drafts."""

import pytest

from app.consent_notice import get_notice

# Per language: provider name, default retention number, training, outside India, over the internet.
FACTS = {
    "en": ("Sarvam", "30", "train", "outside India", "internet"),
    "hi": ("Sarvam", "30", "प्रशिक्ष", "भारत के बाहर", "इंटरनेट"),
    "or": ("Sarvam", "30", "ତାଲିମ", "ଭାରତ ବାହାରେ", "ଇଣ୍ଟରନେଟ୍"),
}


@pytest.mark.parametrize("language", ["en", "hi", "or"])
def test_voice_cloud_checkbox_and_paragraph_state_the_same_facts(language):
    notice = get_notice(language)
    paragraph = next(p for p in notice.paragraphs if "Sarvam" in p)
    for text in (notice.purposes["voice_cloud"], paragraph):
        for fact in FACTS[language]:
            assert fact in text, (language, fact)


def test_translations_stay_marked_unreviewed():
    assert get_notice("hi").review_status == get_notice("or").review_status == "draft_unreviewed_translation"
    assert len({get_notice(lang).version for lang in ("en", "hi", "or")}) == 1


# Documents (Phase 5): what is kept, that it is read locally and not sent out, that the page may show the name.
DOC_FACTS = {
    "en": ("picture of each page", "text read from it", "not sent to any outside company", "name"),
    "hi": ("तस्वीर", "पाठ", "नहीं भेजा", "नाम"),
    "or": ("ଛବି", "ପାଠ୍ୟ", "ପଠାଯାଏ ନାହିଁ", "ନାମ"),
}


@pytest.mark.parametrize("language", ["en", "hi", "or"])
def test_document_paragraph_states_the_same_facts(language):
    notice = get_notice(language)
    marker = {"en": "lab report", "hi": "लैब रिपोर्ट", "or": "ଲ୍ୟାବ୍ ରିପୋର୍ଟ"}[language]
    paragraph = next(p for p in notice.paragraphs if marker in p)
    for fact in DOC_FACTS[language]:
        assert fact in paragraph, (language, fact)


def test_current_notice_discloses_documents():
    from app.consent_notice import DOCUMENT_NOTICE_VERSIONS, NOTICE_VERSION

    assert NOTICE_VERSION in DOCUMENT_NOTICE_VERSIONS
