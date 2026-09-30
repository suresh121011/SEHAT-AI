"""Versioned consent notice (docs/11 §K). Served by the backend so the UI shows exactly what the
consent record references.

Review status is recorded with every consent event:
- en: `project_draft` — written by the project team; not legally reviewed.
- hi, or: `draft_unreviewed_translation` — machine-assisted drafts; need native-speaker review
  before any real use. The UI shows a warning banner for these.
Language support here is for *displaying the notice only*; AI-assisted text processing accepts
English text only in this prototype.
"""

from typing import Literal

from pydantic import BaseModel

NOTICE_VERSION = "2026-09-30.1"

Language = Literal["en", "hi", "or"]
Purpose = Literal["triage", "ai_assist"]


class Notice(BaseModel):
    language: Language
    version: str
    review_status: Literal["project_draft", "draft_unreviewed_translation"]
    title: str
    paragraphs: list[str]
    purposes: dict[Purpose, str]


_NOTICES: dict[str, Notice] = {
    "en": Notice(
        language="en",
        version=NOTICE_VERSION,
        review_status="project_draft",
        title="Consent for triage support — SEHAT AI (research prototype)",
        paragraphs=[
            "This is a research prototype, not a medical device. It does not diagnose illness. A doctor or nurse reviews every result.",
            "What we collect: your symptoms, basic measurements such as temperature and blood pressure, and your answers to health questions for this visit. We do not collect your name or ID numbers in this step.",
            "Why: to decide how urgently you need care, and to share this with the health worker and doctor treating you.",
            "Optional AI assistance: a computer program may draft a summary of your symptoms. Names and numbers are removed first, but this removal is not perfect. You can say no to this part.",
            "No voice recording is stored. Your choice is recorded with the date and time.",
            "You can withdraw at any time by telling the health worker. Withdrawing stops further processing; information already recorded is kept for now.",
            "You can say no. The health worker can still assess you without this system.",
        ],
        purposes={
            "triage": "Use my health information for triage and share it with the health worker and doctor treating me.",
            "ai_assist": "Also allow AI assistance to draft a summary (names and numbers removed first; English text only in this prototype).",
        },
    ),
    "hi": Notice(
        language="hi",
        version=NOTICE_VERSION,
        review_status="draft_unreviewed_translation",
        title="ट्राइएज सहायता के लिए सहमति — SEHAT AI (शोध प्रोटोटाइप)",
        paragraphs=[
            "यह एक शोध प्रोटोटाइप है, कोई चिकित्सा उपकरण नहीं। यह बीमारी का निदान नहीं करता। हर परिणाम की जाँच डॉक्टर या नर्स करते हैं।",
            "हम क्या लेते हैं: इस बार के आपके लक्षण, तापमान और रक्तचाप जैसी बुनियादी माप, और स्वास्थ्य प्रश्नों के आपके उत्तर। इस चरण में हम आपका नाम या पहचान नंबर नहीं लेते।",
            "क्यों: यह तय करने के लिए कि आपको कितनी जल्दी इलाज चाहिए, और यह जानकारी आपका इलाज करने वाले स्वास्थ्य कर्मी और डॉक्टर के साथ साझा करने के लिए।",
            "वैकल्पिक AI सहायता: एक कंप्यूटर प्रोग्राम आपके लक्षणों का सारांश तैयार कर सकता है। पहले नाम और नंबर हटा दिए जाते हैं, लेकिन यह हटाना पूरी तरह सटीक नहीं है। आप इस भाग के लिए मना कर सकते हैं।",
            "कोई आवाज़ रिकॉर्डिंग संग्रहीत नहीं की जाती। आपकी पसंद तारीख और समय के साथ दर्ज की जाती है।",
            "आप कभी भी स्वास्थ्य कर्मी को बताकर सहमति वापस ले सकते हैं। वापस लेने पर आगे की प्रक्रिया रुक जाती है; जो जानकारी पहले से दर्ज है वह अभी रखी जाती है।",
            "आप मना कर सकते हैं। स्वास्थ्य कर्मी इस प्रणाली के बिना भी आपकी जाँच कर सकते हैं।",
        ],
        purposes={
            "triage": "मेरी स्वास्थ्य जानकारी का उपयोग ट्राइएज के लिए करें और इसे मेरा इलाज करने वाले स्वास्थ्य कर्मी और डॉक्टर के साथ साझा करें।",
            "ai_assist": "AI सहायता से सारांश तैयार करने की भी अनुमति दें (पहले नाम और नंबर हटाए जाएँगे; इस प्रोटोटाइप में केवल अंग्रेज़ी पाठ)।",
        },
    ),
    "or": Notice(
        language="or",
        version=NOTICE_VERSION,
        review_status="draft_unreviewed_translation",
        title="ଟ୍ରାଏଜ୍ ସହାୟତା ପାଇଁ ସମ୍ମତି — SEHAT AI (ଗବେଷଣା ପ୍ରୋଟୋଟାଇପ୍)",
        paragraphs=[
            "ଏହା ଏକ ଗବେଷଣା ପ୍ରୋଟୋଟାଇପ୍, କୌଣସି ଚିକିତ୍ସା ଉପକରଣ ନୁହେଁ। ଏହା ରୋଗ ନିର୍ଣ୍ଣୟ କରେ ନାହିଁ। ପ୍ରତ୍ୟେକ ଫଳାଫଳ ଡାକ୍ତର କିମ୍ବା ନର୍ସ ଯାଞ୍ଚ କରନ୍ତି।",
            "ଆମେ କ'ଣ ସଂଗ୍ରହ କରୁ: ଏହି ଥର ଆପଣଙ୍କ ଲକ୍ଷଣ, ତାପମାତ୍ରା ଓ ରକ୍ତଚାପ ଭଳି ମୌଳିକ ମାପ, ଏବଂ ସ୍ୱାସ୍ଥ୍ୟ ପ୍ରଶ୍ନର ଆପଣଙ୍କ ଉତ୍ତର। ଏହି ପର୍ଯ୍ୟାୟରେ ଆମେ ଆପଣଙ୍କ ନାମ କିମ୍ବା ପରିଚୟ ନମ୍ବର ନେଉ ନାହିଁ।",
            "କାହିଁକି: ଆପଣଙ୍କୁ କେତେ ଶୀଘ୍ର ଚିକିତ୍ସା ଦରକାର ତାହା ସ୍ଥିର କରିବା ପାଇଁ, ଏବଂ ଆପଣଙ୍କ ଚିକିତ୍ସା କରୁଥିବା ସ୍ୱାସ୍ଥ୍ୟ କର୍ମୀ ଓ ଡାକ୍ତରଙ୍କ ସହ ଏହା ଅଂଶୀଦାର କରିବା ପାଇଁ।",
            "ଇଚ୍ଛାଧୀନ AI ସହାୟତା: ଏକ କମ୍ପ୍ୟୁଟର ପ୍ରୋଗ୍ରାମ ଆପଣଙ୍କ ଲକ୍ଷଣର ସାରାଂଶ ପ୍ରସ୍ତୁତ କରିପାରେ। ପ୍ରଥମେ ନାମ ଓ ନମ୍ବର ହଟାଯାଏ, କିନ୍ତୁ ଏହା ସମ୍ପୂର୍ଣ୍ଣ ନିର୍ଭୁଲ ନୁହେଁ। ଆପଣ ଏହି ଅଂଶ ପାଇଁ ମନା କରିପାରିବେ।",
            "କୌଣସି ସ୍ୱର ରେକର୍ଡିଂ ସଂରକ୍ଷିତ ହୁଏ ନାହିଁ। ଆପଣଙ୍କ ପସନ୍ଦ ତାରିଖ ଓ ସମୟ ସହ ଲିପିବଦ୍ଧ ହୁଏ।",
            "ଆପଣ ଯେକୌଣସି ସମୟରେ ସ୍ୱାସ୍ଥ୍ୟ କର୍ମୀଙ୍କୁ କହି ସମ୍ମତି ପ୍ରତ୍ୟାହାର କରିପାରିବେ। ପ୍ରତ୍ୟାହାର କଲେ ଆଗକୁ ପ୍ରକ୍ରିୟା ବନ୍ଦ ହୁଏ; ପୂର୍ବରୁ ଲିପିବଦ୍ଧ ତଥ୍ୟ ବର୍ତ୍ତମାନ ପାଇଁ ରଖାଯାଏ।",
            "ଆପଣ ମନା କରିପାରିବେ। ସ୍ୱାସ୍ଥ୍ୟ କର୍ମୀ ଏହି ପ୍ରଣାଳୀ ବିନା ମଧ୍ୟ ଆପଣଙ୍କୁ ଯାଞ୍ଚ କରିପାରିବେ।",
        ],
        purposes={
            "triage": "ମୋର ସ୍ୱାସ୍ଥ୍ୟ ତଥ୍ୟକୁ ଟ୍ରାଏଜ୍ ପାଇଁ ବ୍ୟବହାର କରନ୍ତୁ ଏବଂ ମୋର ଚିକିତ୍ସା କରୁଥିବା ସ୍ୱାସ୍ଥ୍ୟ କର୍ମୀ ଓ ଡାକ୍ତରଙ୍କ ସହ ଅଂଶୀଦାର କରନ୍ତୁ।",
            "ai_assist": "AI ସହାୟତାରେ ସାରାଂଶ ପ୍ରସ୍ତୁତ କରିବାକୁ ମଧ୍ୟ ଅନୁମତି ଦିଅନ୍ତୁ (ପ୍ରଥମେ ନାମ ଓ ନମ୍ବର ହଟାଯିବ; ଏହି ପ୍ରୋଟୋଟାଇପ୍‌ରେ କେବଳ ଇଂରାଜୀ ପାଠ୍ୟ)।",
        },
    ),
}


def get_notice(language: Language) -> Notice:
    return _NOTICES[language]
