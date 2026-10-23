# shellcheck shell=bash
# scripts/trailers.sh — sourced, not run: the one reading of a commit message's trailers.
#
# check-line-provenance.sh and release-cut-in-range.sh both read `Release-Cut:` through this,
# so a pull request is a release cut by exactly the rule that lets a cut onto a release line.

# Does the message carry a trailer with this key and a non-empty value?
has_trailer() {
  git interpret-trailers --parse <<< "$2" | grep -Eq "^$1:[[:space:]]*[^[:space:]]"
}
