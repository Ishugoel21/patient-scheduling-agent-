"""Fictional clinic chart. Tools are the only writers, and they are narrow."""

from __future__ import annotations

import copy
from datetime import datetime


def format_when(date: str, time: str) -> str:
    dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
    hour = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{dt.strftime('%A')}, {dt.strftime('%B')} {dt.day}, {dt.year} at {hour}:{dt.strftime('%M')} {ampm}"


def _slot(slot_id: str, provider_id: str, date: str, time: str, status: str = "open") -> dict:
    return {
        "id": slot_id,
        "provider_id": provider_id,
        "date": date,
        "time": time,
        "status": status,
    }


def _patient(pid: str, name: str, dob: str, phone_last4: str, guardian: str | None) -> dict:
    return {
        "id": pid,
        "name": name,
        "dob": dob,
        "phone_last4": phone_last4,
        "guardian_name": guardian,
        "is_minor": guardian is not None,
    }


def new_clinic() -> dict:
    """Return a fresh chart. Dates are frozen so runs replay exactly.

    October 13, 2026 is a Tuesday. Openings are weekday hours only.
    Phone last-four is on the chart and is not returned by any tool.
    """
    providers = {
        "prov_shah": {"id": "prov_shah", "name": "Dr. Shah", "specialty": "family medicine"},
        "prov_okonkwo": {"id": "prov_okonkwo", "name": "Dr. Okonkwo", "specialty": "cardiology"},
        "prov_nguyen": {"id": "prov_nguyen", "name": "Dr. Nguyen", "specialty": "pediatrics"},
    }
    patients = {
        "pat_maya": _patient("pat_maya", "Maya Chen", "1990-04-12", "4419", None),
        "pat_luis": _patient("pat_luis", "Luis Ortega", "1985-11-02", "7781", None),
        "pat_samir": _patient("pat_samir", "Samir Patel", "2016-08-03", "2204", "Priya Patel"),
    }
    slots = {
        "shah_20261015_1500": _slot("shah_20261015_1500", "prov_shah", "2026-10-15", "15:00"),
        "shah_20261016_0900": _slot("shah_20261016_0900", "prov_shah", "2026-10-16", "09:00"),
        "shah_20261019_1030": _slot("shah_20261019_1030", "prov_shah", "2026-10-19", "10:30"),
        "shah_20261020_0900": _slot("shah_20261020_0900", "prov_shah", "2026-10-20", "09:00", "taken"),
        "nguyen_20261016_1100": _slot("nguyen_20261016_1100", "prov_nguyen", "2026-10-16", "11:00"),
        "okonkwo_20261015_1100": _slot("okonkwo_20261015_1100", "prov_okonkwo", "2026-10-15", "11:00"),
        "okonkwo_20261021_1400": _slot("okonkwo_20261021_1400", "prov_okonkwo", "2026-10-21", "14:00", "taken"),
    }
    appointments = {
        "appt_maya_1": {
            "id": "appt_maya_1",
            "patient_id": "pat_maya",
            "slot_id": "shah_20261020_0900",
            "reason": "follow-up",
            "status": "booked",
        },
        "appt_luis_1": {
            "id": "appt_luis_1",
            "patient_id": "pat_luis",
            "slot_id": "okonkwo_20261021_1400",
            "reason": "cardiology follow-up",
            "status": "booked",
        },
    }
    return {
        "providers": providers,
        "patients": patients,
        "slots": slots,
        "appointments": appointments,
        "escalations": [],
    }


def panel_names(clinic: dict) -> list[str]:
    names = [p["name"] for p in clinic["patients"].values()]
    guardians = [p["guardian_name"] for p in clinic["patients"].values() if p["guardian_name"]]
    # Longest first so "Priya Patel" is not swallowed by a shorter token.
    return sorted(set(names + guardians), key=len, reverse=True)


def _fail(error: str) -> dict:
    return {"ok": False, "error": error}


def _public_patient(patient: dict) -> dict:
    """Minimum fields the next conversational step needs. No phone number."""
    return {
        "patient_id": patient["id"],
        "name": patient["name"],
        "dob": patient["dob"],
        "is_minor": patient["is_minor"],
        "guardian_name": patient["guardian_name"],
    }


def _appt_view(clinic: dict, appt: dict) -> dict:
    slot = clinic["slots"][appt["slot_id"]]
    provider = clinic["providers"][slot["provider_id"]]
    return {
        "id": appt["id"],
        "patient_id": appt["patient_id"],
        "slot_id": appt["slot_id"],
        "provider_id": slot["provider_id"],
        "provider_name": provider["name"],
        "date": slot["date"],
        "time": slot["time"],
        "when": format_when(slot["date"], slot["time"]),
        "reason": appt["reason"],
        "status": appt["status"],
    }


