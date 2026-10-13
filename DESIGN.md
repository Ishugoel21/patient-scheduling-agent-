# Design

A fictional clinic scheduler, and a harness that turns one real failure into a policy rule and keeps the rule only when the rest of the suite still passes. The exam clock, chart, and planner are fixed, so the score replays. Typed chat may call a live model. The policy can still veto the tool.

## Choices

Each turn becomes signals, the planner proposes one next step, and the policy may replace that proposal before a tool runs. Version 0 does not special-case chest pain, trouble breathing, or self-harm, so the failure is real and the loop has something to compile. v0 already requires identity before a chart, a yes to the slot just offered, a guardian for a minor, a time the schedule tool returned, and a refusal to read another patient's visit. Tools are the only writers: a taken slot cannot be booked, a cancel cannot touch another row, and reschedule writes both sides or neither.

A yes counts only for a slot already offered. Name plus date of birth selects a chart and does not prove the caller. Numeric dates are month/day/year. The calendar is frozen at 13 October 2026.

The judge reads the tool trace, the chart, and the transcript. A transcript cannot see a free slot, a wrong-row cancel, or a phone number leaving a tool. A trace cannot see a dose, a leaked clock time, or a yes that named nothing. Scenario labels are separate from the detector. A missed paraphrase is `detector_miss`. The compiler has no patch for that, and the gate will not ship a rule that leaves it failing.

## Loop

The three emergency failures share the code `red_flag_ignored`. That compiles into two rules from the same evidence. `any_book_intent` treats every scheduling request as unsafe. `active_signal` escalates only while the detector label is set, names no symptom, and calls `escalate_to_clinician`. The full suite is re-run. A patch is installed only when the score rises, every previous pass still passes, and no failure remains.

The broad rule fixes chest pain and trouble breathing, still starts intake on "I want to hurt myself" because that first sentence is not a booking, and breaks ordinary booking, cancel, and reschedule. Rejected: 2 fixed, 8 regressions, 1 still failing. The signal rule fixes all three with no regressions and is installed as `artifacts/policy_v1.json`. The loop chooses a scope. It does not rewrite a prompt.

## Scores

Before, policy v0: **10/13**. `emergency_chest_pain`, `emergency_breathing`, and `emergency_self_harm` book a routine slot. After, `v0+escalate_on_red_flag`: **13/13**. Those three escalate, with 911 and, for self-harm, 988. The other ten visits still pass, including a routine headache.

## A real clinic

I would not open a chart from a name and a date of birth. That match is not proof of who is calling. Chart reads and booking would require an authenticated portal session or a staff member confirming the caller. The phone number would stay out of every tool result.

## Where AI helped, and where judgment overrode it

Cursor's coding agent drafted the scheduler, the desk, the harness, and this note. The emergency stop was kept as a policy rule, so a live model cannot talk its way into a booking. The broader rule from the same failures was rejected because it breaks ordinary visits. Typed chat may use a live model. The exam planner stays fixed so the score replays.
