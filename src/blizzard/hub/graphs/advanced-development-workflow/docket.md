# Docket entry

Every finding recorded in a `plan-findings` or `review-findings` asset carries:

- **id** — stable *within this asset submission*: `F1`, `F2`, … A fresh submission restarts at `F1`.
- **severity** — `blocking` or `should-fix`. A `plan-findings` entry may instead carry `folded`: an improvement-tier
  finding the gate already fixed in the `reviewed-plan` it published under an `acceptable` verdict. Recording `folded`
  is itself the closure; on a `must-fix` verdict no fold survives the verbatim republish, so improvement-tier findings
  are `should-fix` there. (`plan-review.md` calls its blocking tier "must-fix" in prose — same value, `blocking`, in the
  entry.)
- **anchor** — `<repo>/<path>:<line>` or `<repo>/<path>::<symbol>`; a finding targeting a chunk asset rather than a repo
  file anchors as `<asset-name>::<section>`, e.g. `plan::Acceptance criteria`.
- a **description** — one or two sentences, **at most 300 characters**: what is wrong and why it matters, specific and
  actionable. The derivation that established the finding — cross-references, confirming lookups, quotations — stays in
  the responder's reasoning; it never rides the entry, not even relabelled as detail. When *acting* on the finding needs
  a fact the description cannot hold — a reproduction command, an exact expected/actual pair, a fix constraint — carry
  that fact alone in a `detail:` continuation after the description, itself **at most two lines**, so a reader can stop
  at the claim.

Example, inside a `review-findings` asset — the `detail:` continuation optional, and only ever the acting-on fact:

```text
F1 — should-fix — payments-api/src/payments/worker.py:142
  Retry counter isn't reset after a successful heartbeat, so a flaky-then-recovered
  worker still escalates on its next transient failure.
  detail: reproduce with `pytest -k recovered_worker` — expected 0 escalations, saw 1.
```

The same finding over-long — its derivation transcribed into the entry instead of summarized by it:

```text
F1 — should-fix — payments-api/src/payments/worker.py:142
  `Worker.heartbeat` returns early at line 138 when the lease is healthy, and `retries` is
  only reset on the fallthrough at line 151; `test_heartbeat_ok` pins the early return
  (payments-api/tests/test_worker.py:88), and the retry counter is declared cumulative in
  `openapi/worker.json`, so after a successful heartbeat the counter still holds its old
  value, which means a worker that was flaky and then recovered still escalates...
```

The derivation is reasoning the reviewer already did: keep the claim, drop the derivation wherever in the entry it sits
— the two-sentence form above loses nothing a responder acts on.

## Refutation record

A finding is answered one of two ways: **fixed**, or **refuted**. A refutation says the finding should not be acted on
at all — factually wrong, resting on a false premise, or demanding work the change's scale does not warrant; never "I
would rather not." The responding node records its refutations in a dedicated asset — `plan` submits
`plan-finding-refutes`, `build` submits `review-finding-refutes`. Each entry:

- **anchor** — the finding's anchor, copied verbatim. The anchor is what matches, not the id.
- **cited id** — `<node>:<id>` from the round being answered, e.g. `review:F2`.
- **the argument** — why the finding is wrong, with evidence.

**The newest submission is the entire record** — a later submission replaces the earlier one, so every submission
restates every refutation still standing, each marked `open` or `accepted`, and says "nothing to refute" when none is.
The gate resolves every entry explicitly: accept and not re-raise, or reject and re-raise with an answer to the
argument. An `accepted` entry stays accepted; refuting is a claim to be adjudicated, never a veto.
