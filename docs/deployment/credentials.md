# Credentials

A credential is what a [repository](./repositories.md) or a [work source](./work-sources.md) acts with when the hub
calls its [forge](./forges.md). A hub holds each one as a stored record, managed with `blizzard hub credential`. A
credential reads a stored [secret](./secrets.md), which holds the token's value; the credential says what kind of token
that is and which forge it acts on. Many records may name one credential, and replacing its token or moving it to
another secret moves every one of them.

```bash
printf '%s' "$TOKEN" | blizzard hub secret create gh-token
blizzard hub credential create gh-token --forge github --binding github.token --secret gh-token --variant classic
blizzard hub repo create blizzard --forge github --owner paul-gross --repo blizzard \
  --base-branch master --credential gh-token
blizzard hub source create blizzard --provider github --locator paul-gross/blizzard --forge github \
  --credential gh-token --annotate
blizzard hub credential get gh-token
```

## The record

| Field         | Meaning                                                                                                                       |
| ------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `slug`        | The handle you type, unique in the tenant. It may be edited (`--slug`); the slug it leaves keeps naming the credential.       |
| `name`        | The display name (`--name`), the slug when not given. Only ever shown, never looked up.                                       |
| `forge`       | The forge it acts on, by id or slug; reads carry `forge_id`, `forge_slug` and `forge_name`. It never changes after `create`.  |
| `binding`     | How it authenticates. `github.token` is the one binding: a GitHub personal access token you supply. It never changes.         |
| `secret_name` | The stored secret holding the token (`--secret`).                                                                             |
| `settings`    | The binding's own settings. For `github.token`, the one setting is `variant` (`--variant`): `classic` or `fine-grained`.      |
| `authority`   | The identity it acts as, derived from its secret: `secret/<secret_name>` for `github.token`. It keys the forge's rate budget. |
| `revision`    | Starts at 1 and moves by one on every change.                                                                                 |
| `retired`     | Whether it is retired.                                                                                                        |

A credential with no `variant` is read for what its token is: the hub recognizes a fine-grained token by its
`github_pat_` prefix, and treats any other as classic. Declaring the variant makes it explicit.

A repository and a work source name their credential with `--credential`, by id or slug, on `create` and `edit`; their
reads carry `credential_id`, `credential_slug` and `credential_name`. The older `--secret` still works on both, and
names the `github.token` credential reading that secret.

## The verbs

- `create <slug>` stores the credential at revision 1, from `--forge`, `--binding`, `--secret` and, optionally, `--name`
  and `--variant`.
- `edit <credential>` changes only the flags given: `--slug`, `--name`, `--secret` and `--variant`. An edit that leaves
  every field as it is writes nothing and keeps the revision. A `--secret` edit moves every record naming the credential
  to that secret.
- `retire <credential>` hides it from `list` unless `--include-retired` is given.
- `enable <credential>` lifts the retirement, and is refused while its secret is retired.
- `get <credential>` shows its fields and `referrers`: every repository and work source naming it, retired ones
  included.
- `list` shows each credential's slug, id, revision, state, forge, binding and authority.

`get`, `edit`, `retire` and `enable` take the credential's id, its slug or a slug it has left. `edit`, `retire` and
`enable` take `--if-match <revision>` and are refused, naming the current revision, when the credential has moved on.
Every write is recorded in the [change log](./config-changes.md).

Writes need the `config:edit` permission and reads need `fleet:view`. Over HTTP the verbs are `GET` and `POST`
`/api/credentials`, `GET` and `PATCH` `/api/credentials/{credential}`, and `POST /api/credentials/{credential}/retire`
and `/enable`. A create body carries `slug`, `name`, `forge`, `binding`, `secret` and `settings`; a `PATCH` naming
`forge` or `binding` is refused.

## Setting up a GitHub token

On GitHub, delivery needs a token on the repository with these permissions. A classic token is the supported path for
reading checks:

| Permission      | Access       | Why                                                                         |
| --------------- | ------------ | --------------------------------------------------------------------------- |
| Contents        | Read & write | Read and move branches, compare revisions                                   |
| Pull requests   | Read & write | Open, update and merge pull requests                                        |
| Checks          | Read         | Read each check on a revision                                               |
| Commit statuses | Read         | Read commit statuses alongside check runs                                   |
| Actions         | Read & write | Re-run, once, a GitHub Actions check that is failing on the base branch too |

