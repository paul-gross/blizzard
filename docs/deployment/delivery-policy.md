# Choosing a landing policy

Each repository selects its own policy; a delivery visit keeps the policy and project landing order it took at its first
turn. Configure it with `blizzard hub repo edit <repository> --policy <policy>`; see [repositories](./repositories.md)
for repository management and capability discovery.

| Policy            | Choose it when                                                                                                                                                    |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `merge-on-checks` | The hub should open and update a pull request and merge it after checks pass. This is the default.                                                                |
| `fast-forward`    | The hub should prove checks on a revision and advance the base without a pull request. A diverged base needs a proven merge candidate on `<work-branch>-landing`. |
| `park`            | A person should decide and merge a pull request. The hub observes the result rather than merging it.                                                              |

For fast-forward, configure push-triggered CI on ordinary work branches **and** `-landing` branches. Pull-request-only
CI cannot establish green checks on the revision the policy must prove. Branch protection must permit the hub's required
ref operations; a protection refusal escalates rather than bypassing it. A diverged candidate also needs forge support
for creating and deleting refs and merging into a branch. Configuration refuses a policy whose mandatory capabilities
are missing; a missing capability discovered during delivery escalates. Consult the forge capabilities before selecting
the policy.

## Checks and missing CI

`merge-on-checks` and `fast-forward` require at least one check on the revision to land, with every check passed or
neutral. GitHub check runs and commit statuses both count. Pending checks wait and failing checks follow the failure
path; an unavailable or refused check read is not evidence that CI is absent. Ensure the repository's forge token can
read both kinds of GitHub result ([forge token](./repositories.md#the-forge-token)).

By default `allow_no_ci` is false, including on existing repositories migrated to this version. If a successful read
finds no checks, the hub records the first observation for that repository and SHA, then waits ten minutes for CI to
register. If a later read still finds no checks after that grace, it escalates `missing-checks`, identifying the
repository, policy, SHA and first observation time. A new SHA has its own grace period. The ordinary overall pending
deadline may expire first: in that case the diagnostic names the earlier empty observation and says it was **not
reconfirmed**, rather than claiming checks are still absent. Inspect the forge and token permissions before deciding
whether CI is missing.

For a repository intentionally without CI, explicitly opt in with `blizzard hub repo edit <repository> --allow-no-ci` or
`allow_no_ci: true` in its configuration document. Reverse it with `--no-allow-no-ci` or `allow_no_ci: false`. This
permits a successful empty check read for either automatic policy; it does not turn failed, pending, or unreadable
checks green. `park` leaves the merge decision to a person.

The delivery visit snapshots the policy, this setting and landing order on its first turn, and retains them across
retries and hub restarts. Editing a repository does not change an already waiting visit. After fixing CI or enabling the
opt-in, requeue the escalated chunk for a fresh visit that takes the current settings. Alternatively, inspect and merge
the pull request manually where appropriate, then requeue so delivery can observe the result. See
[chunk control verbs](./control-verbs.md#notes-to-later-sessions) for requeue and notes; verify what actually landed
before using hand-completion.

Parked changes remain `delivering` while the hub polls and yields the branch between polls. Merge the linked pull
request on the forge; the hub records the revision the merge actually produced and continues, even if the pull request's
final head differs from the worker's commit. A differing head is noted as changed-head evidence, not attributed to
anyone. Closing it without merging escalates. Set `--park-timeout <hours>` on a `park` repository to bound human review;
the default is 168 hours. A review timeout escalates to a person. The node's ordinary pending poll timeout applies to
consecutive pending work, not the parked review wait.

Before rolling back to a build that cannot understand these policies or parked waits, settle affected deliveries and
restore repository policies to ones that build supports. Inspect open parked changes and decide their disposition rather
than assuming a rollback merges or closes them. Fast-forward candidate cleanup is best effort after the landing record:
inspect orphan `<work-branch>-landing` refs and prune them only after confirming no active delivery needs them.
