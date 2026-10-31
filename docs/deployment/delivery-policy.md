# Choosing a landing policy

Each repository selects its own policy; a delivery visit keeps the policy and project landing order it took at its first
turn. Configure it with `blizzard hub repo edit <repository> --policy <policy>`; see
[repositories](./repositories.md) for repository management and capability discovery.

| Policy | Choose it when |
| --- | --- |
| `merge-on-checks` | The hub should open and update a pull request and merge it after checks pass. This is the default. |
| `fast-forward` | The hub should prove checks on a revision and advance the base without a pull request. A diverged base needs a proven merge candidate on `<work-branch>-landing`. |
| `park` | A person should decide and merge a pull request. The hub observes the result rather than merging it. |

For fast-forward, configure push-triggered CI on ordinary work branches **and** `-landing` branches. Pull-request-only CI
cannot establish green checks on the revision the policy must prove. Branch protection must permit the hub's required
ref operations; a protection refusal escalates rather than bypassing it. A diverged candidate also needs forge support
for creating and deleting refs and merging into a branch. Configuration refuses a policy whose mandatory capabilities
are missing; a missing capability discovered during delivery escalates. Consult the forge capabilities before selecting
the policy.

Parked changes remain `delivering` while the hub polls and yields the branch between polls. Merge the linked pull request
on the forge; the hub records its merged revision and continues. Closing it without merging escalates. Set
`--park-timeout <hours>` on a `park` repository to bound human review; the default is 168 hours. A review timeout
escalates to a person. The node's ordinary pending poll timeout applies to consecutive pending work, not the parked
review wait.

Before rolling back to a build that cannot understand these policies or parked waits, settle affected deliveries and
restore repository policies to ones that build supports. Inspect open parked changes and decide their disposition rather
than assuming a rollback merges or closes them. Fast-forward candidate cleanup is best effort after the landing record:
inspect orphan `<work-branch>-landing` refs and prune them only after confirming no active delivery needs them.
