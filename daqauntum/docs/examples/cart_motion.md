# Worked example: cart motion

A cart travels along a level track. Position is recorded against time. How did
it move?

It is the smallest investigation that still needs every part of the scientific
core, and it is the example the rest of the documentation refers to.

## About this data

| t (s) | 0.0 | 1.0 | 2.0 | 3.0 | 4.0 |
| --- | --- | --- | --- | --- | --- |
| x (m) | 0.0 | 1.0 | 2.1 | 3.3 | 4.6 |

**Nobody measured this.** It is example data distributed with DaQauntum so the
pipeline can be demonstrated without hardware. It is recorded with `IMPORTED`
provenance, which makes its `evidence_kind` report `LITERATURE` rather than
`MEASUREMENT`, it is excluded from `raw_measurements`, the analysis carries a
warning naming it, and every plot drawn from it says so on the image itself.

A demo is not an exemption. If example data could quietly be recorded as
measured, the distinction the product exists to maintain would be broken in the
first thing a new user runs.

## Run it

```bash
PYTHONPATH=. python -c "
from memory.store import MemoryStore
from science.core import ScienceCore
from science.reference import run_cart_motion
import json

core = ScienceCore(MemoryStore('data/cart.db'), artifacts_dir='data/science/artifacts')
print(json.dumps(run_cart_motion(core).summary(), indent=2))
"
```

Or over the API, with the server running:

```bash
curl -X POST localhost:8765/api/science/reference-run \
     -H 'Content-Type: application/json' -d '{"source": "example"}'
```

## What it does, step by step

### 1. The experiment

```python
experiment = core.create_experiment(
    "Cart motion on a level track",
    research_question="How does the cart's position change with time, and is the motion uniform?",
    depth=Depth.INTRODUCTORY,
    independent_variables=["time"],
    dependent_variables=["position"],
    controlled_variables=["track angle", "cart mass", "starting point"],
    apparatus=["level track", "cart", "metre rule", "stopwatch"],
)
```

### 2. The hypothesis

> The cart's velocity increases over the run rather than staying constant.

Recorded with `status = proposed` and the rationale that successive one-second
displacements grow. Nothing yet says whether it is right.

### 3. The data

Each value carries a unit and an uncertainty — ±0.01 s for the stopwatch,
±0.01 m for the rule. A measurement without an uncertainty claims a precision
nobody has.

### 4. The analysis

```text
displacement       Δx = x_final − x_initial              4.6 ± 0.014 m
average velocity   v = Δx / Δt                           1.15 ± 0.0035 m/s
interval velocity  v = (x₂ − x₁) / (t₂ − t₁)             1.0, 1.1, 1.2, 1.3 m/s
acceleration       a = Δv / Δt at interval midpoints     0.1 ± 0.0067 m/s²
least squares      slope = Σ(x−x̄)(y−ȳ) / Σ(x−x̄)²        1.15 m/s, r² = 0.9974
```

Every number arrives as an `AnalysisResult` carrying the equation, the ids of
the measurements it consumed and `CALCULATED` provenance, so the arithmetic can
be reconstructed from the record alone.

Two details worth noticing. The uncertainties are propagated in quadrature, not
carried across: the ±0.014 m on the displacement is √(0.01² + 0.01²), because
subtracting two uncertain values gives a result less certain than either. And
the interval velocities are plotted at interval *midpoints*, because an
interval velocity is an average over a span — drawing it at either end would
imply a precision the calculation does not have.

The r² of 0.9974 means a straight line fits well. It does not mean the motion
was uniform; the rising interval velocities say it was not, and the fit carries
a note stating exactly that distinction.

### 5. Evidence, and what it is not

The three summary results become `CALCULATION` evidence — not `MEASUREMENT`.
They were derived, and `is_empirical` is `False` for all of them. The plots
become artifacts attached to evidence of the same kind as the data they draw.

### 6. The assessment

Linking evidence to the hypothesis records the relationship and leaves the
status alone. A separate, explicit `assess_hypothesis(..., assessor="user")`
moves it to `supported` with the reasoning attached.

`supported`, not `proven`. The vocabulary has no such value.

### 7. The claim

> Over the recorded run the cart's average velocity was 1.15 m/s and its
> average acceleration was 0.1 m/s².

Cites the calculated evidence ids; every one resolves. `require_empirical` is
deliberately *not* set here, because no measurement was taken — a claim about
the physical world would be unsupportable from this data, and the code says so
in a comment rather than leaving the next reader to wonder.

## Running it on generated data instead

```python
run_cart_motion(core, source="simulated")
```

Uses `MockCartSensor` (x₀ = 0, v₀ = 1.0 m/s, a = 0.1 m/s²), so v_avg comes out
at 1.2 m/s. Every value is labelled `SIMULATION` and the plots say
"SIMULATED DATA — generated from a model, not measured".

There is no `source="measured"`. The function has no instrument, and offering
the option would be an invitation to relabel generated data. Real readings go
through `core.run_sensor(experiment_id, backend, count)` with a backend for
your actual hardware.

## Recording your own run

```python
experiment = core.create_experiment("My cart run", research_question="...")
for t, x in zip(my_times, my_positions):
    core.record_measurement(experiment.id, "time", t, "s", uncertainty=0.01)
    core.record_measurement(experiment.id, "position", x, "m", uncertainty=0.005)

analysis, evidence = core.analyse_motion(experiment.id)
core.plot_motion(experiment.id, analysis)
```

`record_measurement` records a human reading. It refuses `CALCULATED`,
`SIMULATED` and `AI` provenance outright — those have their own doors, and each
one labels what comes through it.