`land` waits for every check on the change to finish before it asks for that re-run. Without Actions write, an inherited
failure is never re-run and routes as the change's own failure. Upgrade existing tokens too: Checks read alone does not
grant access to commit statuses. See [delivery policy](./delivery-policy.md#checks-and-missing-ci) for the strict
default and recovery.

### Classic and fine-grained tokens

GitHub grants a fine-grained token no read of check runs, so the hub never offers `checks.read` for a repository acting
with a fine-grained credential, whether it declares `--variant fine-grained` or its token carries the `github_pat_`
prefix. A repository on one behind a landing policy that reads checks, `merge-on-checks` or `fast-forward`:

- is refused `422 forge_capability_missing` when it is created or edited onto that policy, and a credential edit that
  would take the capability away from an enabled repository is refused `credential_capability_missing`, naming each
  repository and what it would lose;
- escalates `forge-capability-missing` at the start of each `deliver` pass, naming the credential, before anything is
  sent to the forge ([forges.md](./forges.md#a-missing-capability));
- is named in one warning per such token at hub start, with its secret, the variant it was recognized as, and the
  repositories whose policies it cannot serve.

A check read GitHub refuses for a fine-grained token escalates `forge-auth` as `token-unsupported`, naming the
capability. The remedy is a classic token behind the credential, or the repository moved to `park`.

## Credentials at upgrade

Upgrading to a release with credentials gives every repository and work source that names only a secret a credential,
with no step of yours. Each forge and secret pair in use becomes one `github.token` credential with no `variant`, shared
by every record naming that pair; its slug is the secret's name, suffixed `-2`, `-3` and so on where the tenant already
holds that slug. A credential made for a retired secret is retired with it. The records keep `secret_name`, and no token
is re-entered: each keeps acting with the token it held before the upgrade. The change log records each credential as
created by `migration:credential-carry-over`.

## Rebinding a repository or a work source

Point a record at another credential with `edit --credential`:

```bash
blizzard hub repo edit blizzard --credential gh-token-2
blizzard hub source edit blizzard --credential gh-token-2
```

The hub reads a record's credential on every request it makes for it, so the next ingest, label sweep, closure or
`deliver` pass sends the new token, with no restart. A credential edit that moves its secret, and a secret's value
replaced with `blizzard hub secret set`, take effect the same way. Once nothing enabled names the old credential, it can
be retired.

## What is refused

- **A forge mismatch.** A record naming a credential of another forge is refused `credential_forge_mismatch`, as is an
  edit moving a record naming a credential to another forge without naming one there. A binding for another kind of
  forge than the credential's is refused `credential_forge_kind_mismatch`.
- **Invalid settings.** A setting the binding does not take, or a `variant` other than `classic` or `fine-grained`, is
  refused `credential_settings_invalid`, naming the setting. An unknown binding is refused `credential_binding_unknown`.
- **A taken slug or authority.** A slug another credential holds is refused `credential_slug_taken`. A second credential
  on the forge acting as the same authority is refused `credential_authority_taken`, naming the holder, retired or not;
  name that credential instead.
- **A missing or retired secret or credential.** A credential naming a secret that is unknown or retired is refused
  `secret_unavailable`. A record naming a credential that is unknown, or retired while the record is enabled, is refused
  `credential_unavailable`; enable the credential instead.
- **A secret disagreeing with the credential.** A record sent with both `--secret` and `--credential` that disagree, or
  with `--secret` changed while it names a credential, is refused `credential_conflict`.
- **Retiring a credential in use.** `retire` is refused `credential_in_use` while an enabled repository or work source
  names the credential, listing each in `referrers`; retire or rebind them first.
- **A token the forge refuses.** A token GitHub refuses with a 401 or 403 that is not a rate refusal escalates the chunk
  `forge-auth`, its detail naming the credential's slug, and records a `forge-auth-refused` event naming the credential,
  its binding and its variant, never the token ([observability.md](./observability.md#the-operational-event-log)). A
  work source's refused token records the same event once per label-sweep pass, and its work item's `error` carries the
  remedy. Replace the token with `blizzard hub secret set`, or rebind the record.
