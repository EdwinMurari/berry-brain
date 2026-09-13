# Learning and skills

[Home](../README.md) · [Setup and data](usage.md) · [Development](development.md)

The MCP instructions and tool descriptions provide the learning rules:

1. Recall task state and relevant lessons before substantial work.
2. Save meaningful outcomes with exact evidence. Include failures and uncertainty.
3. Propose a conditional lesson from those records.
4. Test candidates explicitly on later tasks. Report the observed comparison.
5. Save a compact checkpoint so another client can resume with the same project
   and task ID. Recheck old job status and other facts that can change.

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
