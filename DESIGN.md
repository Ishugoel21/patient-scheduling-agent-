# Design

A scheduler for a fictional panel, and a harness that installs one constraint when the agent treats an emergency as a booking. The exam clock, chart, and planner are deterministic, so the before-and-after score cannot wander. Typed chat can call a live model. Safety stays in the policy, which still vetoes the tool call.

## Agent

Each turn becomes signals: chart fields, a day preference, a hedge, a request for someone else's schedule, a medication question, and a red-flag label from a short lexicon. The planner then proposes the next question or tool, and it keeps looking for a visit after chest pain, trouble breathing, or self-harm. That is the hole.

Version 0 already stops five other failures: chart access before name and date of birth, booking before the patient confirms the slot just named, a minor without the guardian on the chart, a time the schedule tool did not return, and reading someone else's visit. Tools are the only writers. A taken slot cannot be booked, a cancel cannot touch another row, and reschedule writes both sides or neither.

A yes counts only for a slot already offered. Name plus date of birth selects a chart and does not prove the caller. Numeric dates are month/day/year. The calendar is frozen at 13 October 2026 so the score replays.

## Judge

Scenarios carry their own labels, including which visits are emergencies. The score uses the tool trace and the chart (whose row changed, whether the schedule returned that slot, whether a move left one visit) and the transcript (911, 988, no dose, no other patient's clock time).

A transcript-only judge cannot see a free slot, a wrong-row cancel, or a phone number leaving a tool. A trace-only judge cannot see a dose, a leaked time, or a yes that referred to nothing. A paraphrase the lexicon misses is `detector_miss`. This compiler has no patch for that, and the gate will not ship a rule that leaves it failing.

## Loop

`red_flag_ignored` compiles to two rules from the same evidence. `active_signal` blocks scheduling while that label is set and calls `escalate_to_clinician`. It names no symptom, so breathing and self-harm are covered along with chest pain. `any_book_intent` treats every scheduling request as unsafe. The suite is re-run. A patch is kept only when the score rises, every pass stays a pass, and no failure remains.

The broad rule fixes two emergencies, still asks for a chart on "I want to hurt myself," and breaks ordinary booking, cancel, and reschedule, so it is rejected. The signal rule is installed. Score: 10/13 to 13/13. The loop chooses a scope. It does not rewrite a prompt.

## AI use

Drafted with Cursor's coding agent. Not left to a prompt rewrite: emergency handling lives in the policy, a yes attaches only to a named slot, scenario labels are independent of the detector, and a patch that fixes emergencies by breaking ordinary booking is rejected. Edit this to say what you kept.
