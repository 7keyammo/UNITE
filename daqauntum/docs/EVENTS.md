# DaQauntum Events

One bus, one direction of flow:

```text
source → EventBus (normalize, suppress, persist) → ReactionEngine → notify / queue / propose
```

Nothing in this pipeline executes a tool or changes external state. Sources
observe, rules advise, and any action a rule proposes goes back through the
permission-gated tool path for a person to approve.

## Suppression, and why science opts out

The bus deduplicates. Identical readings from a chatty sensor collapse into one
stored event with a repeat counter, debounced and rate-limited per kind. For
telemetry — a door sensor reporting "closed" forty times a minute — that is
correct and keeps the record readable.

For science it is wrong. Two measurements of 1.0 m are two facts, not one fact
seen twice. An analysis run over deduplicated readings is an analysis of data
that was silently altered, and nothing downstream would look wrong.

So every scientific event is built through `science/events.py::_event`, which
sets

```python
dedupe_key = f"science:{kind}:{unique}"
```

where `unique` is the id of the object being reported. Two measurements are
always two distinct keys, so suppression can never discard one, while the bus
keeps its useful behaviour for everything else.

`evaluations/science_smoke_test.py::test_identical_measurements_are_not_suppressed`
records five identical values and asserts five stored events and zero
suppressions. It is there to fail loudly if the dedupe key ever stops being
unique.

## Correlation ids

`Event.correlation_id` groups the events of one workflow. A whole
investigation — experiment created, hypothesis proposed, ten measurements,
analysis requested, analysis completed, evidence created, plots rendered, claim
made — shares one id and can be replayed in order.

The column was added to `device_events` by additive migration; existing rows
keep their data and read back with `correlation_id = None`.

## Scientific event kinds

All carry `source = "science"` and `attributes.scientific = True`.

| Kind | When |
| --- | --- |
| `experiment.created` | a new experiment is recorded |
| `experiment.started` / `experiment.completed` | status transitions |
| `hypothesis.created` / `hypothesis.updated` | proposed, linked or assessed |
| `measurement.recorded` | any value stored, raw or derived |
| `analysis.requested` / `.completed` / `.failed` | an analysis run |
| `simulation.requested` / `.completed` / `.failed` | a simulator run |
| `evidence.created` | evidence of any kind recorded |
| `claim.created` / `claim.updated` | a conclusion stated |
| `research.requested` / `.completed` / `.failed` | a literature lookup |
| `visualization.requested` / `.completed` | a plot rendered |

A failed run emits `.failed`, never `.completed`. The two are distinguishable
in the record because "the lookup failed" and "the lookup found nothing" are
different facts and must never collapse into each other.

## Ordering

Mutations persist before they publish. An event never reports a state the
database does not hold. A failed publish is written to memory as
`science_event_failed` rather than swallowed, and never rolls back a stored
fact.

## Subscribing

```python
def on_science(event):
    if event.kind == "measurement.recorded":
        print(event.attributes["quantity"], event.attributes["value"])

kernel.events.bus.subscribe(on_science, name="my-subscriber")
```

A subscriber that raises is recorded as degraded and does not stop the bus or
other subscribers.
