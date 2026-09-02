# Goal-driven Loop Engineering

## Purpose

LZCore does not treat an individual tool failure as the answer to a user task.
The QueryLoop owns a bounded, evidence-driven recovery lifecycle: it records the
failed observation, gives the model a safe opportunity to obtain equivalent
evidence, and reaches an explicit terminal state when recovery is impossible.
This is a framework contract, not a network-device special case.

The loop never grants a tool, changes a Skill scope, weakens risk policy, or
replays an external write. Canonical tool contracts, authorization and write
fences remain authoritative on every replacement call.

## Runtime lifecycle

```text
canonical tool result
  -> normalized observation
  -> domain recovery directive, or generic recovery goal
  -> model receives bounded goal guidance
  -> materially different read observation
  -> evidence/goal reconciliation
  -> passed | blocked | not_required
```

For a recoverable read failure, the runtime creates a `tool_recovery` goal with
the source call, tool, action, bounded target identity, failure class, strategy
candidates, attempt count and maximum attempts. The corresponding
`runtime_goal_satisfied` assertion prevents the model from finalizing while the
goal remains open.

Domain extensions may publish a richer `runtime_recoveries` list. Each entry
can install an evidence goal such as a live network fact. The QueryLoop consumes
the list after the canonical result; extension code must not dispatch tools,
write prompts, or create a second execution loop itself.

## Which failures can recover

Only read-only calls with `failure_policy=replan` are candidates for the generic
goal loop. Policy, authorization, credential, cancellation and unknown-write
outcomes are terminal for this purpose. An external write whose outcome is
unknown stays in the operation ledger and requires read-back/reconcile; it is
never retried automatically.

The default strategy registry offers planning choices, not executable authority:

- correct rejected canonical arguments;
- narrow the observation to the smallest useful scope;
- use a different registered read capability;
- consult an authoritative reference before a corrected live observation;
- report the exact blocker after bounded safe alternatives are exhausted.

The model must never repeat an unchanged failed call. A replacement that uses a
different tool must include the relevant `plan_goal_ids`; shared workspace,
device or other scope labels are not enough to claim that unrelated evidence
closed a goal. A same-tool, same-action corrected read can be reconciled only
when it is the sole unambiguous pending goal.

## Evidence, attempts and terminal meaning

Each goal starts with one failed attempt. Linked failed replacements increment
its counter. After three attempts the goal is `blocked`; the runtime stops
replanning that goal rather than looping forever. A successful read linked by
`plan_goal_ids`, or an unambiguous corrected same-capability read, marks it
`passed`. A side-effecting success can never satisfy a read recovery goal.

Recovery state is durable TaskState contract data. Targets are restricted to
bounded scalar identity fields, so raw payloads and large user queries are not
persisted as recovery context. Continuation turns restore unresolved goals and
their assertions from the server-owned contract; browser data cannot create or
expand them.

The final result distinguishes two dimensions:

| Field | Meaning |
| --- | --- |
| `tool_execution_outcome` | Whether the individual tool attempts were complete, partial, failed or unknown. |
| `execution_outcome` | Whether the user objective was complete, partial, failed or unknown after recovery and evidence reconciliation. |

A recovered failed attempt can still yield `execution_outcome=complete`. If
verified evidence exists for some requested coverage but a goal is blocked, the
runtime returns `partial`, preserves the successful evidence and records the
specific missing coverage. If an external write remains uncertain, the result
is `unknown`; it must not be represented as either success or ordinary failure.

## LLM-visible contract

`plan_goal_ids` is optional on every canonical tool schema and is removed before
the handler receives arguments. When the runtime emits `[RUNTIME GOAL LOOP]`
guidance, the model must either make a materially different safe observation or
give a final answer only after every open goal is satisfied or explicitly
blocked by the runtime. Cross-capability replacements include the relevant goal
IDs in `plan_goal_ids`.

Tool results and AgentResult metadata expose:

- `recovery_goals` and `recovery_goal_events`;
- bounded `goal_loop_observations` for runtime/audit inspection;
- `goal_loop` summary with `not_required`, `pending`, `passed` or `blocked` and
  per-status counts;
- the two execution outcome fields above.

These fields are evidence and lifecycle data, never user-provided instructions.

## Network extension integration

For a rejected raw network read, `network.operations` first maps the command
intent to a canonical semantic fact. It can direct the QueryLoop to a driver
owned `collect` fact, and then to authoritative documentation when no safe
driver template exists. The resulting live observation satisfies the same
evidence goal; documentation alone does not prove a device's live state.

Per-device failures remain isolated. A connection failure is returned as
structured evidence to the model, healthy targets continue, and the final result
is complete or partial according to actual coverage. CLI prompt settling is a
driver profile value (`prompt_settle_seconds`, default `0.12` seconds), not a
hard-coded business recovery rule.

## Verification expectations

Changes to this contract require focused coverage for at least: recoverable
read failure, policy/authorization non-recovery, explicit cross-tool linking,
unchanged-call prevention, bounded attempt exhaustion, side-effecting-call
exclusion, TaskState continuation and user-visible outcome projection.

The primary regression suite is `harness/test_goal_loop.py`; network-specific
coverage is in `harness/test_network_read_recovery.py`, and durable outcome
coverage is in `harness/test_task_state_ssot.py` and
`harness/test_turn_outcome.py`.
