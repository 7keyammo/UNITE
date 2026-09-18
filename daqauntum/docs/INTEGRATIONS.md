# DaQauntum Integrations

Everything outside the core sits behind an adapter. This file records what is
implemented, what is stubbed, and — for the stubs — exactly what has to be
established before they are written.

## Status

| Integration | Status | Notes |
| --- | --- | --- |
| Local research | **implemented** | searches evidence recorded by this installation |
| Kinematic simulation | **implemented** | closed-form constant acceleration |
| Mock cart sensor | **implemented** | generated data, permanently labelled simulated |
| Static sensor replay | **implemented** | replays a list of readings; `simulated` defaults to True |
| Pint cross-check | **optional** | verifies the native unit layer where installed |
| matplotlib | **optional** | PNG output; the SVG renderer is the reference |
| Open Science repository | **NOT IMPLEMENTED** | see below |
| Real laboratory sensors | **not implemented** | needs hardware to develop against |

## Open Science: why it is a stub

DaQauntum should be able to check a result against published work, and an
open-science repository is the obvious source. `OpenScienceBackend` is a
placeholder containing no network code.

That is a deliberate decision, not an oversight. Writing an adapter against an
API whose behaviour has not been verified produces something that looks
finished and fails in a way nobody anticipated. In a system whose entire
purpose is separating what is known from what is guessed, shipping an
integration built by guessing would be the sharpest possible contradiction of
the product.

So `available()` returns `False` with a reason, `describe()` reports
`implemented: false`, and `search()` **raises** rather than returning an empty
list — a failed lookup must never read as "nothing has been published".

### What has to be researched first

None of this can be settled from inside this repository. Each item needs the
service's own current documentation.

1. **Which service.** "Open science" covers several with incompatible APIs:
   OpenAlex, Crossref, OSF, Zenodo, Semantic Scholar, arXiv. They differ in
   coverage, identifiers and licensing. This is a product decision, not an
   implementation detail.
2. **The real request and response shape.** Endpoint, query grammar,
   pagination, and the exact JSON fields carrying title, authors, identifiers,
   dates and any numeric values.
3. **Authentication.** Whether a key or a contact header is required, the
   anonymous rate limit, and how the service signals throttling.
4. **Terms of use.** What may be cached locally, for how long, and what
   attribution the licence requires. This system stores what it retrieves, so
   caching rules are a correctness requirement rather than a nicety.
5. **Failure semantics.** How an empty result is distinguished from an error,
   so a failed lookup never reads as an absence of prior work.
6. **The record mapping.** How a returned record maps onto `ResearchResult`,
   and what to do with fields that do not map rather than dropping them
   silently.

The list lives in code as `OpenScienceBackend.RESEARCH_NEEDED`, and
`evaluations/backends_smoke_test.py` asserts the stub keeps failing loudly.

### When it is implemented

Results must arrive as `EvidenceKind.LITERATURE` with `ProvenanceKind.IMPORTED`
and the source recorded. Someone else measured it; that is a different
epistemic position from having measured it here, and the adapter must not
erase the difference.

## Writing a sensor backend

```python
from science.sensors import SensorBackend, SensorReading

class MyRangefinder(SensorBackend):
    name = "lab.rangefinder"
    simulated = False        # only if it really reads the world

    def available(self):
        return self._port_open(), "serial port /dev/ttyUSB0"

    def describe(self):
        return {"name": self.name, "quantities": ["position"], "unit": "m"}

    def read(self):
        value = self._read_serial()          # raise SensorError on failure
        return SensorReading(quantity="position", value=value, unit="m",
                             uncertainty=0.002, sensor_id=self.name)
```

Two rules. Report unavailability honestly — never return a plausible number
when the instrument cannot be reached. And set `simulated` truthfully: it
decides whether values enter the record as measurements or as simulations, and
`ScienceCore` routes on what the backend said, not on what the caller wants.

## Optional dependencies

```bash
pip install -r requirements-science.txt   # pint, numpy, matplotlib
```

None of them are needed. The core computes units, uncertainty, least squares
and SVG plots itself. Pint is used to *verify* the unit layer, not to provide
it; matplotlib is an alternative renderer.
