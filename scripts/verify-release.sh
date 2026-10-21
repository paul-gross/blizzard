#!/usr/bin/env bash
# Prove a published release the way a stranger would: from outside, with no checkout.
#
# Usage: scripts/verify-release.sh <tag>
#
#   <tag>  `vX.Y.Z` or `vX.Y.Z-rc.N`, a release already published on paul-gross/blizzard.
#
# Needs `gh` (with the `attestation` command, 2.49 or later), `cosign`, `uv`, `curl`, and
# coreutils. It reads nothing from a repository, so it runs when copied alone into an empty
# directory. It exits 1 on the first failed check, naming the check, and 2 on a usage error:
#
#   1. image                the image's signature, and its version tag resolving to the same digest
#   2. image attestations   the image's provenance, and each platform's bill of materials
#   3. attached files       checksums, the exact file set, each file's provenance, the wheel's
#                           bill of materials, and the requirements files' project line
#   4. install              a fresh virtualenv synced from the release's requirements file
#
# Every signature and attestation is checked against the one release identity, so an artifact
# from any other workflow, the `edge` channel's included, cannot satisfy a check.
set -euo pipefail

REPO="paul-gross/blizzard"
IMAGE_REPO="paul-gross/blizzard-hub"
OIDC_ISSUER="https://token.actions.githubusercontent.com"
IDENTITY_REGEX='^https://github\.com/paul-gross/blizzard/\.github/workflows/release\.yml@refs/tags/v'
SPDX_PREDICATE="https://spdx.dev/Document/v2.3"

# The first release signed, attested, and inventoried as the contract describes; an earlier
# tag is refused by name rather than failed on its first missing file.
FIRST_VERIFIABLE="0.2.0-rc.2"
# The commit that first made release.yml write `compat-breaks.json`: a tag carries the file
# exactly when its commit descends from this one, whatever its version number.
COMPAT_BREAKS_COMMIT="c46927dcb5850580e02668da6d16d09aabd74f9d"

usage() {
  echo "usage: $0 <tag>   (vX.Y.Z or vX.Y.Z-rc.N)" >&2
  exit 2
}

fail() {
  echo "FAIL [$1]: $2" >&2
  exit 1
}

# Orders X.Y.Z-rc.N below X.Y.Z, which `sort -V` does not: prints a key that compares as an integer.
version_key() {
  local v="$1" rc=999999
  if [[ "$v" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)(-rc\.([0-9]+))?$ ]]; then
    [ -n "${BASH_REMATCH[5]}" ] && rc="${BASH_REMATCH[5]}"
    printf '%d%06d%06d%06d\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" "${BASH_REMATCH[3]}" "$rc"
  else
    return 1
  fi
}

at_or_after() { [ "$(version_key "$1")" -ge "$(version_key "$2")" ]; }

[ "$#" -eq 1 ] || usage
TAG="$1"
[[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-rc\.[0-9]+)?$ ]] || usage
VERSION="${TAG#v}"
# The wheel alone keeps the PEP 440 spelling of the version.
PEP440="${VERSION/-rc./rc}"

if ! at_or_after "$VERSION" "$FIRST_VERIFIABLE"; then
  echo "ERROR: $TAG predates signed, attested releases; the first verifiable release is v$FIRST_VERIFIABLE" >&2
  exit 1
fi

for tool in gh cosign uv curl sha256sum; do
  command -v "$tool" > /dev/null || fail "tools" "'$tool' is not on PATH"
done
gh attestation verify --help > /dev/null 2>&1 \
  || fail "tools" "this gh ($(gh --version | head -n1)) has no 'attestation' command; install gh 2.49 or later"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
FILES="$WORK/files"
mkdir -p "$FILES"

# JSON is read by a Python that uv supplies, so the script needs no jq.
py() { uv run --no-project --quiet python -I -c "$1" "${@:2}"; }

RELEASE_URL="https://github.com/$REPO/releases/download/$TAG"
WHEEL="blizzard-$PEP440-py3-none-any.whl"
IMAGE_FILE="blizzard-hub-$VERSION-image.txt"
REQ="blizzard-$VERSION-requirements.txt"
REQ_PG="blizzard-$VERSION-requirements-pg.txt"

cosign_verify() {
  cosign verify --certificate-oidc-issuer "$OIDC_ISSUER" --certificate-identity-regexp "$IDENTITY_REGEX" "$@"
}

attest_verify() {
  gh attestation verify "$1" --repo "$REPO" --cert-oidc-issuer "$OIDC_ISSUER" \
    --cert-identity-regex "$IDENTITY_REGEX" "${@:2}"
}

registry_token() {
  curl -fsSL "https://ghcr.io/token?scope=repository:$IMAGE_REPO:pull" \
    | py 'import json, sys; print(json.load(sys.stdin)["token"])'
}

# --- 1. the image -------------------------------------------------------------------------

echo "== 1. image"
gh release download "$TAG" --repo "$REPO" --dir "$FILES" --pattern "$IMAGE_FILE" \
  || fail "image" "$IMAGE_FILE is not on the release page"
IMAGE_REF="$(head -n1 "$FILES/$IMAGE_FILE")"
[[ "$IMAGE_REF" =~ ^ghcr\.io/$IMAGE_REPO@(sha256:[0-9a-f]{64})$ ]] \
  || fail "image" "$IMAGE_FILE does not hold ghcr.io/$IMAGE_REPO@sha256:<digest>: '$IMAGE_REF'"
DIGEST="${BASH_REMATCH[1]}"

cosign_verify "$IMAGE_REF" > /dev/null \
  || fail "image" "the signature on $IMAGE_REF does not verify under the release identity"

