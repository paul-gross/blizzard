# Verifying a release

This document owns how a stranger checks that a release is what blizzard built, without trusting blizzard: what a
release publishes, the identity that signs it, and the check for each artifact. The first release it applies to is
`v0.2.0-rc.2`; an earlier tag carries none of this.

## What a release publishes

A release `vX.Y.Z` (or `vX.Y.Z-rc.N`) is a GitHub release on `paul-gross/blizzard` plus an image on GHCR. Its page
carries exactly these files; `<v>` is the version without its `v`, and the wheel alone spells it the PEP 440 way
(`0.2.0rc2`):

| File                                         | What it is                                                              |
| -------------------------------------------- | ----------------------------------------------------------------------- |
| `blizzard-<pep440>-py3-none-any.whl`         | the wheel                                                               |
| `blizzard-<v>-requirements.txt`              | every dependency pinned with hashes, ending in a line naming the wheel  |
| `blizzard-<v>-requirements-pg.txt`           | the same for a Postgres-backed hub                                      |
| `blizzard-<v>.spdx.json`                     | the wheel's bill of materials                                           |
| `blizzard-hub-<v>-image.txt`                 | one line: `ghcr.io/paul-gross/blizzard-hub@sha256:<digest>`             |
| `blizzard-hub-<v>-linux-amd64.spdx.json`     | the image's bill of materials, `linux/amd64`                            |
| `blizzard-hub-<v>-linux-arm64.spdx.json`     | the image's bill of materials, `linux/arm64`                            |
| `compat-breaks.json`                         | the compatibility report (not `v0.2.0` or `release/0.2`)                |
| `SHA256SUMS`                                 | the checksum of every other file                                        |

The hub image is the multi-arch manifest list at the digest in `blizzard-hub-<v>-image.txt`; its tag `<v>` resolves to
the same digest. There is no package-index release.

## The release identity

Everything is signed or attested keylessly by the release workflow, so one identity covers every check:

- **Issuer:** `https://token.actions.githubusercontent.com`
- **Identity:** `https://github.com/paul-gross/blizzard/.github/workflows/release.yml@refs/tags/v<version>`, matched as
  the regexp `^https://github\.com/paul-gross/blizzard/\.github/workflows/release\.yml@refs/tags/v`

An artifact from any other workflow, the `edge` channel's included, cannot satisfy a check against it.

## Checking each artifact

Needs `gh` 2.49 or later (for `gh attestation`), `cosign`, and `sha256sum`.

**The image's signature.** Take the digest from the image file, then:

```bash
cosign verify ghcr.io/paul-gross/blizzard-hub@sha256:<digest> \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp '^https://github\.com/paul-gross/blizzard/\.github/workflows/release\.yml@refs/tags/v'
```

**The image's provenance and bills of materials.** Provenance attaches to the manifest list; each platform's bill of
materials attaches to that platform's own digest, found in the manifest list:

```bash
gh attestation verify oci://ghcr.io/paul-gross/blizzard-hub@sha256:<digest> --repo paul-gross/blizzard \
  --cert-oidc-issuer https://token.actions.githubusercontent.com \
  --cert-identity-regex '^https://github\.com/paul-gross/blizzard/\.github/workflows/release\.yml@refs/tags/v'
gh attestation verify oci://ghcr.io/paul-gross/blizzard-hub@sha256:<platform-digest> --repo paul-gross/blizzard \
  --predicate-type https://spdx.dev/Document/v2.3
```

**Any attached file's provenance.** Every file on the release page carries build provenance:

```bash
gh attestation verify blizzard-<v>-requirements.txt --repo paul-gross/blizzard
```

**The wheel's bill of materials.** The same command with the wheel and `--predicate-type
https://spdx.dev/Document/v2.3`; the `.spdx.json` file is that document.

**The checksums.** Download every file into one directory, then `sha256sum -c SHA256SUMS` from inside it. A requirements
file's last line names the wheel at its `SHA256SUMS` hash, so verifying the requirements file's provenance vouches for
the wheel and every dependency an install fetches.

## Running all of it

[`scripts/verify-release.sh`](../scripts/verify-release.sh) `<tag>` runs every check above, plus a fresh install from the
release's requirements file, against a published release with no checkout; it needs `gh`, `cosign`, `uv`, and `curl`,
and refuses a tag before `v0.2.0-rc.2`. The release workflow runs it last, as its `verify` job
([ci.md](./ci.md)).
