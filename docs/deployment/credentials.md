# Credentials

A credential is what a [repository](./repositories.md) or a [work source](./work-sources.md) acts with when the hub
calls its [forge](./forges.md). A hub holds each one as a stored record, managed with `blizzard hub credential`. A
credential reads a stored [secret](./secrets.md), which holds a token you supply or a GitHub App's private key; the
credential says how it authenticates and which forge it acts on. Many records may name one credential, and replacing its
token or moving it to another secret moves every one of them.

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

| Field         | Meaning                                                                                                                                                                                                                                                  |
| ------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `slug`        | The handle you type, unique in the tenant. It may be edited (`--slug`); the slug it leaves keeps naming the credential.                                                                                                                                  |
| `name`        | The display name (`--name`), the slug when not given. Only ever shown, never looked up.                                                                                                                                                                  |
| `forge`       | The forge it acts on, by id or slug; reads carry `forge_id`, `forge_slug` and `forge_name`. It never changes after `create`.                                                                                                                             |
| `binding`     | How it authenticates: `github.token`, a GitHub personal access token you supply, or `github.app-installation`, a GitHub App's installation ([GitHub Apps](#github-app-installations)). It never changes.                                                 |
| `secret_name` | The stored secret holding the token, or the App's private key (`--secret`).                                                                                                                                                                              |
| `settings`    | The binding's own settings. For `github.token`, the one setting is `variant` (`--variant`): `classic` or `fine-grained`. For `github.app-installation`, both `app_id` (`--app-id`) and `installation_id` (`--installation-id`), each a positive integer. |
| `authority`   | The identity it acts as: `secret/<secret_name>` for `github.token`, `app/<app_id>/installation/<installation_id>` for `github.app-installation`. It keys the forge's rate budget.                                                                        |
| `revision`    | Starts at 1 and moves by one on every change.                                                                                                                                                                                                            |
| `retired`     | Whether it is retired.                                                                                                                                                                                                                                   |

A credential with no `variant` is read for what its token is: the hub recognizes a fine-grained token by its
`github_pat_` prefix, and treats any other as classic. Declaring the variant makes it explicit.

A repository and a work source name their credential with `--credential`, by id or slug, on `create` and `edit`; their
reads carry `credential_id`, `credential_slug` and `credential_name`. The older `--secret` still works on both, and
names the `github.token` credential reading that secret.

## The verbs

- `create <slug>` stores the credential at revision 1, from `--forge`, `--binding`, `--secret`, the binding's settings
  (`--variant`, or `--app-id` and `--installation-id`) and, optionally, `--name`.
- `edit <credential>` changes only the flags given: `--slug`, `--name`, `--secret`, `--variant`, `--app-id` and
  `--installation-id`. An App credential's `--app-id` and `--installation-id` replace its settings together, so an edit
  giving one gives both. An edit that leaves every field as it is writes nothing and keeps the revision. A `--secret`
  edit moves every record naming the credential to that secret.
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

## GitHub App installations

A `github.app-installation` credential acts as one installation of a GitHub App. You supply the App's private key, and
the hub mints the installation's short-lived tokens itself: each on first use, narrowed to what the call needs, renewed
before it expires, and minted again after a restart. Tokens are held in memory only, never stored. Every repository and
work source naming the credential shares the installation's one rate budget, and its one lane of `deliver` writes.

### Creating and installing the App