TOKEN="$(registry_token)" || fail "image" "no anonymous registry token for $IMAGE_REPO"
ACCEPT="application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json"
TAG_DIGEST="$(curl -fsSL -I -H "Authorization: Bearer $TOKEN" -H "Accept: $ACCEPT" \
  "https://ghcr.io/v2/$IMAGE_REPO/manifests/$VERSION" \
  | tr -d '\r' | awk 'tolower($1) == "docker-content-digest:" { print $2 }')" \
  || fail "image" "the registry has no tag $VERSION on $IMAGE_REPO"
[ "$TAG_DIGEST" = "$DIGEST" ] \
  || fail "image" "tag $VERSION on $IMAGE_REPO resolves to '$TAG_DIGEST', not the release's $DIGEST"

# --- 2. the image's attestations ----------------------------------------------------------

echo "== 2. image attestations"
curl -fsSL -H "Authorization: Bearer $TOKEN" -H "Accept: $ACCEPT" \
  "https://ghcr.io/v2/$IMAGE_REPO/manifests/$DIGEST" > "$WORK/index.json" \
  || fail "image attestations" "the manifest list $DIGEST cannot be read from the registry"
[ "sha256:$(sha256sum "$WORK/index.json" | cut -d' ' -f1)" = "$DIGEST" ] \
  || fail "image attestations" "the manifest list read from the registry does not hash to $DIGEST"

attest_verify "oci://$IMAGE_REF" > /dev/null \
  || fail "image attestations" "the build provenance of $IMAGE_REF does not verify"

# The bills of materials are attested against each platform's digest, not the list's.
for ARCH in amd64 arm64; do
  PLATFORM_DIGEST="$(py '
import json, sys
arch = sys.argv[1]
for m in json.load(open(sys.argv[2]))["manifests"]:
    p = m.get("platform", {})
    if p.get("os") == "linux" and p.get("architecture") == arch:
        print(m["digest"])
        break
' "$ARCH" "$WORK/index.json")" || true
  [[ "$PLATFORM_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] \
    || fail "image attestations" "the manifest list holds no linux/$ARCH image"
  attest_verify "oci://ghcr.io/$IMAGE_REPO@$PLATFORM_DIGEST" --predicate-type "$SPDX_PREDICATE" > /dev/null \
    || fail "image attestations" "the linux/$ARCH bill of materials does not verify"
done

# --- 3. the attached files ----------------------------------------------------------------

echo "== 3. attached files"
EXPECTED=("$WHEEL" "$REQ" "$REQ_PG" "blizzard-$VERSION.spdx.json" "$IMAGE_FILE" "SHA256SUMS"
  "blizzard-hub-$VERSION-linux-amd64.spdx.json" "blizzard-hub-$VERSION-linux-arm64.spdx.json")
STATUS="$(gh api "repos/$REPO/compare/$COMPAT_BREAKS_COMMIT...$TAG" --jq .status)" \
  || fail "attached files" "cannot compare $TAG against $COMPAT_BREAKS_COMMIT"
case "$STATUS" in ahead | identical) EXPECTED+=("compat-breaks.json") ;; esac

PAGE="$(gh release view "$TAG" --repo "$REPO" --json assets --jq '.assets[].name' | LC_ALL=C sort)" \
  || fail "attached files" "the release page for $TAG cannot be read"
WANT="$(printf '%s\n' "${EXPECTED[@]}" | LC_ALL=C sort)"
MISSING="$(comm -13 <(echo "$PAGE") <(echo "$WANT"))"
EXTRA="$(comm -23 <(echo "$PAGE") <(echo "$WANT"))"
[ -z "$MISSING" ] || fail "attached files" "the release page is missing: $(echo $MISSING)"
[ -z "$EXTRA" ] || fail "attached files" "the release page holds files outside the inventory: $(echo $EXTRA)"

gh release download "$TAG" --repo "$REPO" --dir "$FILES" --clobber \
  || fail "attached files" "the files cannot be downloaded from $TAG"
(cd "$FILES" && sha256sum -c SHA256SUMS > /dev/null) \
  || fail "attached files" "sha256sum -c SHA256SUMS fails over the downloaded files"

for NAME in "${EXPECTED[@]}"; do
  attest_verify "$FILES/$NAME" > /dev/null \
    || fail "attached files" "the build provenance of $NAME does not verify"
done
attest_verify "$FILES/$WHEEL" --predicate-type "$SPDX_PREDICATE" > /dev/null \
  || fail "attached files" "the wheel's bill of materials does not verify"

WHEEL_SUM="$(awk -v f="$WHEEL" '$2 == f { print $1 }' "$FILES/SHA256SUMS")"
PROJECT_LINE="blizzard @ $RELEASE_URL/$WHEEL --hash=sha256:$WHEEL_SUM"
for NAME in "$REQ" "$REQ_PG"; do
  [ "$(tail -n1 "$FILES/$NAME")" = "$PROJECT_LINE" ] \
    || fail "attached files" "$NAME does not end with the project line naming this release's wheel at its SHA256SUMS hash"
done

# --- 4. the install -----------------------------------------------------------------------

echo "== 4. install"
uv venv --quiet --python 3.12 "$WORK/venv" || fail "install" "a fresh Python 3.12 virtualenv cannot be created"
uv pip sync --quiet --python "$WORK/venv" --require-hashes "$RELEASE_URL/$REQ" \
  || fail "install" "uv pip sync --require-hashes from the release's $REQ fails"
REPORTED="$("$WORK/venv/bin/blizzard" --version)" || fail "install" "blizzard --version fails in the fresh virtualenv"
[ "$REPORTED" = "blizzard, version $PEP440" ] \
  || fail "install" "blizzard --version printed '$REPORTED', expected 'blizzard, version $PEP440'"

echo "OK: $TAG verified (image, image attestations, attached files, install)"
