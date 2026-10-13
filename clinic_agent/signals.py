"""Turn patient text into structured signals.

Safety classification lives here, not in the planner. The lexicon is
intentionally small and will miss paraphrases. The eval labels emergencies
on the scenario itself so a silent detector cannot grade itself as fine.
"""

from __future__ import annotations

import re
from datetime import datetime

from clinic_agent.clinic import new_clinic, panel_names

MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

# Order is priority if a sentence matches more than one.
RED_FLAGS: list[tuple[str, re.Pattern[str]]] = [
    ("self_harm", re.compile(r"\b(hurt|harm|kill) myself\b|\bsuicid\w*\b|\bend my life\b", re.I)),
    ("chest_pain", re.compile(r"\bchest\s+pain\b|\bchest\s+pressure\b|\bpressure in (my )?chest\b|\bheart attack\b", re.I)),
    ("breathing", re.compile(r"\btrouble breathing\b|\bdifficulty breathing\b|\bcan't breathe\b|\bcannot breathe\b|\bshort(?:ness)? of breath\b", re.I)),
    ("stroke", re.compile(r"\bslurred speech\b|\bface (is )?droop\w*\b|\bsudden weakness\b", re.I)),
]

HEDGE = re.compile(
    r"\b(maybe|not sure|let me think|think about it|don't book|do not book|hold off|not yet|only want)\b",
    re.I,
)
AFFIRM = re.compile(
    r"\b(yes|yeah|yep|confirm|go ahead)\b|\bplease book\b|\bbook it\b|\bbook the soonest\b|\bmove it\b",
    re.I,
)
ADVICE = re.compile(
    r"\b(what dose|how much|how many)\b|\b(ibuprofen|acetaminophen|tylenol|aspirin)\b",
    re.I,
)
WHEN_ASK = re.compile(r"\b(what time|when is|when'?s|appointment time)\b", re.I)

_PANEL = panel_names(new_clinic())
_PATIENT_NAMES = {p["name"] for p in new_clinic()["patients"].values()}
_GUARDIAN_NAMES = {
    p["guardian_name"] for p in new_clinic()["patients"].values() if p["guardian_name"]
}


def _norm(name: str) -> str:
    return " ".join(name.lower().split())


def _parse_dob(text: str) -> str | None:
    iso = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if iso:
        return f"{iso.group(1)}-{iso.group(2)}-{iso.group(3)}"
    month_first = re.search(
        r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2}),?\s+(\d{4})\b",
        text,
        re.I,
    )
    if month_first:
        month = MONTHS[month_first.group(1).lower()]
        return f"{int(month_first.group(3)):04d}-{month:02d}-{int(month_first.group(2)):02d}"
    day_first = re.search(
        r"\b(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{4})\b",
        text,
        re.I,
    )
    if day_first:
        month = MONTHS[day_first.group(2).lower()]
        return f"{int(day_first.group(3)):04d}-{month:02d}-{int(day_first.group(1)):02d}"
    slash = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text)
    if slash:
        # US month/day/year. The clinic is US-based; this is an assumption.
        month, day, year = int(slash.group(1)), int(slash.group(2)), int(slash.group(3))
        try:
            datetime(year, month, day)
        except ValueError:
            return None
        return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def _names_in(text: str) -> list[str]:
    lowered = text.lower()
    found = []
    for name in _PANEL:
        if name.lower() in lowered:
            found.append(name)
    return found


def _set_intent(session, new: str) -> None:
    rank = {None: 0, "book": 1, "cancel": 2, "reschedule": 3}
    if rank[new] >= rank[session.intent]:
        session.intent = new


def _provider(text: str) -> str | None:
    if re.search(r"\bshah\b", text, re.I):
        return "prov_shah"
    if re.search(r"\bokonkwo\b", text, re.I):
        return "prov_okonkwo"
    if re.search(r"\bnguyen\b", text, re.I) or re.search(r"\bpediatr", text, re.I):
        return "prov_nguyen"
    return None


def absorb(session, text: str) -> None:
    """Update session signals from one patient utterance. Sticky flags stay set."""
    if session.red_flag is None:
        for category, pattern in RED_FLAGS:
            if pattern.search(text):
                session.red_flag = category
                session.red_flag_excerpt = text.strip()
                break

    if ADVICE.search(text):
        session.advice_request = True

    for name in _names_in(text):
        if name in _PATIENT_NAMES:
            session.candidate_name = name
        if name in _GUARDIAN_NAMES:
            session.guardian_named = name

    dob = _parse_dob(text)
    if dob:
        session.candidate_dob = dob

    provider = _provider(text)
    if provider:
        session.preference.provider_id = provider

    for word, index in WEEKDAYS.items():
        if re.search(rf"\b{word}\b", text, re.I):
            session.preference.weekday = index
    if re.search(r"\bafternoon\b", text, re.I):
        session.preference.daypart = "afternoon"
    elif re.search(r"\bmorning\b", text, re.I) and not re.search(r"\b(this|since|yesterday) morning\b", text, re.I):
        # "since this morning" is when a symptom started, not a request for a morning visit.
        session.preference.daypart = "morning"
    if re.search(r"\b7(?::00)?\s*(am|a\.m\.)\b", text, re.I):
        session.preference.hour = 7
    if re.search(r"\b(soonest|earliest|today|as soon as)\b", text, re.I):
        session.preference.soonest = True

    if re.search(r"\breschedul|\bmove my appointment\b", text, re.I):
        _set_intent(session, "reschedule")
    elif re.search(r"\bcancel\b", text, re.I):
        _set_intent(session, "cancel")
    elif re.search(r"\b(book|checkup|check-up|fit me in|routine visit|be seen)\b", text, re.I):
        _set_intent(session, "book")

    if _is_phi_probe(text):
        session.phi_probe = True

    hedged = HEDGE.search(text) is not None
    affirmed = AFFIRM.search(text) is not None
    can_decline = bool(session.offered_slot_id or session.unavailable_explained or session.cancel_prompted)
    if hedged and can_decline:
        session.declined = True
        return
    if not affirmed:
        return
    # A "yes" counts only for the offer already on the table. A yes in the
    # same breath as the original request does not confirm a slot we have
    # not named yet. That case is premature_confirm in the suite.
    if session.offered_slot_id and session.intent in {"book", "reschedule"}:
        session.confirmed_slot_id = session.offered_slot_id
    if session.cancel_prompted and session.intent == "cancel":
        session.confirmed_cancel = True


def _is_phi_probe(text: str) -> bool:
    if not WHEN_ASK.search(text):
        return False
    for name in _PATIENT_NAMES:
        if name.lower() not in text.lower():
            continue
        identified = re.search(rf"(i am|i'm|this is|my name is)\s+{re.escape(name)}", text, re.I)
        if identified:
            continue
        return True
    return False


def preference_specified(pref) -> bool:
    return (
        pref.weekday is not None
        or pref.daypart is not None
        or pref.hour is not None
        or pref.soonest
    )
