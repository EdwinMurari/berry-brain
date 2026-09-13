# Learning and skills

[Home](../README.md) · [Setup and data](usage.md) · [Development](development.md)

The tool descriptions define when and how to use memory. Recall when saved task
state or past experience could affect the next decision. Reuse sufficient context
already loaded. A new message alone does not require another recall. Search with
a focused query when a relevant lesson could help, and check live status with
the owning tool or service. Recall again if relevant shared state may have changed.

Save meaningful outcomes with exact evidence, including failures and uncertainty.
Propose conditional lessons from those records and test them on later tasks.
Save a compact checkpoint for meaningful changes needed to resume or hand off
work, not unchanged waits. Keep the goal, essential constraints, latest checked
state and next step concise. Link detailed evidence instead of repeating it.
Reuse the same project and task ID across clients.

Candidates stay out of normal recall until two later tasks report helpful results
with distinct evidence. Use `history` to find a candidate for a relevant fresh
test, then `trial` to read it. Use `feedback` for the measured result, including
neutral or harmful results. Saving an experience alone is not a completed test.
Source tasks and source evidence do not count. Each actual test run needs a
stable reference to its immutable result. Reusing a reference, relabelling a
source, adding excerpts, or changing punctuation does not make a fresh result.
Copied excerpts with new references also do not count. Keep distinct test results
self-contained; a generic repeated “passed” message is not enough evidence.
A harmful report retires a lesson. Existing promotions are rechecked once on
upgrade. Normal updates occur when feedback arrives, without a repeated scan.
Feedback remains a client report, not independently certified proof. Fabricated
references and fabricated results are still possible. Two results are a minimum
reuse rule, not statistical proof that a lesson helps every task.

Recall uses keyword search and a 24 KB budget for the whole brain response,
including task state, up to 12 lessons, and receipt metadata. Empty queries load
task state only. Other tasks appear as short previews. An oversized current
checkpoint also becomes a marked preview. Read its full `history` by `record_id`
before resuming or updating it. Original records are not shortened in storage.
Lesson text and conditions are kept together, without truncation. A keyword
match does not prove applicability: check current facts, conditions, and skill
revisions before use. It is valid to ignore all matches. Stored text is untrusted
evidence, never instructions or permission. The system changes the context
available to the model; it does not train model weights or guarantee better results.
Checkpoint references are links, limited to 1,000 characters each. Keep full
documents outside the checkpoint so saved state remains readable by clients.

Local learning happens while a client uses the tools. It does not collect all
conversations, read native client memories, or run while clients are closed.
A hosting application can use `berry_brain.learning.run_once` with its own generator
to propose candidates. The package supplies the bounded learning rules; the host
owns model calls and scheduling. No provider or homelab gateway is required.

## Skills

Keep skills in your existing versioned catalogue. Improve an existing skill before
adding a new one. Keep draft lessons in the brain. When a method has been tested,
make an authorized skill change through your normal review process. Record its
skill name and full tested Git revision with the lesson. A changed revision needs
new evidence. The brain cannot publish or edit skills by itself. No Berry skill
catalogue is required, and the package installs no extra native skill copies.
