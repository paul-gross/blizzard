---
kind: added
surfaces: [http-api, cli, config, store]
---

A project's link to a repository may name its own base branch, so two projects may work on different branches of one
repository. `blizzard hub project base <project> <repository> (--branch <name> | --inherit)` and
`PATCH /api/projects/{project}/repository-links/{repository}` set or clear it; a link that names none inherits the
repository's `base_branch`, as every existing link does. Each edit moves the link's revision and is recorded in the
change log, and a name that is no git branch name is refused `422` on `base_branch`. Repository-link reads carry
`base_branch` and `effective_base_branch`, `blizzard hub project get` shows each repository's effective branch and
whether it is inherited, and a configuration document's project entry takes a `repository_branches` map, which export
writes for a project whose link names its own branch.
