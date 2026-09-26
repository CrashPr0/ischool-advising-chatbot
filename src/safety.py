"""Deterministic safety tiers for student messages.

Classification runs before keyword shortcuts, retrieval, and the model.
Crisis and distress replies are static templates. The model is never asked
to invent a hotline, a campus number, or a crisis script.

An optional model check is off unless ``SAFETY_MODEL_ASSIST`` is enabled.
It may only raise the tier. A deterministic crisis result is never downgraded,
and any model error keeps the deterministic tier.
"""

from __future__ import annotations

import os
import re
import unicodedata
from typing import Callable, Optional

TIER_CRISIS = "crisis"
TIER_DISTRESS = "distress"
TIER_NONE = "none"

_TIERS = {TIER_CRISIS, TIER_DISTRESS, TIER_NONE}

# Filler words allowed between a negation and the phrase it cancels.
_NEGATION_TAIL = re.compile(
    r"(?:^|\s)(?:not|no|never|dont|cannot|cant|without|isnt|arent|wasnt|werent|wont)"
    r"(?:\s+(?:do|really|even|actually|just|want|to|going|gonna)){0,3}\s*$"
)

_PERSON = (
    r"(?:he|she|they|boyfriend|girlfriend|partner|husband|wife|roommate|"
    r"someone|somebody|mom|dad|father|mother|parent|parents|abuser|ex)"
)

# Idioms that must not become CRISIS. Checked only after a person-harm match
# has already had its chance, so "he is killing me" stays crisis.
_NON_CRISIS_IDIOMS = (
    re.compile(
        r"\bkill(?:ing|ed|s)?\s+(?:the|a|an|this|that|my)\s+"
        r"(?:process|processes|server|job|task|thread|container|pod|app|"
        r"application|program|script|service|switch)\b"
    ),
    re.compile(r"\bdead\s+week\b"),
    re.compile(
        r"\bshoot\s+me\s+(?:an|a|the)\s+"
        r"(?:email|e\s*mail|message|note|link|text|dm|info|details|copy)\b"
    ),
    re.compile(
        r"\b(?:deadline|class|classes|homework|exam|midterm|final|finals|"
        r"project|assignment|course|school|week|workload|stress|quiz|lab|paper)\b"
        r"(?:\s+\w+){0,5}\s+(?:is\s+|are\s+)?(?:really\s+)?"
        r"(?:going\s+to\s+|gonna\s+|will\s+)?(?:kill|killing|kills)\s+me\b"
    ),
    re.compile(r"\b(?:this|that|it)\s+is\s+(?:really\s+)?killing\s+me\b"),
    re.compile(r"\bkilling\s+me\b"),
    re.compile(r"\bkills\s+me\b"),
)

