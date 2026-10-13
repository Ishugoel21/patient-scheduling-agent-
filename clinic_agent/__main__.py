"""Run the scheduling agent, or the evaluation loop that patches it.

From the repository root:

    python -m clinic_agent chat
    python -m clinic_agent eval
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from clinic_agent.agent import handle_turn, new_session
from clinic_agent.evalkit import (
    ARTIFACTS,
    BLIND_SPOTS,
    SCENARIOS,
    apply_rule,
    compile_candidates,
    judge_gate,
    render_transcript,
    run_scenario,
    run_suite,
    score,
)
from clinic_agent.policy import PolicyPack, baseline_pack

BEFORE_FAILURES = {
    "emergency_chest_pain",
    "emergency_breathing",
    "emergency_self_harm",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m clinic_agent")
    sub = parser.add_subparsers(dest="command")

    chat = sub.add_parser("chat", help="Talk to the scheduling agent")
    chat.add_argument("--policy", choices=("v0", "v1"), default="v0")
    chat.add_argument("--demo", metavar="SCENARIO", help="Replay a scripted visit instead of typing")

    sub.add_parser("eval", help="Score v0, compile patches, keep the one that does not regress")
    serve_cmd = sub.add_parser("serve", help="Open the scheduling desk at http://127.0.0.1:8765")
    serve_cmd.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    if args.command == "chat":
        return chat_main(args.policy, args.demo)
    if args.command == "serve":
        from clinic_agent.web import serve

        serve(args.port)
        return 0
    return eval_main()


def chat_main(policy_name: str, demo: str | None) -> int:
    pack = load_policy(policy_name)
    if pack is None:
        print("No accepted policy yet. Run: python -m clinic_agent eval")
        return 1
    scenarios = {item.id: item for item in SCENARIOS}
    if demo:
        if demo not in scenarios:
            print("Known scenarios:")
            for item in SCENARIOS:
                print(f"  {item.id}")
            return 1
        result, session = run_scenario(scenarios[demo], pack)
        print(f"policy {pack.version}")
        print(render_transcript(session).rstrip())
        print(f"\n{result.scenario_id}: {'PASS' if result.passed else 'FAIL'}  {result.outcome}")
        return 0

    from clinic_agent.llm import live_status

    status = live_status()
    if not status["ready"]:
        print("Typed chat needs OPENAI_API_KEY in a .env file, or OPENAI_BASE_URL for a local model.")
        return 1
    print(f"Riverside demo clinic. Policy {pack.version}. Model {status['model']}.")
    print("Book, cancel, or reschedule. Type 'quit' to stop.")
    print("This agent does not give medical advice. Emergencies belong on 911, not on the schedule.")
    session = new_session()
    while True:
        try:
            line = input("\nPatient> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if line.lower() in {"quit", "exit"}:
            return 0
        if not line:
            continue
        before = len(session.trace)
        try:
            reply = handle_turn(session, line, pack, planner="live")
        except RuntimeError as exc:
            print(f"Agent> {exc}")
            continue
        for event in session.trace[before:]:
            print("  " + _short_event(event))
        print(f"Agent> {reply}")


def eval_main() -> int:
    _check_calendar()
    lines: list[str] = []

    def emit(text: str = "") -> None:
        print(text)
        lines.append(text)

    baseline = baseline_pack()
    before = run_suite(baseline)
    emit("Patient scheduling agent, evaluation loop")
    emit("Frozen clinic clock. No model call. The planner proposes; the policy can replace it.")
    emit("")
    emit(f"BEFORE  {baseline.version}  ({_rule_ids(baseline)})")
    _emit_table(emit, before)

    candidates = compile_candidates(before)
    if not candidates:
        emit("")
        emit("No red_flag_ignored failure. The loop has nothing to install.")
        return 1

    gates = []
    after_by_rule = {}
    emit("")
    emit("Patches compiled from red_flag_ignored. Each one is re-run on the full suite.")
    for rule in candidates:
        pack = apply_rule(baseline, rule)
        after = run_suite(pack)
        gate = judge_gate(before, after)
        gates.append((rule, pack, after, gate))
        after_by_rule[rule.id] = after
        emit("")
        emit(f"CANDIDATE  {rule.id}  scope={rule.params['scope']}")
        emit(f"  from: {', '.join(rule.origin['scenarios'])}")
        emit(f"  {rule.origin['note']}")
        emit(
            "  gate: "
            + ("ACCEPT" if gate.accepted else "REJECT")
            + f"  fixed={len(gate.fixed)}  regressions={len(gate.regressions)}  still_failing={len(gate.still_failing)}"
        )
        if gate.fixed:
            emit("  fixed: " + ", ".join(gate.fixed))
        if gate.still_failing:
            emit("  still failing: " + ", ".join(gate.still_failing))
        if gate.regressions:
            emit("  regressions: " + ", ".join(gate.regressions))

    accepted = [(rule, pack, after, gate) for rule, pack, after, gate in gates if gate.accepted]
    emit("")
    if len(accepted) != 1:
        emit(f"Expected one accepted patch, found {len(accepted)}.")
        _write_artifacts(lines, before, gates, None)
        return 1
    rule, pack, after, gate = accepted[0]
    emit(f"AFTER  {pack.version}")
    _emit_table(emit, after)
    before_score = score(before)
    after_score = score(after)
    emit("")
    emit(f"Score {before_score[0]}/{before_score[1]} -> {after_score[0]}/{after_score[1]}")
    emit(f"Installed {rule.id}. Rejected the broader scope from the same failures.")
    emit("")
    emit("Where a transcript-only judge is blind")
    for spot in BLIND_SPOTS:
        emit(f"- {spot}")

    problems = _contract_problems(before, gates, after)
    _write_artifacts(lines, before, gates, (rule, pack, after, gate))
    if problems:
        emit("")
        emit("Loop contract failed:")
        for problem in problems:
            emit(f"- {problem}")
        return 1
    emit("")
    emit(f"Wrote {ARTIFACTS}")
    return 0


def _contract_problems(before, gates, after) -> list[str]:
    """The demo has to actually close. These checks fail the process if it does not."""
    problems = []
    before_failed = {item.scenario_id for item in before if not item.passed}
    if before_failed != BEFORE_FAILURES:
        problems.append(f"v0 failures were {sorted(before_failed)}, expected {sorted(BEFORE_FAILURES)}")
    chest = next(item for item in before if item.scenario_id == "emergency_chest_pain")
    if "book_appointment" not in chest.tools:
        problems.append("v0 chest-pain visit did not book; the hole under test is gone")
    harm = next(item for item in before if item.scenario_id == "emergency_self_harm")
    if "escalate_to_clinician" in harm.tools:
        problems.append("v0 already escalated self-harm")
    if "book_appointment" not in harm.tools:
        problems.append("v0 self-harm visit did not complete a booking")
    broad = next(gate for rule, _pack, _after, gate in gates if rule.id == "escalate_any_scheduling")
    precise = next(gate for rule, _pack, _after, gate in gates if rule.id == "escalate_on_red_flag")
    if broad.accepted:
        problems.append("broad scheduling ban was accepted")
    if "emergency_self_harm" not in broad.still_failing:
        problems.append("broad ban unexpectedly fixed self-harm")
    if not precise.accepted:
        problems.append("signal-scoped rule was rejected")
    if not all(item.passed for item in after):
        missed = [item.scenario_id for item in after if not item.passed]
        problems.append(f"accepted policy still fails {missed}")
    if score(before)[0] >= score(after)[0]:
        problems.append("score did not rise")
    return problems


def _emit_table(emit, results) -> None:
    passed, total = score(results)
    for item in results:
        mark = "PASS" if item.passed else "FAIL"
        codes = ""
        if not item.passed:
            codes = "  " + ",".join(failure["code"] for failure in item.failures)
        emit(f"  {item.scenario_id:<24} {mark}  {item.outcome}{codes}")
    emit(f"  score {passed}/{total}")


def _write_artifacts(lines, before, gates, accepted) -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (ARTIFACTS / "before.json").write_text(_dump([item.to_public() for item in before]), encoding="utf-8")
    transcripts = ARTIFACTS / "transcripts"
    transcripts.mkdir(exist_ok=True)
    for scenario in SCENARIOS:
        if scenario.id not in BEFORE_FAILURES and scenario.id != "happy_book":
            continue
        _result, session = run_scenario(scenario, baseline_pack())
        (transcripts / f"{scenario.id}_v0.txt").write_text(render_transcript(session), encoding="utf-8")
    for rule, pack, after, gate in gates:
        payload = {
            "rule": rule.to_dict(),
            "gate": gate.to_dict(),
            "results": [item.to_public() for item in after],
        }
        (ARTIFACTS / f"candidate_{rule.id}.json").write_text(_dump(payload), encoding="utf-8")
    if accepted is None:
        return
    rule, pack, after, gate = accepted
    (ARTIFACTS / "policy_v1.json").write_text(_dump(pack.to_dict()), encoding="utf-8")
    (ARTIFACTS / "after.json").write_text(_dump([item.to_public() for item in after]), encoding="utf-8")
    for scenario in SCENARIOS:
        if scenario.id not in BEFORE_FAILURES and scenario.id != "happy_book":
            continue
        _result, session = run_scenario(scenario, pack)
        (transcripts / f"{scenario.id}_v1.txt").write_text(render_transcript(session), encoding="utf-8")


def _dump(payload) -> str:
    return json.dumps(payload, indent=2) + "\n"


def _rule_ids(pack: PolicyPack) -> str:
    return ", ".join(rule.id for rule in pack.rules)


def load_policy(name: str) -> PolicyPack | None:
    if name == "v0":
        return baseline_pack()
    path = ARTIFACTS / "policy_v1.json"
    if not path.exists():
        return None
    return PolicyPack.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _short_event(event: dict) -> str:
    from clinic_agent.evalkit import _event_line

    return _event_line(event)


def _check_calendar() -> None:
    # The suite names weekdays in prose. If these drift, the slot picker and the judge disagree.
    assert datetime(2026, 10, 13).strftime("%A") == "Tuesday"
    assert datetime(2026, 10, 15).strftime("%A") == "Thursday"
    assert datetime(2026, 10, 16).strftime("%A") == "Friday"
    assert datetime(2026, 10, 20).strftime("%A") == "Tuesday"
    assert datetime(2026, 10, 21).strftime("%A") == "Wednesday"


if __name__ == "__main__":
    sys.exit(main())
