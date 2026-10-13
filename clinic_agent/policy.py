"""Enforceable conversation policy.

The planner proposes. This module may replace the proposal before a tool
runs. Version 0 already guards identity, confirmation, minors, grounding,
and other people's schedules. It does not yet act on a red-flag signal.
That missing rule is what the evaluation loop is allowed to install.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from clinic_agent.clinic import SCHEDULING_TOOLS

MUTATING = {
    "book_appointment",
    "cancel_appointment",
    "reschedule_appointment",
    "get_my_appointments",
}


@dataclass
class Proposal:
    utterance: str | None = None
    tool: str | None = None
    args: dict | None = None
    halt: bool = False

    def to_dict(self) -> dict:
        return {
            "utterance": self.utterance,
            "tool": self.tool,
            "args": self.args,
            "halt": self.halt,
        }


@dataclass
class Decision:
    proposal: Proposal
    replaced: bool = False
    rule_id: str | None = None
    allowed_by: list[str] = field(default_factory=list)


@dataclass
class Rule:
    id: str
    kind: str
    params: dict
    origin: dict | None = None

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "params": self.params, "origin": self.origin}


@dataclass
class PolicyPack:
    version: str
    rules: list[Rule]

    def to_dict(self) -> dict:
        return {"version": self.version, "rules": [r.to_dict() for r in self.rules]}

    @staticmethod
    def from_dict(data: dict) -> "PolicyPack":
        rules = [
            Rule(id=r["id"], kind=r["kind"], params=r["params"], origin=r.get("origin"))
            for r in data["rules"]
        ]
        return PolicyPack(version=data["version"], rules=rules)


def baseline_pack() -> PolicyPack:
    return PolicyPack(
        version="v0",
        rules=[
            Rule("phi_session_boundary", "phi_gate", {}),
            Rule("identity_before_chart", "identity_gate", {}),
            Rule("guardian_for_minor", "minor_gate", {}),
            Rule("confirm_named_slot", "confirmation_gate", {}),
            Rule("ground_slots_in_schedule", "grounding_gate", {}),
        ],
    )


def escalation_utterance(category: str | None) -> str:
    if category == "self_harm":
        return (
            "I'm sorry you're dealing with this. I won't schedule it as a routine visit, "
            "and I'm alerting the on-call clinician now. "
            "If you're in immediate danger, call 911. "
            "You can also call or text 988, the Suicide and Crisis Lifeline."
        )
    if category:
        return (
            "That needs urgent clinical help, not a routine booking. "
            "I'm alerting the on-call clinician now. "
            "If this is an emergency, call 911. I can't book this visit."
        )
    return (
        "I'm alerting the on-call clinician instead of booking. "
        "If this is an emergency, call 911."
    )


def _already_utterance(category: str | None) -> str:
    if category == "self_harm":
        return (
            "I've already alerted the on-call clinician. "
            "If you're in immediate danger, call 911, or call or text 988."
        )
    return (
        "I've already alerted the on-call clinician. "
        "If this is an emergency, call 911. I can't book a routine visit for this."
    )


def _escalate(session, category: str | None) -> Proposal:
    return Proposal(
        tool="escalate_to_clinician",
        args={
            "category": category or "unspecified",
            "summary": session.red_flag_excerpt or (session.intent or "scheduling"),
            "patient_id": session.patient_id,
        },
        utterance=escalation_utterance(category),
        halt=True,
    )


def _say(text: str) -> Proposal:
    return Proposal(utterance=text, halt=True)


def apply(pack: PolicyPack, session, proposal: Proposal) -> Decision:
    for rule in pack.rules:
        replacement = _consider(rule, session, proposal)
        if replacement is not None:
            return Decision(proposal=replacement, replaced=True, rule_id=rule.id)
    allowed = [r.id for r in pack.rules if _relevant(r, proposal)]
    return Decision(proposal=proposal, replaced=False, allowed_by=allowed)


def _relevant(rule: Rule, proposal: Proposal) -> bool:
    if proposal.tool is None:
        return False
    if rule.kind == "phi_gate":
        return proposal.tool in MUTATING or proposal.tool == "lookup_patient"
    if rule.kind == "identity_gate":
        return proposal.tool in MUTATING
    if rule.kind == "minor_gate":
        return proposal.tool == "book_appointment"
    if rule.kind == "confirmation_gate":
        return proposal.tool in {"book_appointment", "reschedule_appointment", "cancel_appointment"}
    if rule.kind == "grounding_gate":
        return proposal.tool in {"book_appointment", "reschedule_appointment"}
    return False


def _consider(rule: Rule, session, proposal: Proposal) -> Proposal | None:
    kind = rule.kind
    if kind == "red_flag_gate":
        return _red_flag(rule, session, proposal)
    if kind == "phi_gate":
        if session.phi_probe and proposal.tool:
            return _say(
                "I can only discuss a schedule with the person on that chart, after they identify themselves. "
                "I can't look up someone else's visit."
            )
        return None
    if kind == "identity_gate":
        if proposal.tool in MUTATING and not session.identity_verified:
            return _say("I need the patient's full name and date of birth before I open a chart or change a visit.")
        pid = (proposal.args or {}).get("patient_id")
        if proposal.tool in MUTATING and pid and session.patient_id and pid != session.patient_id:
            return _say("I can't change or read a chart that isn't the one we just verified.")
        return None
    if kind == "minor_gate" and proposal.tool == "book_appointment":
        patient = session.patient or {}
        if patient.get("is_minor") and not session.guardian_ok:
            guardian = patient.get("guardian_name") or "the guardian on the chart"
            return _say(f"I can book for this child only with {guardian}. Are you {guardian}?")
        return None
    if kind == "confirmation_gate":
        if proposal.tool in {"book_appointment", "reschedule_appointment"}:
            slot_id = (proposal.args or {}).get("slot_id")
            if session.confirmed_slot_id != slot_id:
                return _say("I won't book that until you confirm that specific visit.")
        if proposal.tool == "cancel_appointment" and not session.confirmed_cancel:
            return _say("I won't cancel it until you confirm.")
        return None
    if kind == "grounding_gate" and proposal.tool in {"book_appointment", "reschedule_appointment"}:
        slot_id = (proposal.args or {}).get("slot_id")
        if slot_id not in session.seen_slot_ids:
            return _say("I can only book a time the schedule just returned.")
        return None
    return None


def _red_flag(rule: Rule, session, proposal: Proposal) -> Proposal | None:
    scope = rule.params.get("scope")
    triggered = False
    if scope == "active_signal" and session.red_flag:
        triggered = True
    if scope == "any_book_intent" and session.intent in {"book", "reschedule", "cancel"}:
        triggered = True
    if not triggered:
        return None
    if proposal.tool == "escalate_to_clinician":
        return None
    category = session.red_flag
    if session.escalated:
        return _say(_already_utterance(category))
    return _escalate(session, category)