# (pattern, ignore_negation). Negation-immune phrases include the negation
# itself: "don't want to live" is crisis, not a cancelled "want to live".
_CRISIS_PATTERNS = (
    (re.compile(r"\bdont\s+want\s+to\s+live\b"), True),
    (re.compile(r"\bdo\s+not\s+want\s+to\s+live\b"), True),
    (re.compile(r"\bno\s+longer\s+want\s+to\s+live\b"), True),
    (re.compile(r"\bno\s+quiero\s+vivir\b"), True),
    (re.compile(
        rf"\b{_PERSON}\b(?:\s+\w+){{0,6}}\s+(?:is\s+|was\s+|keeps\s+)?"
        rf"(?:going\s+to\s+|gonna\s+|will\s+|trying\s+to\s+)?"
        rf"(?:kill(?:ing)?|hurt(?:ing)?|hit(?:ting)?|beat(?:ing)?|abus(?:e|ing|ed))\s+me\b"
    ), False),
    (re.compile(r"\b(?:being|been)\s+abused\b"), False),
    (re.compile(r"\b(?:am|im)\s+abused\b"), False),
    (re.compile(r"\babused\s+me\b"), False),
    (re.compile(r"\bdomestic\s+violence\b"), False),
    (re.compile(r"\bsexually\s+assaulted\b"), False),
    (re.compile(r"\bsexual\s+assault\b"), False),
    (re.compile(r"\bin\s+immediate\s+danger\b"), False),
    (re.compile(r"\bin\s+danger\b(?!\s+of\b)"), False),
    (re.compile(r"\b(?:i\s+am|im)\s+not\s+safe\b"), False),
    (re.compile(r"\b(?:dont|do\s+not)\s+feel\s+safe\b"), False),
    (re.compile(r"\bsuicid"), False),
    (re.compile(r"\bkill\s+myself\b"), False),
    (re.compile(r"\bkms\b"), False),
    (re.compile(r"\bend(?:ing)?\s+my\s+life\b"), False),
    (re.compile(r"\btake\s+my\s+(?:own\s+)?life\b"), False),
    (re.compile(r"\bwant(?:ed)?\s+to\s+die\b"), False),
    (re.compile(r"\bwanna\s+die\b"), False),
    (re.compile(r"\bwish(?:ed)?\s+i\s+(?:was|were)\s+dead\b"), False),
    (re.compile(r"\bbetter\s+off\s+dead\b"), False),
    (re.compile(r"\b(?:hurt|harm|cut)(?:ing)?\s+myself\b"), False),
    (re.compile(r"\bself\s+harm"), False),
    (re.compile(r"\bunalive\s+myself\b"), False),
    (re.compile(r"\bshoot\s+myself\b"), False),
    (re.compile(r"\bkill\s+me\b"), False),
    (re.compile(
        r"\b(?:kill|hurt|harm|shoot)\s+"
        r"(?:him|her|them|someone|somebody|people|everyone|everybody)\b"
    ), False),
    (re.compile(
        r"\b(?:going\s+to|gonna|want\s+to)\s+(?:kill|hurt|shoot)\s+"
        r"(?:him|her|them|someone|somebody|people|everyone)\b"
    ), False),
    (re.compile(r"\bquiero\s+morir\b"), False),
    (re.compile(r"\bme\s+quiero\s+suicid\w*"), False),
    (re.compile(r"\bquiero\s+suicidarme\b"), False),
    (re.compile(r"\bvoy\s+a\s+suicidarme\b"), False),
    (re.compile(r"\bquiero\s+hacerme\s+dano\b"), False),
    (re.compile(r"\bquiero\s+matarme\b"), False),
    (re.compile(r"\bme\s+quiero\s+matar\b"), False),
)

_DISTRESS_PATTERNS = (
    re.compile(r"\boverwhelm\w*\b"),
    re.compile(r"\b(?:cant|cannot|can\s+not)\s+cope\b"),
    re.compile(r"\banxious\b"),
    re.compile(r"\banxiety\b"),
    re.compile(r"\bdepress(?:ed|ion|ing)\b"),
    re.compile(r"\bburn(?:ed|t)\s+out\b"),
    re.compile(r"\bburnout\b"),
    re.compile(r"\bfailing\s+(?:everything|all)\b"),
    re.compile(r"\bfalling\s+behind\b"),
    re.compile(r"\bin\s+danger\s+of\s+failing\b"),
    re.compile(
        r"\b(?:cant|cannot|can\s+not|dont|do\s+not)\s+"
        r"(?:think\s+i\s+can\s+)?keep\s+going\b"
    ),
    re.compile(r"\bdo\s+not\s+think\s+i\s+can\s+keep\s+going\b"),
    re.compile(r"\bthinking\s+(?:of|about)\s+(?:dropping\s+out|leaving)\b"),
    re.compile(r"\bdrop(?:ping)?\s+out\b"),
    re.compile(
        r"\bleav(?:e|ing)\s+(?:school|college|the\s+program|the\s+university|sjsu)\b"
    ),
    re.compile(r"\bwhether\s+i\s+should\s+stay\b"),
    re.compile(r"\bshould\s+i\s+stay\b"),
    re.compile(r"\bwant\s+to\s+give\s+up\b"),
    re.compile(r"\b(?:cant|cannot|can\s+not)\s+do\s+this\s+anymore\b"),
    re.compile(r"\bpanic\s+attack\b"),
    re.compile(r"\b(?:so|too)\s+stressed\b"),
    re.compile(r"\bkilling\s+me\b"),
    re.compile(r"\bkills\s+me\b"),
    re.compile(
        r"\b(?:deadline|class|classes|homework|exam|midterm|final|finals|"
        r"project|assignment|course|school|week|workload|stress|quiz|lab|paper)\b"
        r"(?:\s+\w+){0,5}\s+(?:is\s+|are\s+)?(?:really\s+)?"
        r"(?:going\s+to\s+|gonna\s+|will\s+)?kill\s+me\b"
    ),
    re.compile(r"\babrumad\w*\b"),
    re.compile(r"\bno\s+puedo\s+(?:mas|seguir)\b"),
    re.compile(r"\bansiedad\b"),
    re.compile(r"\bansios\w*\b"),
    re.compile(r"\bdeprimid\w*\b"),
    re.compile(r"\bquiero\s+dejar\s+(?:la\s+)?(?:escuela|universidad|carrera)\b"),
)

