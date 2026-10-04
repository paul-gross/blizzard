"""The exact script the compatibility proof asks OpenCode to run.

Every prompt, agent name, and Bash command the probe both authorizes in the runner-owned
config and later looks for in OpenCode's output.  One home keeps the two halves spelled
identically: a command the config allows but the prompt never requests proves nothing.
"""

from __future__ import annotations

FRESH_PROMPT = (
    "Compatibility proof fresh turn. In this disposable repository, use the Bash tool exactly "
    "three times with these exact commands in order: `printf 'ok\\n' > compatibility-proof.txt`, "
    "`git add compatibility-proof.txt`, and `git commit -q -m 'compatibility: model proof'`. "
    "Then run the Bash command `sleep 5` as the final tool call and wait for it to finish. "
    "Finally reply <Choice>pass</Choice>. Do not do anything else."
)
RESUME_PROMPT = "Compatibility proof resume turn. Reply <Choice>pass</Choice> and nothing else."
PROCESS_CONTROL_COMMAND = "sleep 30"
PROCESS_CONTROL_PROMPT = (
    "Compatibility proof process-control turn. Use the Bash tool exactly once with the command "
    f"`{PROCESS_CONTROL_COMMAND}` and do not finish until it completes."
)
PERMISSION_TOOL = "bash"
PERMISSION_AGENT = "compatibility"
TOOL_AGENT = "compatibility-tools"
PERMISSION_COMMAND = "printf permission-probe"
CONFIG_PERMISSION_COMMAND = "printf config-isolation-probe"
SECURITY_DENIAL_COMMAND = "printf security-probe"
PERMISSION_PROMPT = (
    "Compatibility permission proof. Use the Bash tool exactly once with the exact command "
    f"`{PERMISSION_COMMAND}`. Make the tool call; do not replace it with prose."
)
CONFIGURATION_PROMPT = (
    "Compatibility configuration turn. Use the Bash tool exactly once with the exact command "
    f"`{CONFIG_PERMISSION_COMMAND}`. Make the tool call; do not replace it with prose."
)
TAKEOVER_PROMPT = "Compatibility takeover proof. Reply <Choice>pass</Choice> and nothing else."
SAFE_PROOF_COMMAND = "printf 'ok\\n' > compatibility-proof.txt"
SAFE_COMMIT_ADD_COMMAND = "git add compatibility-proof.txt"
SAFE_COMMIT_COMMAND = "git commit -q -m 'compatibility: model proof'"

PERMISSION_DENIAL_MESSAGES = frozenset(
    {
        "the user has specified a rule which prevents you from using this specific tool call.",
        "the configured permission rule prevented this specific tool call.",
    }
)


__all__ = [
    "CONFIGURATION_PROMPT",
    "CONFIG_PERMISSION_COMMAND",
    "FRESH_PROMPT",
    "PERMISSION_AGENT",
    "PERMISSION_COMMAND",
    "PERMISSION_DENIAL_MESSAGES",
    "PERMISSION_PROMPT",
    "PERMISSION_TOOL",
    "PROCESS_CONTROL_COMMAND",
    "PROCESS_CONTROL_PROMPT",
    "RESUME_PROMPT",
    "SAFE_COMMIT_ADD_COMMAND",
    "SAFE_COMMIT_COMMAND",
    "SAFE_PROOF_COMMAND",
    "SECURITY_DENIAL_COMMAND",
    "TAKEOVER_PROMPT",
    "TOOL_AGENT",
]
