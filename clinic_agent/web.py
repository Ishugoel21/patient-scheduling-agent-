"""Local desk UI for the scheduler and the evaluation loop.

Bind to localhost only. The page is a view over the same session, policy,
and chart the command-line agent uses.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from clinic_agent.agent import handle_turn, new_session
from clinic_agent.clinic import format_when
from clinic_agent.evalkit import (
    ARTIFACTS,
    SCENARIOS,
    _event_line,
    apply_rule,
    compile_candidates,
    judge_gate,
    run_scenario,
    run_suite,
    score,
)
from clinic_agent.policy import PolicyPack, baseline_pack

STATIC = Path(__file__).resolve().parent / "static"
HOST = "127.0.0.1"
DEFAULT_PORT = 8765

_LOCK = threading.Lock()
_SESSIONS: dict[str, dict] = {}
_ACCEPTED: PolicyPack | None = None


def serve(port: int = DEFAULT_PORT) -> None:
    _load_accepted_from_disk()
    server = ThreadingHTTPServer((HOST, port), DeskHandler)
    print(f"Riverside desk  http://{HOST}:{port}", flush=True)
    print("Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()


def _load_accepted_from_disk() -> None:
    global _ACCEPTED
    path = ARTIFACTS / "policy_v1.json"
    if path.exists():
        _ACCEPTED = PolicyPack.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _pack(name: str) -> PolicyPack | None:
    if name == "v0":
        return baseline_pack()
    if name == "v1":
        return _ACCEPTED
    return None


def _meta() -> dict:
    from clinic_agent.llm import live_status

    pack = _pack("v1")
    status = live_status()
    return {
        "v1_ready": pack is not None,
        "v1_version": pack.version if pack else None,
        "live": status,
        "clock": "Tuesday, October 13, 2026",
        "scenarios": [
            {"id": item.id, "title": item.title, "why": item.why, "emergency": item.emergency}
            for item in SCENARIOS
        ],
    }


def _chart(clinic: dict) -> dict:
    appointments = []
    for appt in clinic["appointments"].values():
        if appt["status"] != "booked":
            continue
        slot = clinic["slots"][appt["slot_id"]]
        provider = clinic["providers"][slot["provider_id"]]
        patient = clinic["patients"][appt["patient_id"]]
        appointments.append(
            {
                "id": appt["id"],
                "patient": patient["name"],
                "when": format_when(slot["date"], slot["time"]),
                "provider": provider["name"],
                "reason": appt["reason"],
                "date": slot["date"],
                "time": slot["time"],
                "fresh": appt["id"].startswith("appt_new"),
            }
        )
    appointments.sort(key=lambda row: (row["date"], row["time"], row["patient"]))
    openings = []
    for slot in clinic["slots"].values():
        if slot["status"] != "open":
            continue
        provider = clinic["providers"][slot["provider_id"]]
        openings.append(
            {
                "id": slot["id"],
                "when": format_when(slot["date"], slot["time"]),
                "provider": provider["name"],
                "specialty": provider["specialty"],
                "date": slot["date"],
                "time": slot["time"],
            }
        )
    openings.sort(key=lambda row: (row["date"], row["time"]))
    return {
        "appointments": appointments,
        "openings": openings,
        "escalations": [
            {"id": item["id"], "category": item["category"], "summary": item["summary"]}
            for item in clinic["escalations"]
        ],
    }


def _view(held: dict, grade: dict | None = None) -> dict:
    session = held["session"]
    pack = held["pack"]
    patient = session.patient or {}
    return {
        "id": held["id"],
        "policy": held["policy"],
        "policy_version": pack.version,
        "rules": [rule.id for rule in pack.rules],
        "clock": "Tuesday, October 13, 2026",
        "transcript": session.transcript,
        "events": [
            {"turn": event["turn"], "kind": event.get("event", "tool"), "text": _event_line(event)}
            for event in session.trace
        ],
        "signals": {
            "verified_name": patient.get("name"),
            "intent": session.intent,
            "red_flag": session.red_flag,
            "escalated": session.escalated,
            "guardian_ok": session.guardian_ok,
        },
        "chart": _chart(session.clinic),
        "grade": grade,
    }


def _open_session(policy: str) -> dict:
    pack = _pack(policy)
    if pack is None:
        raise LookupError("v1")
    held = {"id": uuid4().hex[:12], "policy": policy, "pack": pack, "session": new_session()}
    with _LOCK:
        _SESSIONS[held["id"]] = held
    return _view(held)


def _reply(session_id: str, text: str) -> dict:
    with _LOCK:
        held = _SESSIONS.get(session_id)
        if held is None:
            raise KeyError(session_id)
        handle_turn(held["session"], text, held["pack"], planner="live")
        return _view(held)


def _replay(policy: str, scenario_id: str) -> dict:
    pack = _pack(policy)
    if pack is None:
        raise LookupError("v1")
    scenario = next((item for item in SCENARIOS if item.id == scenario_id), None)
    if scenario is None:
        raise KeyError(scenario_id)
    result, session = run_scenario(scenario, pack)
    held = {"id": uuid4().hex[:12], "policy": policy, "pack": pack, "session": session}
    with _LOCK:
        _SESSIONS[held["id"]] = held
    grade = {
        "scenario_id": result.scenario_id,
        "title": result.title,
        "passed": result.passed,
        "outcome": result.outcome,
        "codes": [failure["code"] for failure in result.failures],
    }
    return _view(held, grade)


def _rows(results) -> list[dict]:
    return [
        {
            "id": item.scenario_id,
            "title": item.title,
            "passed": item.passed,
            "outcome": item.outcome,
            "codes": [failure["code"] for failure in item.failures],
        }
        for item in results
    ]


def _run_eval() -> dict:
    global _ACCEPTED
    baseline = baseline_pack()
    before = run_suite(baseline)
    candidates = []
    accepted_pack = None
    accepted_after = None
    for rule in compile_candidates(before):
        pack = apply_rule(baseline, rule)
        after = run_suite(pack)
        gate = judge_gate(before, after)
        candidates.append(
            {
                "id": rule.id,
                "scope": rule.params.get("scope"),
                "note": (rule.origin or {}).get("note", ""),
                "from": (rule.origin or {}).get("scenarios", []),
                "accepted": gate.accepted,
                "fixed": gate.fixed,
                "regressions": gate.regressions,
                "still_failing": gate.still_failing,
            }
        )
        if gate.accepted:
            accepted_pack = pack
            accepted_after = after
    if accepted_pack is not None and accepted_after is not None:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        (ARTIFACTS / "policy_v1.json").write_text(
            json.dumps(accepted_pack.to_dict(), indent=2) + "\n",
            encoding="utf-8",
        )
        with _LOCK:
            _ACCEPTED = accepted_pack
    before_score = score(before)
    after_score = score(accepted_after) if accepted_after is not None else None
    return {
        "before": {"version": baseline.version, "passed": before_score[0], "total": before_score[1], "rows": _rows(before)},
        "candidates": candidates,
        "after": None
        if accepted_after is None
        else {
            "version": accepted_pack.version,
            "passed": after_score[0],
            "total": after_score[1],
            "rows": _rows(accepted_after),
        },
        "v1_ready": accepted_pack is not None,
    }


class DeskHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        print(f"  {self.address_string()}  {fmt % args}")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/meta":
            self._json(200, _meta())
            return
        static = {"/": "index.html", "/app.css": "app.css", "/app.js": "app.js"}.get(path)
        if static is None:
            self._json(404, {"error": "Not found"})
            return
        self._file(STATIC / static)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
            if path == "/api/sessions":
                self._json(200, _open_session(payload.get("policy", "v0")))
                return
            if path == "/api/replay":
                self._json(200, _replay(payload.get("policy", "v0"), payload["scenario_id"]))
                return
            if path == "/api/eval":
                self._json(200, _run_eval())
                return
            if path.startswith("/api/sessions/") and path.endswith("/messages"):
                session_id = path.split("/")[3]
                text = (payload.get("text") or "").strip()
                if not text:
                    self._json(400, {"error": "Say something to send."})
                    return
                self._json(200, _reply(session_id, text))
                return
            self._json(404, {"error": "Not found"})
        except LookupError:
            self._json(409, {"error": "Run the evaluation before opening the improved policy."})
        except KeyError:
            self._json(404, {"error": "That visit or scenario is no longer on the desk."})
        except RuntimeError as exc:
            self._json(503, {"error": str(exc)})
        except (json.JSONDecodeError, TypeError, ValueError):
            self._json(400, {"error": "The desk could not read that request."})

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path) -> None:
        if not path.is_file():
            self._json(404, {"error": "Missing desk file"})
            return
        kind = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
        }.get(path.suffix, "application/octet-stream")
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