_CHINESE_DISTRESS = (
    "压力很大",
    "压力好大",
    "太焦虑",
    "很焦虑",
    "抑郁",
    "撑不下去",
    "想退学",
    "不想读了",
    "崩溃了",
)

_ADVISING_REQUEST = re.compile(
    r"\bhow\s+(?:do|can|should|to)\b.{0,48}\b"
    r"(?:withdraw|drop|leave|enroll|add|waitlist|petition|register|absence)\b"
    r"|\bwhere\s+(?:do|can)\s+i\b.{0,48}\b"
    r"(?:withdraw|drop|leave|enroll|add|petition|register|absence|form)\b"
    r"|\bwithdraw(?:al|ing)?\b"
    r"|\bdrop(?:ping)?\s+(?:a|the|my|this|one)\s+class\b"
    r"|\bleave\s+of\s+absence\b"
    r"|\bpermission\s+number\b"
    r"|\badd\s+code\b"
)

_SPANISH_SIGNAL = re.compile(
    r"\b(?:quiero|estoy|puedo|abrumad\w*|ansiedad|ansios\w*|deprimid\w*|"
    r"suicid\w*|matarme|hacerme|escuela|universidad|morir|vivir)\b"
)

CRISIS_RESPONSE_EN = """I'm glad you told me. You deserve support right now, and you do not have to handle this alone.

If you are in immediate danger, call 911.

The 988 Suicide & Crisis Lifeline is free, confidential, and available 24/7. Call [988](tel:988) or text [988](sms:988). You can also visit https://988lifeline.org/.

SJSU Counseling and Psychological Services (CAPS) is in the Student Wellness Center. Call [408-924-5678](tel:4089245678). During business hours you can drop in or call for same-day support, and same-day telehealth visits open each morning in the SWC Patient Portal: https://www.sjsu.edu/wellness/access-services/counseling/index.php. After hours, call [408-924-5678](tel:4089245678) for urgent mental health support: https://www.sjsu.edu/wellness/access-services/emergency-crisis.php.

SJSU University Police: call 911 or [408-924-2222](tel:4089242222). https://www.sjsu.edu/police/"""

CRISIS_RESPONSE_ES = """Me alegra que lo hayas dicho. Mereces apoyo ahora, y no tienes que enfrentarlo solo.

Si estás en peligro inmediato, llama al 911.

La línea 988 Suicide & Crisis Lifeline es gratuita, confidencial y está disponible las 24 horas. Llama al [988](tel:988) o envía un mensaje de texto al [988](sms:988). También puedes visitar https://988lifeline.org/.

SJSU Counseling and Psychological Services (CAPS), en el Student Wellness Center, atiende en el [408-924-5678](tel:4089245678). En horario de oficina puedes pasar sin cita o llamar para apoyo el mismo día, y cada mañana se abren citas de teleconsulta del mismo día en el SWC Patient Portal: https://www.sjsu.edu/wellness/access-services/counseling/index.php. Fuera de horario, llama al [408-924-5678](tel:4089245678) para apoyo urgente de salud mental: https://www.sjsu.edu/wellness/access-services/emergency-crisis.php.

Policía universitaria de SJSU: llama al 911 o al [408-924-2222](tel:4089242222). https://www.sjsu.edu/police/"""

CRISIS_RESPONSE_ZH = """你愿意说出来，这很重要。你值得被支持，不必一个人扛着。

如果正处于即时危险中，请拨打 911。

988 自杀与心理危机生命线免费、保密，全天可用。请拨打 [988](tel:988) 或发送短信 [988](sms:988)，也可以访问 https://988lifeline.org/。

圣何塞州立大学 Counseling and Psychological Services（CAPS）位于 Student Wellness Center，电话 [408-924-5678](tel:4089245678)。办公时间内可以上门或致电获得当日支持，每天早上也可在 SWC Patient Portal 预约当日远程咨询：https://www.sjsu.edu/wellness/access-services/counseling/index.php。非办公时间的紧急心理健康支持请拨打 [408-924-5678](tel:4089245678)：https://www.sjsu.edu/wellness/access-services/emergency-crisis.php。

SJSU 校警紧急电话：拨打 911 或 [408-924-2222](tel:4089242222)。https://www.sjsu.edu/police/"""

