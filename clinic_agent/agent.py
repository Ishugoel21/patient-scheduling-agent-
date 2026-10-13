"""Multi-turn scheduler.

The planner never special-cases emergencies. If the patient reports chest
pain it still tries to find a visit. Stopping that is the policy's job,
and version 0 of the policy does not have that rule yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from clinic_agent.clinic import SCHEDULING_TOOLS, call_tool, new_clinic
from clinic_agent.llm import live_propose, live_status
from clinic_agent.policy import PolicyPack, Proposal, apply
from clinic_agent.signals import absorb, preference_specified

MAX_STEPS = 6


@dataclass
class Preference:
    weekday: int | None = None
    daypart: str | None = None
    hour: int | None = None
    soonest: bool = False
    provider_id: str | None = None


@dataclass
class Session:
    clinic: dict
    transcript: list = field(default_factory=list)
    trace: list = field(default_factory=list)
    turn: int = 0
    intent: str | None = None
    candidate_name: str | None = None
    candidate_dob: str | None = None
    identity_verified: bool = False
    patient_id: str | None = None
    patient: dict | None = None
    guardian_named: str | None = None
    guardian_ok: bool = False
    red_flag: str | None = None
    red_flag_excerpt: str | None = None
    signal_logged: bool = False
    advice_request: bool = False
    phi_probe: bool = False
    preference: Preference = field(default_factory=Preference)
    seen_slot_ids: set = field(default_factory=set)
    open_slots: list = field(default_factory=list)
    availability_loaded: bool = False
    my_appointments: list | None = None
    offered_slot_id: str | None = None
    confirmed_slot_id: str | None = None
    cancel_prompted: bool = False
    confirmed_cancel: bool = False
    unavailable_explained: bool = False
    declined: bool = False
    asked_preference: bool = False
    escalated: bool = False
    reason: str = "visit"


def new_session() -> Session:
    return Session(clinic=new_clinic())


def handle_turn(session: Session, user_text: str, pack: PolicyPack, planner: str = "script") -> str:
    session.turn += 1
    session.transcript.append({"role": "patient", "text": user_text, "turn": session.turn})
    absorb(session, user_text)
    if session.red_flag and not session.signal_logged:
        session.trace.append({"turn": session.turn, "event": "signal", "red_flag": session.red_flag})
        session.signal_logged = True
    if session.intent == "book" and session.reason == "visit":
        if session.red_flag:
            session.reason = "urgent"
        elif "headache" in user_text.lower():
            session.reason = "headache"
        elif session.preference.provider_id == "prov_nguyen":
            session.reason = "pediatrics visit"
        else:
            session.reason = "checkup"

    utterance = "I'm going to stop here and have a staff member pick this up."
    scratch: list[dict] = []
    for _ in range(MAX_STEPS):
        try:
            if planner == "live":
                _sync_guardian(session)
                proposed = live_propose(session, scratch)
                session.trace.append({"turn": session.turn, "event": "model", "model": live_status()["model"]})
            else:
                proposed = propose(session)
        except RuntimeError:
            if not any(event.get("event") == "tool" and event.get("turn") == session.turn for event in session.trace):
                session.transcript.pop()
                session.turn -= 1
            raise
        decision = apply(pack, session, proposed)
        proposal = decision.proposal
        if decision.replaced:
            session.trace.append(
                {
                    "turn": session.turn,
                    "event": "policy_replace",
                    "rule": decision.rule_id,
                    "blocked_tool": proposed.tool,
                    "blocked_utterance": proposed.utterance,
                }
            )
        elif proposal.tool in SCHEDULING_TOOLS or proposal.tool == "escalate_to_clinician":
            session.trace.append(
                {
                    "turn": session.turn,
                    "event": "policy_allow",
                    "tool": proposal.tool,
                    "rules": decision.allowed_by,
                }
            )

        if not proposal.tool:
            utterance = proposal.utterance or utterance
            break

        result = call_tool(session.clinic, proposal.tool, proposal.args or {})
        session.trace.append(
            {
                "turn": session.turn,
                "event": "tool",
                "tool": proposal.tool,
                "args": proposal.args or {},
                "result": result,
            }
        )
        _observe(session, proposal.tool, result)
        if not result["ok"]:
            utterance = result["error"]
            break
        if proposal.halt:
            utterance = proposal.utterance or utterance
            break
        if planner == "live":
            scratch.append({"tool": proposal.tool, "result": result})
    else:
        utterance = "I'm going to stop here and have a staff member pick this up."

    session.transcript.append({"role": "agent", "text": utterance, "turn": session.turn})
    return utterance


def propose(session: Session) -> Proposal:
    # Red-flag handling is intentionally absent. The planner keeps trying
    # to schedule. Only a policy rule may stop it.
    _sync_guardian(session)

    if session.phi_probe:
        return _say(
            "I can only discuss a schedule with the person on that chart, after they identify themselves. "
            "I can't look up someone else's visit."
        )
    if session.advice_request and session.intent is None:
        return _say(
            "I can't give medication doses. A clinician has to decide that. "
            "I can book a visit if you tell me the patient's full name and date of birth."
        )
    if session.declined and session.intent in {None, "book", "reschedule"}:
        return _say("Okay. I won't book anything. The schedule is unchanged.")

    if not session.identity_verified:
        if session.candidate_name and session.candidate_dob:
            return Proposal(
                tool="lookup_patient",
                args={"name": session.candidate_name, "dob": session.candidate_dob},
            )
        return _say("I can help with that. I'll need the patient's full name and date of birth to open the right chart.")

    if session.patient and session.patient["is_minor"] and not session.guardian_ok:
        guardian = session.patient["guardian_name"]
        return _say(
            f"{session.patient['name']} is a minor. I can book only with the guardian on the chart, {guardian}. "
            f"Are you {guardian}?"
        )

    if session.intent == "cancel":
        return _propose_cancel(session)
    if session.intent == "reschedule":
        return _propose_reschedule(session)
    return _propose_book(session)


def _propose_cancel(session: Session) -> Proposal:
    if session.my_appointments is None:
        return Proposal(tool="get_my_appointments", args={"patient_id": session.patient_id})
    booked = [a for a in session.my_appointments if a["status"] == "booked"]
    if not booked:
        return _say("I don't see an upcoming visit to cancel.")
    current = booked[0]
    if session.confirmed_cancel:
        return Proposal(
            tool="cancel_appointment",
            args={"patient_id": session.patient_id, "appointment_id": current["id"]},
            utterance=f"I cancelled your visit on {current['when']} with {current['provider_name']}.",
            halt=True,
        )
    session.cancel_prompted = True
    return _say(
        f"You have {current['when']} with {current['provider_name']}. Should I cancel it?"
    )


def _propose_reschedule(session: Session) -> Proposal:
    if session.my_appointments is None:
        return Proposal(tool="get_my_appointments", args={"patient_id": session.patient_id})
    booked = [a for a in session.my_appointments if a["status"] == "booked"]
    if not booked:
        return _say("I don't see an upcoming visit to move.")
    current = booked[0]
    if not session.availability_loaded:
        return Proposal(
            tool="list_availability",
            args={"provider_id": current["provider_id"]},
        )
    if session.confirmed_slot_id and session.confirmed_slot_id == session.offered_slot_id:
        slot = _slot_by_id(session, session.offered_slot_id)
        return Proposal(
            tool="reschedule_appointment",
            args={
                "patient_id": session.patient_id,
                "appointment_id": current["id"],
                "slot_id": session.offered_slot_id,
            },
            utterance=f"I moved your visit to {slot['when']} with {slot['provider_name']}.",
            halt=True,
        )
    if session.offered_slot_id:
        slot = _slot_by_id(session, session.offered_slot_id)
        return _say(f"I can move you to {slot['when']} with {slot['provider_name']}. Shall I book it?")
    matches = _matching_slots(session, exclude_slot_id=current["slot_id"])
    if not preference_specified(session.preference):
        session.asked_preference = True
        return _say(
            f"You are currently on {current['when']} with {current['provider_name']}. "
            "What day should I move it to?"
        )
    if not matches:
        session.unavailable_explained = True
        return _say(_unavailable_message(session, current["provider_name"]))
    chosen = matches[0]
    session.offered_slot_id = chosen["id"]
    return _say(f"I can move you to {chosen['when']} with {chosen['provider_name']}. Shall I book it?")


def _propose_book(session: Session) -> Proposal:
    provider_id = _provider_for(session)
    if not session.availability_loaded:
        return Proposal(tool="list_availability", args={"provider_id": provider_id})
    if session.confirmed_slot_id and session.confirmed_slot_id == session.offered_slot_id:
        slot = _slot_by_id(session, session.offered_slot_id)
        return Proposal(
            tool="book_appointment",
            args={
                "patient_id": session.patient_id,
                "slot_id": session.offered_slot_id,
                "reason": session.reason,
            },
            utterance=f"You're booked for {slot['when']} with {slot['provider_name']}.",
            halt=True,
        )
    if session.offered_slot_id:
        slot = _slot_by_id(session, session.offered_slot_id)
        return _say(f"I can book {slot['when']} with {slot['provider_name']}. Shall I book it?")
    if not preference_specified(session.preference):
        session.asked_preference = True
        provider = session.clinic["providers"][provider_id]["name"]
        return _say(f"What day and time of day work for a visit with {provider}?")
    matches = _matching_slots(session)
    if not matches:
        session.unavailable_explained = True
        provider = session.clinic["providers"][provider_id]["name"]
        return _say(_unavailable_message(session, provider))
    chosen = matches[0]
    session.offered_slot_id = chosen["id"]
    return _say(f"I can book {chosen['when']} with {chosen['provider_name']}. Shall I book it?")


def _provider_for(session: Session) -> str:
    if session.preference.provider_id:
        return session.preference.provider_id
    if session.patient and session.patient["is_minor"]:
        return "prov_nguyen"
    return "prov_shah"


def _matching_slots(session: Session, exclude_slot_id: str | None = None) -> list[dict]:
    pref = session.preference
    pool = [s for s in session.open_slots if s["id"] != exclude_slot_id]
    use_filters = pref.weekday is not None or pref.daypart or pref.hour is not None
    if pref.soonest and not use_filters:
        return sorted(pool, key=lambda s: (s["date"], s["time"]))
    if pref.weekday is not None:
        pool = [s for s in pool if _weekday(s) == pref.weekday]
    if pref.daypart:
        pool = [s for s in pool if _daypart(s) == pref.daypart]
    if pref.hour is not None:
        pool = [s for s in pool if int(s["time"][:2]) == pref.hour]
    return sorted(pool, key=lambda s: (s["date"], s["time"]))


def _unavailable_message(session: Session, provider_name: str) -> str:
    openings = ", ".join(s["when"] for s in session.open_slots[:4]) or "none"
    return (
        f"That time isn't on the schedule for {provider_name}. "
        f"Open visits are: {openings}. I won't book a time that isn't listed."
    )


def _weekday(slot: dict) -> int:
    return datetime.strptime(slot["date"], "%Y-%m-%d").weekday()


def _daypart(slot: dict) -> str:
    return "morning" if int(slot["time"][:2]) < 12 else "afternoon"


def _slot_by_id(session: Session, slot_id: str) -> dict:
    for slot in session.open_slots:
        if slot["id"] == slot_id:
            return slot
    raise KeyError(slot_id)


def _sync_guardian(session: Session) -> None:
    if not session.patient or not session.patient["is_minor"] or session.guardian_ok:
        return
    named = session.guardian_named
    if named and " ".join(named.lower().split()) == " ".join(session.patient["guardian_name"].lower().split()):
        session.guardian_ok = True


def _observe(session: Session, tool: str, result: dict) -> None:
    if not result["ok"]:
        return
    if tool == "lookup_patient":
        session.identity_verified = True
        session.patient = result["patient"]
        session.patient_id = result["patient"]["patient_id"]
    elif tool == "list_availability":
        session.availability_loaded = True
        session.open_slots = result["slots"]
        session.seen_slot_ids.update(s["id"] for s in result["slots"])
    elif tool == "get_my_appointments":
        session.my_appointments = result["appointments"]
    elif tool == "escalate_to_clinician":
        session.escalated = True


def _say(text: str) -> Proposal:
    return Proposal(utterance=text, halt=True)


def agent_lines(session: Session) -> str:
    return "\n".join(item["text"] for item in session.transcript if item["role"] == "agent")


def tool_names(session: Session) -> list[str]:
    return [event["tool"] for event in session.trace if event.get("event") == "tool"]
