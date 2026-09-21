"""The OpenCode usage-limit ``error`` run-event shape (blizzard#594 D6).

No real usage-limited run exists on the machine this was captured on (its own retained
worker stdout and ``~/.local/share/opencode/log/opencode.log`` carry no ``usage_limit``/
``429`` hit), so this is built from provider-documented material, confirmed against
strings in the installed ``opencode-ai`` binary (``opencode-ai@1.18.31``, resolved via
``bin/opencode.exe``):

- The event's ``error.name`` is the Vercel AI SDK's own class name for a provider HTTP
  failure, found verbatim in the binary: ``class ... {constructor({message,url,...
  statusCode,...}){...this.name=y}}`` where ``y = "AI_APICallError"``.
- The message text mirrors OpenCode's own account-usage-limit upsell copy, found verbatim
  in the binary: ``` `${f?`${f} usage limit`:"Usage limit"} reached. It will reset in
  ${g}. To continue using this model now, enable usage from your available balance` ```
  (the surrounding code names this path ``reason:"account_rate_limit"``, distinct from an
  ordinary transient rate limit).
- ``statusCode: 429`` matches the binary's own retryable-status set for an
  ``AI_APICallError`` (``isRetryable:...j===429...``) and OpenCode's own provider-refusal
  status roster (``opencode_facts.py::_PROVIDER_REFUSAL_STATUSES``).

This is the shape :meth:`OpenCodeAdapter.classify_usage_limit` reads: a ``type: "error"``
run event whose ``error.data.statusCode`` is 429 and whose ``error.data.message`` names a
usage limit — parseable by the production ``opencode_shapes.parse_run_event``, never a
shape the mock's own writer invents independently (``blizzard_mock``'s
``usage_limited()`` re-emits this same literal)."""

from __future__ import annotations

USAGE_LIMIT_EVENT: dict[str, object] = {
    "type": "error",
    "sessionID": "ses_usage_limit",
    "error": {
        "name": "AI_APICallError",
        "data": {
            "message": "Usage limit reached. It will reset in 2 hours. "
            "To continue using this model now, enable usage from your available balance",
            "statusCode": 429,
        },
    },
}