DISTRESS_RESPONSE_EN = """I'm sorry you're carrying this much. You do not have to decide today whether to stay or leave, and this chat cannot make that decision for you.

Counseling and Psychological Services (CAPS) at the Student Wellness Center can help. For same-day support, drop in or call [408-924-5678](tel:4089245678) during business hours. Same-day telehealth times open each morning, Monday through Friday, in the SWC Patient Portal.
https://www.sjsu.edu/wellness/access-services/counseling/index.php
Same-day appointments: https://www.sjsu.edu/wellness/access-services/make-an-appointment.php

You can also talk with an iSchool advisor. Current students schedule in Spartan Connect: https://one.sjsu.edu/task/all/spartan-connect
iSchool advising: https://ischool.sjsu.edu/bsisda-advising

These academic options exist if you want them:
- Drop a class in MySJSU: https://www.sjsu.edu/registrar/registration/registration-basics/drop-a-class.php
- Late drop or semester withdrawal: https://www.sjsu.edu/ue/student-petitions/drops/index.php
- Leave of absence: https://www.sjsu.edu/registrar/registration/leave-of-absence.php"""

DISTRESS_RESPONSE_ES = """Siento que estés cargando con tanto. No tienes que decidir hoy si quedarte o irte, y este chat no puede decidirlo por ti.

Counseling and Psychological Services (CAPS), en el Student Wellness Center, ofrece apoyo el mismo día. Pasa sin cita o llama al [408-924-5678](tel:4089245678) en horario de oficina. Las citas de teleconsulta del mismo día se abren cada mañana, de lunes a viernes, en el SWC Patient Portal.
https://www.sjsu.edu/wellness/access-services/counseling/index.php
Citas del mismo día: https://www.sjsu.edu/wellness/access-services/make-an-appointment.php

También puedes hablar con un asesor de iSchool. Estudiantes actuales reservan en Spartan Connect: https://one.sjsu.edu/task/all/spartan-connect
Asesoría de iSchool: https://ischool.sjsu.edu/bsisda-advising

Estas opciones académicas existen si las quieres:
- Dar de baja una clase en MySJSU: https://www.sjsu.edu/registrar/registration/registration-basics/drop-a-class.php
- Baja tardía o retiro del semestre: https://www.sjsu.edu/ue/student-petitions/drops/index.php
- Permiso de ausencia (leave of absence): https://www.sjsu.edu/registrar/registration/leave-of-absence.php"""

DISTRESS_RESPONSE_ZH = """你现在承受的已经很多了。你不必今天就决定留下还是离开，这个对话也不能替你做这个决定。

Student Wellness Center 的 Counseling and Psychological Services（CAPS）可以提供帮助。办公时间内可以上门，或拨打 [408-924-5678](tel:4089245678) 获得当日支持。周一到周五每天早上，SWC Patient Portal 会开放当日远程咨询时段。
https://www.sjsu.edu/wellness/access-services/counseling/index.php
当日预约：https://www.sjsu.edu/wellness/access-services/make-an-appointment.php

你也可以和 iSchool 顾问谈谈。在读学生通过 Spartan Connect 预约：https://one.sjsu.edu/task/all/spartan-connect
iSchool 咨询：https://ischool.sjsu.edu/bsisda-advising

如果需要，学校有这些学业办法：
- 在 MySJSU 退选一门课：https://www.sjsu.edu/registrar/registration/registration-basics/drop-a-class.php
- 逾期退课或学期退学申请：https://www.sjsu.edu/ue/student-petitions/drops/index.php
- 休学（leave of absence）：https://www.sjsu.edu/registrar/registration/leave-of-absence.php"""

TEMPLATES = {
    (TIER_CRISIS, "en"): CRISIS_RESPONSE_EN,
    (TIER_CRISIS, "es"): CRISIS_RESPONSE_ES,
    (TIER_CRISIS, "zh"): CRISIS_RESPONSE_ZH,
    (TIER_DISTRESS, "en"): DISTRESS_RESPONSE_EN,
    (TIER_DISTRESS, "es"): DISTRESS_RESPONSE_ES,
    (TIER_DISTRESS, "zh"): DISTRESS_RESPONSE_ZH,
}


