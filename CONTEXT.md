# Code Reviewer

A LangGraph-orchestrated code review assistant that attacks the cognitive load
of PR review: senior reviewers gatekeep architecture instead of style, while
junior developers get a mentor-voiced pre-check before opening a PR.

## Language

**Pre-check**:
A local review pass a developer runs before opening a PR. Runs in Mentor Mode.
_Avoid_: pre-review, self-check, lint pass

**Review**:
The gatekeeping pass over an opened PR. Runs in Gatekeeper Mode.
_Avoid_: audit, inspection

**Mentor Mode**:
The graph routing mode serving the PR author; output teaches why, not just what.
_Avoid_: junior mode, assistant mode

**Gatekeeper Mode**:
The graph routing mode serving the senior reviewer; output prioritizes
verdicts on architecture and business logic over style nits.
_Avoid_: senior mode, admin mode

**Spec KB (Spec Knowledge Base)**:
An injectable, editable, optional collection of normative documents that
defines what "consistent with our rules" means. May be empty.
_Avoid_: rules, guidelines, checklist

**Spec Compliance Check**:
The pipeline stage verifying changes against the Spec KB. Skipped entirely
when the Spec KB is empty — no spec, no verdict.
_Avoid_: rule check, style check

**Spec Document**:
A single Markdown file in the Spec KB. Referenced as "文档名 § 节名" in
finding rationales.
_Avoid_: rule entry, 条文

**Replay**:
Running the reviewer against a fixed diff snapshot (`--diff-file`) instead of
live git state. The foundation of the golden-set evaluation pipeline.
_Avoid_: offline run, dry run

**Expert**:
One of the parallel reviewers with a single-charter responsibility
(architecture / logic / spec / style), always emitting Findings of its own
category.
_Avoid_: agent, reviewer, specialist

**Aggregator**:
The fan-in node that sorts and deduplicates expert Findings, merges duplicates
(severity may only be kept or lowered, never raised), and derives the Verdict.
_Avoid_: judge, supervisor

**Finding**:
A single file-and-line-anchored review observation with three-level severity
(blocker / concern / nit), produced by an expert and canonicalized by the
aggregator.
_Avoid_: issue, comment, defect

**Report**:
The complete output of one review run: summary, findings, and metadata.
Versioned by schema_version.
_Avoid_: result, output

**Verdict**:
The overall three-state assessment of a Report — pass / concerns / blocked —
derived from finding severities by the aggregation node.
_Avoid_: score, rating
