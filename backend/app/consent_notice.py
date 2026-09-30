"""Versioned consent notice (docs/11 §K). Served by the backend so the UI shows exactly what the
consent record references.

Review status is recorded with every consent event:
- en: `project_draft` — written by the project team; not legally reviewed.
- hi, or: `draft_unreviewed_translation` — machine-assisted drafts; need native-speaker review
  before any real use. The UI shows a warning banner for these.
Language support here is for *displaying the notice only*; AI-assisted text processing accepts
English text only in this prototype.

`voice_cloud` (Phase 4, docs/12): permission to send the patient's voice to Sarvam AI for online
speech-to-text. The wording states Sarvam's published defaults plainly (retention up to 30 days; may
be used for model training unless the deploying organisation opts out). Whether an opt-out is in
place is not verified by this software.
"""

from typing import Literal

from pydantic import BaseModel

NOTICE_VERSION = "2026-09-30.3"  # .2: voice + cloud speech (Phase 4); .3: read-back text + training stated in the checkbox

Language = Literal["en", "hi", "or"]
Purpose = Literal["triage", "ai_assist", "voice_cloud"]


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
            "If you describe your symptoms by speaking, the written text of what was said is kept with this visit's record. SEHAT AI does not store the voice recording itself.",
            "Optional online speech processing: if you allow it, your voice is sent to Sarvam AI (a company in India) to turn it into text, and the values heard (for example your temperature) may be sent to Sarvam to be read back aloud. Sarvam may keep these for up to 30 days and may use them to improve its models unless the organisation running this system has opted out. You can say no to this part; you can still speak (if on-device speech processing is available) or type instead.",
            "Your choices are recorded with the date and time.",
            "You can withdraw at any time by telling the health worker. Withdrawing stops further processing; information already recorded is kept for now.",
            "You can say no. The health worker can still assess you without this system.",
        ],
        purposes={
            "triage": "Use my health information for triage and share it with the health worker and doctor treating me.",
            "ai_assist": "Also allow AI assistance to draft a summary (names and numbers removed first; English text only in this prototype).",
            "voice_cloud": "Also allow my voice, and the values read back to me, to be sent to Sarvam AI (India). Sarvam may keep them for up to 30 days and may use them to improve its models unless the organisation has opted out.",
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
            "अगर आप बोलकर अपने लक्षण बताते हैं, तो जो कहा गया उसका लिखित पाठ इस बार के रिकॉर्ड के साथ रखा जाता है। SEHAT AI आवाज़ की रिकॉर्डिंग खुद संग्रहीत नहीं करता।",
            "वैकल्पिक ऑनलाइन वाणी प्रसंस्करण: अगर आप अनुमति देते हैं, तो आपकी आवाज़ पाठ में बदलने के लिए Sarvam AI (भारत की एक कंपनी) को भेजी जाती है, और सुने गए मान (जैसे आपका तापमान) पढ़कर सुनाने के लिए Sarvam को भेजे जा सकते हैं। Sarvam इन्हें 30 दिनों तक रख सकता है और अपने मॉडल सुधारने के लिए इसका उपयोग कर सकता है, जब तक कि इस प्रणाली को चलाने वाले संगठन ने मना न किया हो। आप इस भाग के लिए मना कर सकते हैं; फिर भी आप बोल सकते हैं (अगर डिवाइस पर वाणी प्रसंस्करण उपलब्ध है) या लिख सकते हैं।",
            "आपकी पसंद तारीख और समय के साथ दर्ज की जाती है।",
            "आप कभी भी स्वास्थ्य कर्मी को बताकर सहमति वापस ले सकते हैं। वापस लेने पर आगे की प्रक्रिया रुक जाती है; जो जानकारी पहले से दर्ज है वह अभी रखी जाती है।",
            "आप मना कर सकते हैं। स्वास्थ्य कर्मी इस प्रणाली के बिना भी आपकी जाँच कर सकते हैं।",
        ],
        purposes={
            "triage": "मेरी स्वास्थ्य जानकारी का उपयोग ट्राइएज के लिए करें और इसे मेरा इलाज करने वाले स्वास्थ्य कर्मी और डॉक्टर के साथ साझा करें।",
            "ai_assist": "AI सहायता से सारांश तैयार करने की भी अनुमति दें (पहले नाम और नंबर हटाए जाएँगे; इस प्रोटोटाइप में केवल अंग्रेज़ी पाठ)।",
            "voice_cloud": "मेरी आवाज़ और मुझे पढ़कर सुनाए जाने वाले मान Sarvam AI (भारत) को भेजने की भी अनुमति दें। Sarvam इन्हें 30 दिनों तक रख सकता है और, जब तक संगठन ने मना न किया हो, अपने मॉडल सुधारने के लिए उपयोग कर सकता है।",
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
            "ଯଦି ଆପଣ କହି ଆପଣଙ୍କ ଲକ୍ଷଣ ବର୍ଣ୍ଣନା କରନ୍ତି, ଯାହା କୁହାଗଲା ତାହାର ଲିଖିତ ପାଠ୍ୟ ଏହି ଥରର ରେକର୍ଡ ସହ ରଖାଯାଏ। SEHAT AI ସ୍ୱର ରେକର୍ଡିଂ ନିଜେ ସଂରକ୍ଷଣ କରେ ନାହିଁ।",
            "ଇଚ୍ଛାଧୀନ ଅନଲାଇନ୍ ସ୍ୱର ପ୍ରକ୍ରିୟାକରଣ: ଯଦି ଆପଣ ଅନୁମତି ଦିଅନ୍ତି, ଆପଣଙ୍କ ସ୍ୱରକୁ ପାଠ୍ୟରେ ପରିଣତ କରିବା ପାଇଁ Sarvam AI (ଭାରତର ଏକ କମ୍ପାନୀ)କୁ ପଠାଯାଏ, ଏବଂ ଶୁଣାଯାଇଥିବା ମୂଲ୍ୟ (ଯେପରି ଆପଣଙ୍କ ତାପମାତ୍ରା) ପଢ଼ି ଶୁଣାଇବା ପାଇଁ Sarvamକୁ ପଠାଯାଇପାରେ। Sarvam ଏଗୁଡ଼ିକୁ 30 ଦିନ ପର୍ଯ୍ୟନ୍ତ ରଖିପାରେ ଏବଂ ଏହି ପ୍ରଣାଳୀ ଚଳାଉଥିବା ସଂସ୍ଥା ମନା କରିନଥିଲେ ନିଜ ମଡେଲ ଉନ୍ନତି ପାଇଁ ବ୍ୟବହାର କରିପାରେ। ଆପଣ ଏହି ଅଂଶ ପାଇଁ ମନା କରିପାରିବେ; ତଥାପି ଆପଣ କହିପାରିବେ (ଯଦି ଡିଭାଇସରେ ସ୍ୱର ପ୍ରକ୍ରିୟାକରଣ ଉପଲବ୍ଧ) କିମ୍ବା ଲେଖିପାରିବେ।",
            "ଆପଣଙ୍କ ପସନ୍ଦ ତାରିଖ ଓ ସମୟ ସହ ଲିପିବଦ୍ଧ ହୁଏ।",
            "ଆପଣ ଯେକୌଣସି ସମୟରେ ସ୍ୱାସ୍ଥ୍ୟ କର୍ମୀଙ୍କୁ କହି ସମ୍ମତି ପ୍ରତ୍ୟାହାର କରିପାରିବେ। ପ୍ରତ୍ୟାହାର କଲେ ଆଗକୁ ପ୍ରକ୍ରିୟା ବନ୍ଦ ହୁଏ; ପୂର୍ବରୁ ଲିପିବଦ୍ଧ ତଥ୍ୟ ବର୍ତ୍ତମାନ ପାଇଁ ରଖାଯାଏ।",
            "ଆପଣ ମନା କରିପାରିବେ। ସ୍ୱାସ୍ଥ୍ୟ କର୍ମୀ ଏହି ପ୍ରଣାଳୀ ବିନା ମଧ୍ୟ ଆପଣଙ୍କୁ ଯାଞ୍ଚ କରିପାରିବେ।",
        ],
        purposes={
            "triage": "ମୋର ସ୍ୱାସ୍ଥ୍ୟ ତଥ୍ୟକୁ ଟ୍ରାଏଜ୍ ପାଇଁ ବ୍ୟବହାର କରନ୍ତୁ ଏବଂ ମୋର ଚିକିତ୍ସା କରୁଥିବା ସ୍ୱାସ୍ଥ୍ୟ କର୍ମୀ ଓ ଡାକ୍ତରଙ୍କ ସହ ଅଂଶୀଦାର କରନ୍ତୁ।",
            "ai_assist": "AI ସହାୟତାରେ ସାରାଂଶ ପ୍ରସ୍ତୁତ କରିବାକୁ ମଧ୍ୟ ଅନୁମତି ଦିଅନ୍ତୁ (ପ୍ରଥମେ ନାମ ଓ ନମ୍ବର ହଟାଯିବ; ଏହି ପ୍ରୋଟୋଟାଇପ୍‌ରେ କେବଳ ଇଂରାଜୀ ପାଠ୍ୟ)।",
            "voice_cloud": "ମୋ ସ୍ୱର ଏବଂ ମୋତେ ପଢ଼ି ଶୁଣାଯାଉଥିବା ମୂଲ୍ୟ Sarvam AI (ଭାରତ)କୁ ପଠାଇବାକୁ ମଧ୍ୟ ଅନୁମତି ଦିଅନ୍ତୁ। Sarvam ଏଗୁଡ଼ିକୁ 30 ଦିନ ପର୍ଯ୍ୟନ୍ତ ରଖିପାରେ ଏବଂ ସଂସ୍ଥା ମନା କରିନଥିଲେ ନିଜ ମଡେଲ ଉନ୍ନତି ପାଇଁ ବ୍ୟବହାର କରିପାରେ।",
        },
    ),
}


def get_notice(language: Language) -> Notice:
    return _NOTICES[language]