def model_assist_enabled() -> bool:
    """True only when an operator explicitly turns the optional checker on."""
    return os.getenv("SAFETY_MODEL_ASSIST", "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def normalize_text(text: str) -> str:
    """Casefold, strip Latin accents, and flatten punctuation to spaces.

    CJK characters are kept. Apostrophes are removed so ``don't`` stays one
    token (``dont``) instead of ``don`` + ``t``.
    """
    if not isinstance(text, str):
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    folded = stripped.casefold()
    for source, expanded in (
        ("i'm", "i am"),
        ("he's", "he is"),
        ("she's", "she is"),
        ("they're", "they are"),
        ("we're", "we are"),
    ):
        folded = folded.replace(source, expanded)
    folded = folded.replace("'", "").replace("’", "").replace("`", "")
    cleaned = re.sub(r"[^\w\s]+", " ", folded, flags=re.UNICODE)
    return re.sub(r"\s+", " ", cleaned).strip()


def _is_negated(text: str, start: int) -> bool:
    """True when a short negation sits immediately before ``start``."""
    return _NEGATION_TAIL.search(text[max(0, start - 48) : start]) is not None


def _pattern_hits(pattern: re.Pattern, text: str, immune: bool = False) -> bool:
    for match in pattern.finditer(text):
        if immune or not _is_negated(text, match.start()):
            return True
    return False


def _chinese_crisis(text: str) -> bool:
    """Simplified (and a few traditional) crisis phrases.

    ``不想活`` stays crisis. ``不想死`` and ``没有想死`` do not.
    """
    if "不想活" in text or "活不下去" in text:
        return True
    for phrase in (
        "想自杀",
        "想自殺",
        "想死",
        "想伤害自己",
        "想傷害自己",
        "想结束生命",
        "想結束生命",
        "想伤害别人",
        "想傷害別人",
    ):
        start = 0
        while True:
            index = text.find(phrase, start)
            if index < 0:
                break
            prefix = text[max(0, index - 2) : index]
            if prefix.endswith(("没有", "不", "没")):
                start = index + len(phrase)
                continue
            return True
    return False


def _mask_non_crisis_idioms(text: str) -> str:
    masked = text
    for pattern in _NON_CRISIS_IDIOMS:
        masked = pattern.sub(" ", masked)
    return masked


def _deterministic_tier(text: str) -> str:
    normalized = normalize_text(text)
    if not normalized:
        return TIER_NONE
    if _chinese_crisis(normalized):
        return TIER_CRISIS
    # Person-harm is scored before idiom masking so "he is killing me"
    # is not treated like "this deadline is killing me".
    for pattern, immune in _CRISIS_PATTERNS[:6]:
        if _pattern_hits(pattern, normalized, immune):
            return TIER_CRISIS
    masked = _mask_non_crisis_idioms(normalized)
    for pattern, immune in _CRISIS_PATTERNS[6:]:
        if _pattern_hits(pattern, masked, immune):
            return TIER_CRISIS
    for pattern in _DISTRESS_PATTERNS:
        if _pattern_hits(pattern, normalized):
            return TIER_DISTRESS
    for phrase in _CHINESE_DISTRESS:
        if phrase in normalized:
            return TIER_DISTRESS
    return TIER_NONE


def classify_message(
    text: str,
    model_assist: Optional[Callable[[str], str]] = None,
) -> str:
    """Return ``crisis``, ``distress``, or ``none``.

    ``model_assist`` is consulted only when the deterministic tier is not
    crisis. Exceptions and unrecognized labels leave the deterministic tier
    unchanged. The callable cannot lower a crisis or distress result.
    """
    tier = _deterministic_tier(text)
    if tier == TIER_CRISIS or model_assist is None:
        return tier
    try:
        suggested = model_assist(text)
    except Exception:
        return tier
    if not isinstance(suggested, str):
        return tier
    token = suggested.strip().casefold().split()[:1]
    label = token[0].strip(".,:;") if token else ""
    if label == TIER_CRISIS:
        return TIER_CRISIS
    if label == TIER_DISTRESS and tier == TIER_NONE:
        return TIER_DISTRESS
    return tier


def reply_language(message: str) -> str:
    """``zh`` when the student used Han text, else ``es`` or ``en``."""
    if isinstance(message, str) and re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", message):
        return "zh"
    if _SPANISH_SIGNAL.search(normalize_text(message)):
        return "es"
    return "en"


def safety_reply(tier: str, message: str) -> str:
    """Return the vetted template for this tier and the student's language."""
    if tier not in {TIER_CRISIS, TIER_DISTRESS}:
        return ""
    return TEMPLATES[(tier, reply_language(message))]


def message_has_advising_request(message: str) -> bool:
    """True when a distressed student also asked a procedural advising question.

    "Drop a class" by itself is a normal advising request and is not distress.
    "How do I withdraw" alongside overwhelm is both.
    """
    return _ADVISING_REQUEST.search(normalize_text(message)) is not None
