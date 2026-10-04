# Secrets

The hub stores named credentials, written once and never read back. A value is encrypted at rest under a hub key held
outside the store, and no API response, CLI output, log line, trace attribute, or event-log row carries it.

## The verbs

`blizzard hub secret set <name>`, `list [--include-retired]`, `show <name>`, `retire <name>`, `enable <name>`, and
`rotate-key` are the verbs. A name is lowercase letters, digits, and hyphens.

`set` reads the value from stdin and takes no value argument or option, so the value never lands in shell history or the
process list:

```bash
printf '%s' "$TOKEN" | blizzard hub secret set gh-token
```

Trailing CR/LF is stripped and an empty value is refused. `set` creates the secret at revision 1, and replaces an
existing one at the next revision. `show` and `list` report the name, revision, who replaced the value and when, and
whether the secret is retired.

`retire` is a reversible brake: a retired secret is hidden from `list` unless `--include-retired` is given, and cannot
be replaced until `enable` lifts it. Retirement never deletes the stored value.

Writes need the `config:edit` permission, held by `admin` and `superuser`; reads need `fleet:view`.

Over HTTP, `POST /api/secrets` creates and `PUT /api/secrets/{name}/value` replaces. A replace carrying an `If-Match`
header answers 409, naming the current revision, when that revision has moved.

## The hub key

A secret is sealed with AES-256-GCM under a 32-byte hub key, bound to its name and revision, so a ciphertext copied to
another row or restored at an older revision fails to open. The key has two sources; the first that applies wins:

1. **`BZ_HUB_SECRET_KEY`**, 32 random bytes in base64, is the only generation when set. `BZ_HUB_SECRET_KEY_PREVIOUS`
   optionally names the generation it replaced, so rows sealed under it stay readable until rotated. A malformed value
   fails the hub start naming the variable, never its content. Both are read once at start.
2. **`data/auth/secret-keys/`** in the hub's runtime directory otherwise. `blizzard hub init` and the first hub start
   mint it. The directory is `0700`, each key file `0600`, and `meta.json` names the `current` and `previous`
   generations.

The hub refuses to start when any stored secret, retired ones included, is sealed under a generation its key source does
not hold, and names the missing generation.

## Rotating the key

`blizzard hub secret rotate-key [--dir <hub runtime dir>]` runs offline, on the hub host, against the store and key
source directly. It re-seals every row under the new current key in one transaction, leaving each secret's revision
unchanged. A concurrent replace between its read and commit rolls the whole rotation back and exits non-zero; run it
again.

- **Directory source.** The verb mints a new generation, re-seals every row into it, then promotes it to `current` and
  demotes the old current to `previous`. A running hub reads the key files from disk on use, so it keeps decrypting
  throughout and needs no restart. The verb deletes a generation file only once no row references it.
- **Environment source.** Set `BZ_HUB_SECRET_KEY` to the new key and `BZ_HUB_SECRET_KEY_PREVIOUS` to the old one, run
  the verb, then restart the hub with the new key. With the previous key absent while rows need it, the verb refuses and
  names the missing generation.

## Key loss

The values are unrecoverable without their key. If every generation a row was sealed under is lost, the hub will not
start; restore the key from backup, or remove the secrets from the store and `set` each one again.
[Back up](../backup.md) `data/auth/secret-keys/` together with the store.
