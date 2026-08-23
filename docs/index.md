# Operator and release documentation

`docs/` holds blizzard's operator- and release-facing prose; the table below is the way in, routing each topic to the
one file that owns it.

| File                                                          | When to read                                                                                                                 |
| ------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| [`install.md`](./install.md)                                  | You are standing blizzard up for the first time — the reference `docker compose` deployment of hub, postgres, and Caddy      |
| [`upgrade.md`](./upgrade.md)                                  | You are pulling a new image tag                                                                                              |
| [`rollback.md`](./rollback.md)                                | You are reversing a release                                                                                                  |
| [`versioning.md`](./versioning.md)                            | You need to know what a version number promises                                                                              |
| [`backup.md`](./backup.md)                                    | You are snapshotting or restoring durable state                                                                              |
| [`remote-runner.md`](./remote-runner.md)                      | You are adding a runner on a machine that is not the hub's                                                                   |
| [`deployment.md`](./deployment.md)                            | You want the colocated wheel + systemd install instead, or any operator concern that is not specific to one deployment shape |
| [`ci.md`](./ci.md)                                            | You are touching a GitHub Actions workflow, or reproducing the merge gate locally                                            |
| [`packaging/docker/README.md`](../packaging/docker/README.md) | You need the container image's own mount and environment-variable reference                                                  |

These documents are the deployed-side half of the release cut; the cut sequence itself is owned by
[`blizzard-context/workflows/release.md`](https://github.com/paul-gross/blizzard-context/blob/master/workflows/release.md).