def lookup_patient(clinic: dict, name: str, dob: str) -> dict:
    wanted = " ".join(name.lower().split())
    for patient in clinic["patients"].values():
        if patient["name"].lower() == wanted and patient["dob"] == dob:
            return {"ok": True, "patient": _public_patient(patient)}
    # Do not suggest other charts.
    return _fail("No chart matched that name and date of birth.")


def list_availability(clinic: dict, provider_id: str | None = None) -> dict:
    slots = []
    for slot in clinic["slots"].values():
        if slot["status"] != "open":
            continue
        if provider_id and slot["provider_id"] != provider_id:
            continue
        provider = clinic["providers"][slot["provider_id"]]
        slots.append(
            {
                "id": slot["id"],
                "provider_id": slot["provider_id"],
                "provider_name": provider["name"],
                "date": slot["date"],
                "time": slot["time"],
                "when": format_when(slot["date"], slot["time"]),
            }
        )
    slots.sort(key=lambda s: (s["date"], s["time"]))
    return {"ok": True, "slots": slots}


def get_my_appointments(clinic: dict, patient_id: str) -> dict:
    if patient_id not in clinic["patients"]:
        return _fail("Unknown patient.")
    rows = [
        _appt_view(clinic, appt)
        for appt in clinic["appointments"].values()
        if appt["patient_id"] == patient_id and appt["status"] == "booked"
    ]
    rows.sort(key=lambda r: (r["date"], r["time"]))
    return {"ok": True, "appointments": rows}


def book_appointment(clinic: dict, patient_id: str, slot_id: str, reason: str) -> dict:
    if patient_id not in clinic["patients"]:
        return _fail("Unknown patient.")
    slot = clinic["slots"].get(slot_id)
    if slot is None or slot["status"] != "open":
        return _fail("That visit is not open.")
    slot["status"] = "taken"
    appt_id = f"appt_new_{len(clinic['appointments']) + 1}"
    appt = {
        "id": appt_id,
        "patient_id": patient_id,
        "slot_id": slot_id,
        "reason": reason or "visit",
        "status": "booked",
    }
    clinic["appointments"][appt_id] = appt
    return {"ok": True, "appointment": _appt_view(clinic, appt)}


def cancel_appointment(clinic: dict, patient_id: str, appointment_id: str) -> dict:
    appt = clinic["appointments"].get(appointment_id)
    if appt is None or appt["patient_id"] != patient_id or appt["status"] != "booked":
        return _fail("That appointment is not on this patient's chart.")
    appt["status"] = "cancelled"
    clinic["slots"][appt["slot_id"]]["status"] = "open"
    return {"ok": True, "appointment": _appt_view(clinic, appt)}


def reschedule_appointment(clinic: dict, patient_id: str, appointment_id: str, slot_id: str) -> dict:
    appt = clinic["appointments"].get(appointment_id)
    if appt is None or appt["patient_id"] != patient_id or appt["status"] != "booked":
        return _fail("That appointment is not on this patient's chart.")
    new_slot = clinic["slots"].get(slot_id)
    if new_slot is None or new_slot["status"] != "open":
        return _fail("That visit is not open.")
    old_slot = clinic["slots"][appt["slot_id"]]
    # Both writes happen only after both checks. A failure leaves the chart as it was.
    old_slot["status"] = "open"
    new_slot["status"] = "taken"
    appt["slot_id"] = slot_id
    return {"ok": True, "appointment": _appt_view(clinic, appt)}


def escalate_to_clinician(clinic: dict, category: str, summary: str, patient_id: str | None) -> dict:
    record = {
        "id": f"esc_{len(clinic['escalations']) + 1}",
        "category": category,
        "summary": summary,
        "patient_id": patient_id,
    }
    clinic["escalations"].append(record)
    return {"ok": True, "escalation": record}


TOOLS = {
    "lookup_patient": lookup_patient,
    "list_availability": list_availability,
    "get_my_appointments": get_my_appointments,
    "book_appointment": book_appointment,
    "cancel_appointment": cancel_appointment,
    "reschedule_appointment": reschedule_appointment,
    "escalate_to_clinician": escalate_to_clinician,
}

SCHEDULING_TOOLS = {
    "list_availability",
    "book_appointment",
    "cancel_appointment",
    "reschedule_appointment",
    "get_my_appointments",
}


def call_tool(clinic: dict, name: str, args: dict) -> dict:
    fn = TOOLS[name]
    result = fn(clinic, **args)
    return copy.deepcopy(result)
