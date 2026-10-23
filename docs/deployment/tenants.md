# Tenants

A hub holds its fleets as tenants. A tenant is one fleet's whole world: its projects, chunks and graphs, scopes and
routines, work sources, repositories and secrets, its event log, and its runners. Every such record belongs to exactly
one tenant, and nothing crosses between them — a person sees, and a runner works on, only the records of a tenant they
belong to. People's accounts and sign-in identities, the hub administrators, the egress and trace exports, the hub's own
event log, and its signing keys are the hub's own and sit above every tenant.

A tenant is named by its id, `ten_` followed by a ULID, which never changes. Its name is a label: 1 to 64 characters
with no leading or trailing whitespace, never refused for being taken, so two tenants may share one. Every verb and
route names a tenant by its id.

A fresh hub starts with one tenant named `default`, empty but for its `default` project. Every tenant starts with a
project of that slug ([projects.md](./projects.md#the-default-project)). An upgraded hub carries every existing row into
that one tenant ([`upgrade.md`](../upgrade.md#tenants-boundary)).

## Hub administrators

A hub administrator holds the four hub-level permissions. No tenant role holds any of them.

| Permission             | What it allows                                                                                                                                                               |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tenant:admin`         | Create, list, rename and delete tenants; reconcile the packaged graphs into every tenant (`blizzard hub graph sync`); read the hub's own event log (`GET /api/admin/events`) |
| `membership:grant-any` | Grant, change and revoke memberships, and issue, list and revoke invitations, in any tenant — one the administrator is no member of included                                 |
| `hub-admin:grant`      | List, grant and revoke hub administrators; rotate the hub's signing key (`blizzard hub rotate-signing-key`)                                                                  |
| `export:admin`         | Operate the egress and trace exports: `blizzard hub egress status`, `backfill` and `reset`, and `blizzard hub traces status` and `replay`                                    |

Being a hub administrator grants nothing inside a tenant. To read a tenant's board or work its chunks, an administrator
needs a membership there, which `membership:grant-any` lets them grant themselves.

The first hub administrator is the person `[auth].superuser` names, once they sign in
([human-auth.md](./human-auth.md#roles)). After that, administrators are granted by user id:

| Verb                               | Route                                    | Does                                                                 |
| ---------------------------------- | ---------------------------------------- | -------------------------------------------------------------------- |
| `blizzard hub admin list`          | `GET /api/admin/hub-admins`              | Every hub administrator: user id, username, email                    |
| `blizzard hub admin grant <user>`  | `PUT /api/admin/hub-admins/{user_id}`    | Makes the user a hub administrator; granting a holder writes nothing |
| `blizzard hub admin revoke <user>` | `DELETE /api/admin/hub-admins/{user_id}` | Revokes the role; the hub's last administrator keeps it (`409`)      |

## Creating, renaming and deleting a tenant

Each verb takes `tenant:admin` and no `--tenant`: it names the tenant it acts on itself.

| Verb                                                 | Route                                   |
| ---------------------------------------------------- | --------------------------------------- |
| `blizzard hub tenant create <name> [--admin <user>]` | `POST /api/admin/tenants`               |
| `blizzard hub tenant list`                           | `GET /api/admin/tenants`                |
| `blizzard hub tenant rename <tenant_id> <name>`      | `PATCH /api/admin/tenants/{tenant_id}`  |
| `blizzard hub tenant delete <tenant_id> [--yes]`     | `DELETE /api/admin/tenants/{tenant_id}` |

**Create** makes the tenant in one transaction, its packaged graphs minted, its `default` project created, and, with
`--admin <user_id>` (`admin_user_id` over HTTP), that user granted the `admin` role in it. It answers `201` with the new
id, and the tenant is usable at once. A name that is not a tenant name's shape answers `422`, and an `--admin` that
names no user `404`; neither creates anything. Without `--admin` the tenant has no members until someone who holds
`membership:grant-any` grants one or invites one.

**List** shows every tenant oldest first: the open ones, and those whose deletion is underway with when they closed.

**Rename** changes an open tenant's name and never its id. A runner of the tenant learns the new name at its next
registration. An id that names no open tenant answers `404`.

### Deleting a tenant

Deleting a tenant removes it and every row it holds, and cannot be undone. The CLI asks for confirmation unless `--yes`
is given.

The hub first closes the tenant. From that moment it does not resolve: its people's calls into it answer `404`, its
runners' tokens are refused with a `401`, and nothing more is written there. Then the hub drains the tenant's rows,
children before parents, at most 5,000 rows per transaction, so another tenant's writes wait on at most one batch.
Finally it removes the tenant itself. The hub's event log records `tenant-closed` and `tenant-deleted`.

| Answer | Meaning                                                                                                           |
| ------ | ----------------------------------------------------------------------------------------------------------------- |
| `200`  | `state: deleted`: the request drained the tenant within its own budget of 50,000 rows; `rows_removed` counts them |
| `202`  | `state: deleting`: the tenant is closed and the `tenant_teardown` sweep finishes the drain                        |
| `404`  | No tenant has the id                                                                                              |
| `409`  | `last_tenant`: the tenant is the hub's only open tenant, which cannot be deleted                                  |
| `410`  | The tenant was deleted already; the CLI says so and exits zero                                                    |

The `tenant_teardown` sweep runs every 30 seconds and drains up to 50,000 rows of each deletion underway per pass. A hub
stopped or killed mid-drain resumes the drain from wherever the tables stand once it is back. Deleting a tenant whose
deletion is underway answers `202` again and changes nothing. `tenant list` shows the tenant until it is gone.

Deletion removes the tenant's rows from the hub's store, never from an export already written
([egress.md](./egress.md#upgrading-to-tenants)).

## Members and roles

A person belongs to a tenant through a membership, which carries one role. One person may hold memberships in several
tenants, with a different role in each.

| Role          | Holds                                                                                                                |
| ------------- | -------------------------------------------------------------------------------------------------------------------- |
| `guest`       | `fleet:view`: reads the tenant's fleet and changes nothing                                                           |
| `contributor` | Adds `chunk:ingest`, `chunk:control`, `question:answer`, `gate:resolve`, `queue:reorder` and `transcript:read`       |
| `admin`       | Adds `runner:pause`, `runner:retire`, `runner:add`, `graph:edit`, `user:manage`, `analytics:admin` and `config:edit` |

A tenant's members are managed by user id, in the tenant the call acts in:

| Verb                                         | Route                           | Needs                                    |
| -------------------------------------------- | ------------------------------- | ---------------------------------------- |
| `blizzard hub member list`                   | `GET /api/members`              | `user:manage`                            |
| `blizzard hub member grant <user_id> <role>` | `PUT /api/members/{user_id}`    | `user:manage`, or `membership:grant-any` |
| `blizzard hub member revoke <user_id>`       | `DELETE /api/members/{user_id}` | `user:manage`, or `membership:grant-any` |

`grant` gives a role or changes the one held. A tenant admin changes only existing members: a user id that is no member
answers `404`, exactly as one that does not exist, so someone new comes in through an invitation. A hub administrator
may grant anyone directly, in any tenant. Nobody changes or revokes their own role (`403`), and the tenant's last
`admin` is neither demoted nor revoked (`409`). A role other than `guest`, `contributor` or `admin` answers `422`. On
the board, the same lives under Admin → Members. The older `GET /api/users` and `POST /api/users/{id}/role` still
answer, over the members of the request's tenant only.

## Invitations

An invitation lets one email address into one tenant with one role. It is how a person the hub does not know yet gets
in, since sign-in is invite-only ([human-auth.md](./human-auth.md#who-may-sign-in)).

| Verb                                                                            | Route                                               |
| ------------------------------------------------------------------------------- | --------------------------------------------------- |
| `blizzard hub invite create --email <address> --role <role> [--expires <days>]` | `POST /api/invitations`                             |
| `blizzard hub invite list`                                                      | `GET /api/invitations`                              |
| `blizzard hub invite revoke <invitation_id>`                                    | `POST /api/invitations/{invitation_id}/revocations` |

Each takes `user:manage` in the tenant, or `membership:grant-any` in any tenant.

**Issuing** one names the address, a role of `guest`, `contributor` or `admin` (any other answers `422`), and how many
whole days it stays live: 7 by default, 1 to 30. The CLI prints its link, `<hub_url>/invite/<token>`. The link is shown
that once: the hub keeps only a hash of the token, so it cannot be shown again, and the hub sends no mail — hand the
link to the person yourself.

**Listing** shows every invitation of the tenant, oldest first, with its state — live, accepted and by whom, expired, or
revoked — and who issued it. It never shows a link.

**Revoking** a live or expired invitation stops its link admitting anyone. An invitation the tenant does not hold
answers `404`; one already accepted or revoked answers `409`, and an accepted one stays accepted.

**Accepting.** The person opens the link and signs in from the page it shows. The sign-in is admitted only when an
address the sign-in provider vouches for matches the invited address, ignoring case. It then grants the membership and
lands the person in the tenant. A link admits once. [human-auth.md](./human-auth.md#who-may-sign-in) owns what the
person sees when an invitation is refused.

## Naming the tenant a call acts in

The hub settles the tenant of each call by the first rule that applies:

1. A runner's bearer token names its runner's tenant. A tenant header naming another answers `400`.
2. The `X-Blizzard-Tenant: <tenant_id>` header names it. A tenant that does not exist, one that is closed, and one the
   caller holds no membership in all answer the same `404`.
3. The caller's only membership settles it.
4. Otherwise the call answers `409 tenant_required`.

A signed-in person with no membership at all answers `404` on every tenant route. Under `auth.mode = "none"` the header
names the tenant, or else the hub's only open tenant serves.

The CLI keeps no saved tenant. Every verb that acts in a tenant takes `--tenant <tenant_id>`, by id, never by name, and
sends it as the header. `BZ_HUB_TENANT` in the shell stands in for the flag, as `BZ_HUB_URL` does for `--hub-url`, and
`--tenant` overrides it. Without either, a person with one membership acts there; a person with several gets the verb's
refusal and a list of their tenants, ids beside names, to pick from. The hub-level verbs — `tenant`, `admin`,
`graph sync`, `egress`, `traces` and `rotate-signing-key` — take no `--tenant`.

The board settles the tenant once, at sign-in, before any page renders. A person with one membership is placed in it;
one with several picks from a list, the last choice remembered; one with none sees only *You don't have access to
anything here yet. Reach out to your administrator for access.* Someone with several switches tenants from the profile
menu.

## A runner belongs to one tenant

A runner belongs to the tenant it was added in, for as long as it exists. `blizzard hub runner add --tenant <tenant_id>`
or `blizzard runner init --tenant <tenant_id>` adds it there, by id; without the flag the operator's only tenant is
used. Its token names that tenant on every fleet call, and it never moves: to serve another tenant, set up a new runner
from a fresh directory. [runner-auth.md](./runner-auth.md#adding-a-runner) owns adding one. Deleting the tenant refuses
its runners' tokens.

## Exports are the hub's

The fact-egress and trace exports are one stream for every tenant: one directory and one cursor per dataset, one trace
endpoint. Operating them takes `export:admin`. Each egress row names its tenant
([egress.md](./egress.md#upgrading-to-tenants)).

## One forge repository, one annotating tenant

Annotate a given forge repository from one tenant only: the hub does not coordinate two tenants whose work sources
annotate the same repository ([work-sources.md](./work-sources.md#forge-status-labels-annotate)).
