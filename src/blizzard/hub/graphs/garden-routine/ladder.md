# The proposal classes and the mechanization ladder

## The four classes

A proposal's `class` is drawn from exactly these four — closed, not invented per run:

- **`remediate`** — do the cleanup the findings describe. One proposal per theme per area, sized as work somebody can
  actually pick up.
- **`prevent`** — fix the source so the inflow stops: the missing standard, the exemplar spreading the pattern, the rule
  too vague to follow.
- **`mechanize`** — move the check into a gate so no later run pays a model to notice this class again.
- **`escalate`** — hand it past what this routine absorbs: a decision needing a person to plan it somewhere blizzard is
  not.

## The ladder

When a finding class recurs run after run with nobody overriding it, it is crisp enough to encode. Climb from the
cheapest rung that would actually hold; tiers 1-4 all resolve to `mechanize`, tier 5 to `prevent`.

1. **Reconfigure tooling the project already runs.** Research obligation: before proposing anything new, check whether
   a check, config, or gate the project already invokes can be turned on or tightened to catch this class. Free to
   adopt — no new dependency, nothing new to maintain.
2. **Adopt tooling the project does not yet run.** Research obligation: if nothing already running covers it, check
   whether tooling that is already built and maintained elsewhere implements the rule. Still off-the-shelf; carries the
   cost of a new dependency but no bespoke code.
3. **Author a bespoke check.** When no existing tool, running or not, covers the exact rule: a small project-owned
   script or test enforcing it mechanically. New infrastructure the project must maintain itself, and has to carry its
   own case for why that is worth it.
4. **Graduate the check into a standing gate.** A bespoke or newly adopted check that has proven itself moves house
   rather than being copied: the axis's own Criteria pointer moves to point at the mechanized check, and the axis stops
   re-judging that class by hand. One owner per check.
5. **Prevent, when nothing mechanical can hold it.** The judgment is real but nothing above catches it, so the rung to
   reach for is a change to the project's own guidance: the missing standard, the exemplar spreading the pattern. This
   tier is `prevent`, not `mechanize`.

Tiers 1 and 2 are obligations, not options: check both before reaching for tier 3 or above. A proposal reaching for
tier 1 or 2 must state, of its candidate tool, three things — the tool, the rule it would enforce, and whether the
project already runs it — so a reader can judge the proposal without re-doing the research.

## Who a proposal is for

A proposal's body is written for a person: a senior developer reading it to decide whether to accept it, and again,
later, to pick up the work an acceptance mints. Write it in that register — briefing a competent peer who will act on
it, not documenting a system.
