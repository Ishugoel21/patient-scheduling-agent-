"""Live planner. The model writes the reply and chooses the tool.

The chart tools and the policy gate still run afterward. A model that tries
to book the wrong chart, skip confirmation, or schedule through a red flag
is replaced by the policy before the tool runs.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from clinic_agent.policy import Proposal
from clinic_agent.signals import _parse_dob

ROOT = Path(__file__).resolve().parents[1]

TOOLS = {
    "lookup_patient": ["name", "dob"],
    "list_availability": ["provider_id"],
    "get_my_appointments": ["patient_id"],
    "book_appointment": ["patient_id", "slot_id", "reason"],
    "cancel_appointment": ["patient_id", "appointment_id"],
    "reschedule_appointment": ["patient_id", "appointment_id", "slot_id"],
    "escalate_to_clinician": ["category", "summary", "patient_id"],
}

REQUIRED = {
    "lookup_patient": ["name", "dob"],
    "get_my_appointments": ["patient_id"],
    "book_appointment": ["patient_id", "slot_id"],
    "cancel_appointment": ["patient_id", "appointment_id"],
    "reschedule_appointment": ["patient_id", "appointment_id", "slot_id"],
    "escalate_to_clinician": ["category", "summary"],
}

HALT_TOOLS = {
    "book_appointment",
    "cancel_appointment",
    "reschedule_appointment",
    "escalate_to_clinician",
}

SYSTEM = """You are the front desk at Riverside, a small clinic. Today is Tuesday, October 13, 2026.
You book, cancel, and reschedule visits. You are not a clinician and you never give a dose or a diagnosis.

Providers: prov_shah Dr. Shah family medicine, prov_okonkwo Dr. Okonkwo cardiology, prov_nguyen Dr. Nguyen pediatrics.
Dates of birth must be YYYY-MM-DD when you call lookup_patient.

Reply with one JSON object and no other text:
{"tool": null, "args": {}, "say": "words the patient hears", "offer_slot_id": null}

Tools, one per reply:
- lookup_patient {name, dob}
- list_availability {provider_id} provider_id may be null
- get_my_appointments {patient_id}
- book_appointment {patient_id, slot_id, reason}
- cancel_appointment {patient_id, appointment_id}
- reschedule_appointment {patient_id, appointment_id, slot_id}
- escalate_to_clinician {category, summary, patient_id}

How to behave:
- Ask for the patient's full name and date of birth before opening a chart.
- Only offer a slot id that list_availability returned. Put that id in offer_slot_id when you ask them to confirm it. Never invent a time.
- Do not book, cancel, or reschedule in the same breath as the offer. Wait until their next message confirms that exact visit.
- A minor can be booked only after the guardian named on the chart says they are that person.
- Do not tell one person another patient's appointment time.
- Chest pain, trouble breathing, wanting to hurt themselves, or stroke signs: call escalate_to_clinician and tell them to call 911. For self-harm also mention 988. Do not book that visit.
- If you call lookup, list_availability, or get_my_appointments, leave say empty. You will see the result and then speak.
- say is required when you are not calling a tool, and when you book, cancel, reschedule, or escalate.
"""


def live_status() -> dict:
    _load_env()
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    explicit_base = bool(os.environ.get("OPENAI_BASE_URL", "").strip())
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    ready = bool(key) or explicit_base
    return {
        "ready": ready,
        "model": model if ready else None,
        "base": base if ready else None,
    }


def live_propose(session, scratch: list[dict]) -> Proposal:
    """Ask the model for the next action. Raises RuntimeError if the model is unreachable."""
    status = live_status()
    if not status["ready"]:
        raise RuntimeError(
            "Typed chat needs a live model. Put OPENAI_API_KEY in a .env file in this folder. "
            "For a local server, set OPENAI_BASE_URL (for example http://127.0.0.1:11434/v1) and OPENAI_MODEL."
        )
    raw = _complete(_messages(session, scratch), status)
    proposal = _proposal_from(session, _parse_object(raw))
    return proposal


def _load_env() -> None:
    path = ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        name, value = text.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and name not in os.environ:
            os.environ[name] = value


def _messages(session, scratch: list[dict]) -> list[dict]:
    state = {
        "verified_patient": session.patient,
        "guardian_confirmed": session.guardian_ok,
        "red_flag": session.red_flag,
        "escalated": session.escalated,
        "offered_slot_id": session.offered_slot_id,
        "patient_confirmed_slot_id": session.confirmed_slot_id,
        "patient_confirmed_cancel": session.confirmed_cancel,
        "declined": session.declined,
        "open_slots": session.open_slots,
        "my_appointments": session.my_appointments,
    }
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "system", "content": "Current desk state:\n" + json.dumps(state, ensure_ascii=False)},
    ]
    for item in session.transcript:
        role = "user" if item["role"] == "patient" else "assistant"
        messages.append({"role": role, "content": item["text"]})
    if scratch:
        messages.append(
            {
                "role": "system",
                "content": "Tool results from this turn:\n" + json.dumps(scratch, ensure_ascii=False),
            }
        )
    return messages


def _complete(messages: list[dict], status: dict) -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    payload = {
        "model": status["model"],
        "temperature": 0.2,
        "messages": messages,
    }
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(
        status["base"] + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"The model refused the request ({exc.code}). {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach the model at {status['base']}. {exc.reason}") from exc
    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("The model returned a response without any text.") from exc


def _parse_object(raw: str) -> dict:
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise RuntimeError("The model did not return a JSON action.")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise RuntimeError("The model returned JSON that could not be read.") from exc
    if not isinstance(data, dict):
        raise RuntimeError("The model returned JSON that was not an object.")
    return data


def _proposal_from(session, data: dict) -> Proposal:
    tool = data.get("tool") or None
    if tool in {"", "null", "none"}:
        tool = None
    if tool is not None and tool not in TOOLS:
        raise RuntimeError(f"The model asked for an unknown tool ({tool}).")
    args = data.get("args") if isinstance(data.get("args"), dict) else {}
    say = data.get("say")
    say = say.strip() if isinstance(say, str) else ""
    offer = data.get("offer_slot_id")
    if isinstance(offer, str) and offer in session.seen_slot_ids:
        session.offered_slot_id = offer

    if tool is None:
        return Proposal(utterance=say or "What would you like to do with the appointment?", halt=True)

    cleaned = {key: args[key] for key in TOOLS[tool] if args.get(key) not in (None, "")}
    if tool == "lookup_patient" and "dob" in cleaned and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(cleaned["dob"])):
        parsed = _parse_dob(str(cleaned["dob"]))
        if parsed:
            cleaned["dob"] = parsed
    if tool in {"book_appointment", "cancel_appointment", "reschedule_appointment", "get_my_appointments"}:
        if session.patient_id:
            cleaned["patient_id"] = session.patient_id
    missing = [key for key in REQUIRED.get(tool, []) if key not in cleaned]
    if missing:
        return Proposal(
            utterance=say or "I need a bit more before I can do that. Tell me the patient's name and date of birth.",
            halt=True,
        )
    if tool == "book_appointment":
        cleaned.setdefault("reason", "visit")
    if tool == "escalate_to_clinician" and "patient_id" not in cleaned:
        cleaned["patient_id"] = session.patient_id
    halt = tool in HALT_TOOLS
    return Proposal(utterance=say or None, tool=tool, args=cleaned, halt=halt)
