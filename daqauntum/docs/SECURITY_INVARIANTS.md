# DaQauntum Security and Trust Invariants

These are architecture constraints, not suggestions.

## 1. Authority is deterministic

Language models may propose actions. They do not decide whether they are authorized.

All state-changing tools must route through the tool registry and permission manager. Never create a provider-specific direct execution path.

## 2. Intelligence does not imply authority

A stronger model, higher cognition mode, specialist agent, autonomous learner, or workspace maturity level must never increase permissions automatically.

## 3. Always-confirm class

Irreversible/high-impact categories must remain explicitly confirmable according to global policy, including at least deletion, sending/publishing, purchasing, credential/security changes, and similarly consequential operations.

## 4. Local/privacy routing is a hard constraint

If privacy policy or operation mode requires local processing, hosted model/vision/speech providers must be excluded. Failure should degrade capability, not silently upload data.

## 5. Sensing != scanning != connecting != controlling

These are separate states:

```text
passive observe -> explicit discovery -> connect/pair/join -> operate
```

Each step may require greater authority. Passive Presence must not silently start radio scans.

## 5a. Events are observations, not instructions

An event records that something was seen. A deterministic reaction rule may
respond by notifying the user, queueing a task, or recording a tool *proposal*.
It must never execute a tool, at any permission level - including L4, where the
permission gate would allow the call outright. Passive observation becoming
autonomous action is exactly the failure this separation prevents.

A proposal the permission gate refuses is stored as denied and can never be
approved afterwards.

## 5b. A device write needs two independent gates

The permission manager decides whether the user authorized the tool call. The
driver decides whether that device was ever opened for writing, via its
allowlist and an explicit `allow_writes`. Both must agree. User approval alone
must not reach a device that was never opened for writing.

## 6. Device detection does not equal device capability

Unknown hardware can be recorded as detected. It must not be labeled controllable until a verified adapter/capability mapping exists.

## 7. Main control surface stays private by default

The primary GUI/server should bind to loopback by default. Use Tailscale or another authenticated private transport for remote access. Do not default to public `0.0.0.0` exposure of the main control API.

The phone/device ingestion bridge is deliberately narrower than the main control interface.

## 8. Credentials are references, not durable knowledge

Secrets belong in environment variables or a future dedicated secret vault. Do not put API keys, passwords, Wi-Fi credentials, private keys, or bearer tokens into workspaces, memory, reports, training traces, source chunks, Git history, or release archives.

## 9. Retrieval is not proof

Semantic similarity/source search may retrieve candidate evidence. A claim-support relationship requires explicit provenance/evidence logic. Never transform "similar" into "supports" automatically.

## 10. Model output is not automatically truth

Do not save arbitrary model statements as durable semantic facts. Structured memory promotion must follow explicit rules/user instruction/verified source relationships.

## 11. Interrupted output is non-authoritative

Interrupted/cancelled partial responses may remain in raw conversational/audit history if useful, but must not become semantic knowledge, graph truth, or training examples.

## 12. Autonomous learning does not change weights

Daily learning may read approved data, reason, critique, write reports, update memory/graph, and create candidate training traces. It may not train, replace, or deploy model weights by itself.

Native-model candidates must pass explicit train/validation/test evaluation and require human promotion.

## 13. Side agents are advisory by default

Parallel workbench agents may read/reason/draft. Consequential action must hand back to the main kernel/tool permission path.

## 14. External integrations are optional and scoped

DaQauntum must boot when optional integrations are absent. An integration may not weaken the global permission/privacy model.

## 15. Migrations preserve user data

Schema changes should be additive and reversible where practical. Never delete/overwrite existing memories/sources/call history silently during upgrade.

## 16. Release hygiene

Release archives must exclude runtime databases, transcripts, screenshots, private sources, credentials, model weights, `.venv`, caches, and local configuration.
