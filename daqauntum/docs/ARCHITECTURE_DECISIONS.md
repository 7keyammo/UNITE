# DaQauntum Architecture Decisions

These decisions were accumulated across the v0.1 -> v0.4.0 evolution. Changing one should be treated as an architectural proposal, not a cleanup.

## ADR-001 - DaQauntum is a system, not one foundation model

**Decision:** Keep the DaQauntum identity/runtime independent of the model provider.

**Why:** Models change quickly; memory, permissions, tools, user identity and projects must survive provider changes. This also supports local/privacy-sensitive operation and future native models.

**Consequence:** `core/brain.py` providers sit behind routing/policy rather than leaking provider assumptions through the codebase.

## ADR-002 - Authority lives outside the LLM

**Decision:** LLMs may recommend tool actions; deterministic code decides execute/confirm/deny.

**Why:** Prompt instructions are not a security boundary.

**Consequence:** State-changing adapters must enter through the tool/permission path.

## ADR-003 - Planner, executor and critic are separable cognitive roles

**Decision:** DaQauntum may route different cognitive roles to different models/providers.

**Why:** Cost, latency, privacy and independent criticism differ by task.

**Consequence:** Realtime conversation can bypass expensive serial reasoning while Deep mode retains planner/critic quality controls.

## ADR-004 - Privacy/locality policy is deterministic

**Decision:** Sensitive/local-forced workloads may exclude hosted providers entirely.

**Why:** A fallback that silently uploads private data is not a fallback; it is a policy violation.

## ADR-005 - Memory is structured, inspectable and correctable

**Decision:** Preserve raw messages/events and separately maintain episodic, semantic, project, procedural, decision, source and training memories.

**Why:** A chatbot transcript is not durable cognition. User correction and auditability matter.

## ADR-006 - Similarity does not create truth/evidence

**Decision:** Semantic/vector retrieval discovers candidates. Evidence/provenance relationships require explicit traceable logic.

**Why:** RAG similarity is not epistemic support.

## ADR-007 - Autonomous learning does not equal autonomous fine-tuning

**Decision:** Daily learning may create reports and candidate training traces but may not update/deploy model weights.

**Why:** Self-training on unvalidated outputs can produce feedback-loop degradation.

## ADR-008 - External products are adapters, not forks

**Decision:** Integrate mature tools (Ollama, Open Interpreter, LangGraph, Home Assistant, Tailscale, Obsidian, pgvector, Flux, etc.) behind optional adapters.

**Why:** DaQauntum should orchestrate ecosystems instead of becoming responsible for reimplementing them.

**Consequence:** Missing external products must not prevent core startup.

## ADR-009 - GUI is a client/surface, not the cognitive authority

**Decision:** Keep business rules in backend modules and support a persistent server independent of the browser.

**Why:** DaQauntum should continue living when the UI closes and support multiple future clients.

## ADR-010 - Sensing and control are different capabilities

**Decision:** Passive telemetry, explicit discovery, connection and operation are separate authority steps.

**Why:** Ambient awareness should not silently become ambient control or surveillance.

## ADR-011 - Computer actions require verification

**Decision:** Where possible, follow state-changing computer actions with fresh observation and classify the result PASS/FAIL/UNCERTAIN.

**Why:** Successful invocation does not guarantee successful real-world effect.

## ADR-012 - Side agents are advisory unless handed back to the kernel

**Decision:** Parallel workspace/workbench agents read/reason/draft; consequential mutation routes back through DaQauntum's main authority path.

## ADR-013 - Release archives are complete and independently testable

**Decision:** Every user-facing version is a full repo, not a patch, with SHA-256 manifest and fresh-extraction regression gate.

**Why:** The project is frequently moved between machines and coding agents; partial overlays create unreproducible Frankenstein installs.
