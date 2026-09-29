# Review — the mutation report

The `mutation-report` asset in this envelope lists the mutants the change's own functions leave surviving. Read it with
`blizzard runner artifact get mutation-report --content`.

Sort each survivor under the project's survivor classification. Raise each real gap as a `should-fix` finding that
names the mutant and its remedy. Never make a finding `blocking` on survival alone. A report stating a reason in place
of survivors needs no finding.