1. On GitHub, create a GitHub App under the account or organization that owns the repositories. It needs no webhook and
   no callback URL. Grant it the repository permissions in [the table below](#app-permissions).
2. Note the App's id, shown on its settings page, and generate a private key there. GitHub downloads it as a `.pem`
   file.
3. Install the App on the account, choosing the repositories the hub lands on and reads work from. The installation id
   is the last segment of the installation's settings URL, `…/settings/installations/<id>`.

### App permissions

The hub mints each token with the permissions of what it is for: a `deliver` step's landing, or a work source's reads
and label writes. An installation without a required permission is refused at the mint. One without an optional
permission keeps working without what that permission serves:

| Scope         | Permission      | Access  | Needed for     |
| ------------- | --------------- | ------- | -------------- |
| `landing`     | `metadata`      | `read`  | required       |
| `landing`     | `contents`      | `write` | required       |
| `landing`     | `pull_requests` | `write` | required       |
| `landing`     | `checks`        | `read`  | `checks.read`  |
| `landing`     | `statuses`      | `read`  | `checks.read`  |
| `landing`     | `actions`       | `write` | `checks.rerun` |
| `work-source` | `metadata`      | `read`  | required       |
| `work-source` | `issues`        | `write` | required       |
| `work-source` | `pull_requests` | `read`  | required       |

GitHub names these Metadata, Contents, Pull requests, Checks, Commit statuses, Actions, and Issues. An installation
without Checks read or Commit statuses read offers no `checks.read`, so a repository on it behind a landing policy that
reads checks escalates `forge-capability-missing` at the start of each `deliver` pass, as a fine-grained token does
([Classic and fine-grained tokens](#classic-and-fine-grained-tokens)). Without Actions write, an inherited failure is
never re-run.

### Registering the credential

Store the private key as a secret, then create the credential naming it:

```bash
blizzard hub secret create gh-app-key < my-app.private-key.pem
blizzard hub credential create gh-app --forge github --binding github.app-installation --secret gh-app-key \
  --app-id 123456 --installation-id 7890123
blizzard hub repo edit blizzard --credential gh-app
blizzard hub source edit blizzard --credential gh-app
```

`blizzard hub credential get gh-app` shows its authority, `app/123456/installation/7890123`, and never the key.

On the board, a user with `config:edit` does the same on a desktop from the Admin page. On the Secrets tab, create a
secret and paste the whole `.pem` file into its value; the field is masked and keeps the key's line breaks. On the
Credentials tab, create a credential, pick `github.app-installation` as its binding and the secret, and enter the App id
and the installation id as numbers. The credential's detail shows its authority, read-only.

### Replacing the key

Generate a new private key on the App's settings page, then replace the secret's value:

```bash
blizzard hub secret set gh-app-key < my-app.new-private-key.pem
```

The next token is minted with the new key, with no restart. Delete the old key on GitHub once the replacement is in
place; until then GitHub accepts both. To move the credential to another installation, edit both ids at once:
`blizzard hub credential edit gh-app --app-id 123456 --installation-id 7890456`.

### When GitHub refuses the App

A refusal escalates the chunk at its `deliver` pass and records a `forge-auth-refused` event, as for a token
([What is refused](#what-is-refused)). The escalation's detail and the event both carry the remedy:

| What happened                                                       | What the hub reports                                                                             | Remedy                                                                                 |
| ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------- |
| The App's key was replaced or deleted on GitHub, or its id is wrong | `forge-auth`, `key-rejected`                                                                     | Replace the App's private key, or correct its App id, or check the hub's clock.        |
| The installation was deleted, or its id is wrong                    | `forge-auth`, `installation-missing`                                                             | Reinstall the App, or correct the installation id.                                     |
| The installation was suspended                                      | `forge-auth`, `installation-suspended`                                                           | Unsuspend the App's installation.                                                      |
| A required permission was withdrawn                                 | `forge-auth`, `permission-missing`, naming the permission                                        | Grant the permission it names, such as `contents=write`, to the App installation.      |
| A token is refused again after the hub minted it afresh             | `forge-auth`, `credential-invalid`                                                               | Replace the token, or check the App's installation.                                    |
| The repository was removed from the installation                    | `forge-config`: the repository is not found, or the installation does not include it             | Add the repository to the installation.                                                |
| Checks read or Commit statuses read was withdrawn                   | `forge-capability-missing` at the start of each `deliver` pass behind a policy that reads checks | Grant the permission, or move the repository to a landing policy that reads no checks. |

A token GitHub refuses once, because it was revoked or expired early, is dropped and minted again, and the request is
sent once more. That is logged as a warning, and nothing escalates. The hub signs the App's requests with its own clock,
so a hub clock far from GitHub's is refused as `key-rejected`.

A work source's refused App records the same `forge-auth-refused` event once per label-sweep pass, including when its
binding cannot be built at all.

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
