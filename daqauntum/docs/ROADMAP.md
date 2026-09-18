# DaQauntum Roadmap

Where the project is, and what is deliberately not built yet.

## Shipped

**v0.4.x — the assistant.** Persistent local runtime, multimodel routing with
a permission gate outside the model, durable and structured memory, knowledge
graph with provenance, voice, perception, permission-gated computer actions,
device identity, event bus with deterministic reaction rules, device driver
adapters, Obsidian long-term memory, the native model lab.

**v0.5.0 — the scientific core.** Experiments, hypotheses, measurements,
evidence and claims as first-class objects; dependency-free SI units with
uncertainty propagation; deterministic motion analysis; typed scientific events
exempt from bus suppression; SVG plotting with provenance on the image;
sensor, research and simulation backends; a physics concept registry; and
fail-closed validation. See `docs/SCIENTIFIC_CORE.md`.

## Not built, and why

These are absences with reasons, not a backlog of things nobody got to.

**Open Science integration.** Stubbed with no network code. Six things must be
verified from the service's own documentation first; the list is in
`docs/INTEGRATIONS.md` and in `OpenScienceBackend.RESEARCH_NEEDED`. An adapter
written against an unverified API looks finished and fails unpredictably.

**Automated truth scoring.** Not planned. A number claiming to represent how
true a claim is would be treated as an answer, and this system has no basis for
producing one. Validation finds contradictions; people judge truth.

**An AI fact-checker.** Not planned in this form. A model checking a model
produces another interpretation, and presenting it as verification would be
exactly the confusion the core exists to prevent. A model can *propose* checks
for a person to run.

**Automatic hypothesis promotion.** Linking evidence records a relationship.
Deciding what a body of evidence means stays an explicit act by a named person.

**Weight updates from use.** Unchanged from v0.4: learning produces reports and
datasets. Changing a model requires an evaluated training run and a human
promotion decision.

## Next, in rough order

1. **Statistical depth** — weighted least squares, confidence intervals on fit
   parameters, and a residual test that says when a linear model is the wrong
   one. Current OLS reports r² and a slope standard error and is honest about
   the limits of both.
2. **More analysis engines** — energy and momentum, following the same pattern:
   equations visible, inputs named, results as `CALCULATION` evidence.
3. **A real sensor backend**, developed against actual hardware. The interface
   is ready; the work is a device, not a design.
4. **The education layer.** `Depth` exists on experiments and concepts and
   carries no content yet. Explaining at four levels is a content problem, and
   generating explanations without checking them against the registry would
   produce confident wrong physics.
5. **Multi-run experiments** — comparing runs, pooling uncertainty, detecting
   drift between sessions.
6. **Export** — a record that leaves as a paper-shaped document with its
   provenance intact, rather than as a screenshot.

## The line that does not move

Whatever else gets built: a calculated value never becomes a reading, a
simulation never becomes a measurement, an AI interpretation never becomes
evidence of the world, and a hypothesis never becomes proven. Every feature
above has to work within that or not ship.
