# DaQauntum v0.5 — Scientific Core

The scientific core is the part of DaQauntum that keeps different kinds of
knowledge distinguishable. Everything else in this document follows from that
one job.

## The distinction that defines the product

A number in a scientific record can have arrived in several ways, and they are
not interchangeable:

| Kind | Where it came from |
| --- | --- |
| `MEASUREMENT` | somebody or some instrument read it off the world |
| `OBSERVATION` | a qualitative human observation |
| `CALCULATION` | derived deterministically from other evidence |
| `SIMULATION` | produced by a model, not by the world |
| `LITERATURE` | sourced from published work or an imported dataset |
| `AI_INTERPRETATION` | a language model's reading of the data |
| `HUMAN_INTERPRETATION` | the investigator's reading of the data |

Only `MEASUREMENT` and `OBSERVATION` are empirical — `EvidenceKind.is_empirical`
is the single test. The ordering above runs from most directly tied to the
physical world to most interpretive, and that is *not* a ranking of importance:
an AI interpretation can be the most valuable thing in an experiment. It is a
reminder that these are different epistemic categories and a conclusion that
silently changes category is a conclusion that has been overstated.

A language model's fluent paragraph about your data is not a measurement of
your data. That is the failure this core is built to make structurally
impossible rather than merely discouraged.

## Objects

- **Experiment** — the question, the variables, the apparatus, the procedure.
- **Hypothesis** — a proposed answer. Its status vocabulary is `proposed`,
  `supported`, `contradicted`, `inconclusive`, `withdrawn`. There is
  deliberately no `proven`: a hypothesis accumulates support, it does not
  graduate to truth, and a vocabulary without the word cannot casually produce
  a UI that says it.
- **Measurement** — one value, with a unit, an uncertainty and a provenance.
  `derived` marks a calculated value; `is_reading` is true only for values
  somebody actually read off an instrument.
- **Evidence** — a statement with a `kind` that is required and has no default.
- **Claim** — a conclusion, with the evidence ids it rests on. Every id must
  resolve; a claim citing a record that is not there is unfalsifiable.
- **Provenance** — `HUMAN`, `SENSOR`, `CALCULATED`, `SIMULATED`, `AI`,
  `IMPORTED`, with the agent, the inputs and the method.

`Measurement.evidence_kind` is computed from the record rather than stored
beside it, so the two can never disagree: derived → `CALCULATION`, simulated
provenance → `SIMULATION`, imported → `LITERATURE`, otherwise `MEASUREMENT`.

## Units and uncertainty

`science/units.py` implements SI dimensional analysis over the seven base
dimensions with no third-party dependency. `Quantity` carries an SI value, a
dimension vector and an optional uncertainty; arithmetic propagates uncertainty
in quadrature to first order, and adding a length to a mass raises
`DimensionalityError` naming both dimensions.

Offset units (Celsius, Fahrenheit) convert correctly on their own and are
refused inside composite units, where the offset has no consistent meaning.

The layer is dependency-free by choice, and cross-checked against Pint where
Pint is installed (`evaluations/units_smoke_test.py`, worst relative error
5.7e-16 across twenty conversions). Where the reference library is absent the
test skips with an explicit note that the conversions are *not* independently
verified, rather than reporting a pass it did not earn.

## Analysis

`science/analysis/motion.py` computes displacement, average velocity,
per-interval velocities, average acceleration and an ordinary least-squares
fit. The equations are written out in plain Python rather than delegated to
NumPy, so a reader can check `(x2 - x1) / (t2 - t1)` against the `method`
string stored on the result.

Every result carries six things: the value, its unit, the equation, the ids of
the measurements it consumed, its provenance, and the `Evidence` object it
converts to. Failure modes are refusals rather than approximations — mismatched
series lengths raise instead of truncating, a repeated timestamp raises instead
of dividing by zero, and fewer than three samples reports that acceleration is
unavailable instead of inventing one from noise.

## Events

Scientific events ride the existing v0.4 bus. That bus deliberately suppresses
repeats, which is right for telemetry and **wrong for science**: two readings of
1.0 m are two facts, not one fact seen twice, and an analysis run on
deduplicated data is an analysis of data that was silently altered.

Every scientific event therefore carries a dedupe key unique to the object it
reports, so suppression can never discard a measurement while the bus keeps its
useful behaviour everywhere else. See `docs/EVENTS.md`.

## Validation

`science/validation.py` finds contradictions. It does not award truth.

Three verdicts: `FAIL` (contradicts something definite), `UNCERTAIN` (could not
be established either way), `PASS` (this specific check found no
contradiction).

**Fail-closed.** Anything the validator does not understand resolves to
`UNCERTAIN`, never `PASS`. A quantity absent from the physics registry is not
thereby validated. One uncertain check makes the whole report uncertain,
because the problem it would have caught is still unexamined, and a report with
no checks in it is uncertain rather than clean. The failure mode of the
alternative is a validator that certifies whatever it fails to understand —
blind exactly where the novel work is.

A `PASS` is the narrowest statement in the system, and the report says so in
the words it returns. There is no truth score, no confidence number and no AI
fact-checker.

## Backends

Adapters, never dependencies of the core.

- **SensorBackend** — `available()`, `describe()`, `read()`. A backend that
  cannot reach its instrument raises rather than returning a plausible number.
  `MockCartSensor` generates data from a kinematic model and its `simulated`
  flag is a class attribute with no constructor option to turn it off.
- **ResearchBackend** — `LocalResearchBackend` searches recorded evidence with
  literal substring matching and no ranking model, because a similarity score
  is not evidence that two results are about the same thing.
- **OpenScienceBackend** — **not implemented, deliberately.** It contains no
  network code, `available()` returns `False`, and `search()` raises rather
  than returning an empty list, so a failed lookup is never read as "nothing
  has been published". See `docs/INTEGRATIONS.md` for the six things that must
  be verified from the service's own documentation before it is written.
- **SimulationBackend** — every result carries its model, parameters and stated
  assumptions, because a simulation that does not say what it ignored invites
  its output to be read as the behaviour of the real system.

## What this core does not do

- It does not score truth, automatically or otherwise.
- It does not fact-check AI output. It labels it.
- It does not treat a model's stated confidence as a probability that a
  statement is correct. Where a confidence is recorded it is stored as a
  self-report and labelled as one.
- It does not decide what evidence means. Linking evidence to a hypothesis
  records the relationship; `assess_hypothesis` requires a named person.
- It does not connect to external research platforms, because their APIs have
  not been verified from source.
