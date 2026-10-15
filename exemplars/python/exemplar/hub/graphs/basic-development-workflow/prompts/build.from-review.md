# Re-entering build from review

Review found blocking issues, each recorded in the envelope's `review-findings` asset. Your commits are intact on the
feature branch; nothing has landed. Answer every finding by fixing or refuting it, then commit, push, re-declare the
tip, and declare done again.

- Check each finding against the current code first; one an earlier attempt resolved needs no second fix.
- Refute a finding only when it is factually wrong, rests on a false premise, or demands work beyond the change's scale.
- Record each refutation in `review-finding-refutes`: the finding's anchor (`<repo>/<path>:<line>`) copied verbatim, the
  id answered, and the argument with evidence. The reviewer matches on the anchor, since every pass renumbers ids.
- Commit each repair on top of the reviewed tip — never amend, rebase, or merge the base branch; integration is
  `pre-push`'s.

The finding format this restates is retrievable directly:
`blizzard runner artifact get --scope system review/finding-format --content`. If that read fails or comes back empty,
proceed on the restatement above.
