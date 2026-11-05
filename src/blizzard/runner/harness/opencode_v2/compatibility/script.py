"""The exact prompts and commands the OpenCode 2 proof asks a model to run, and the markers it reads back."""

from __future__ import annotations

ENV_MARKER = "BLZ_PROOF_ENV"
ENV_PROMPT = (
    "Use the shell tool exactly once to run `echo ROOT=${BLZ_PROOF_ENV:-} OTHER=${BLZ_PROOF_OTHER:-}`. Then use the "
    "subagent tool exactly once to launch a `general` subagent whose instructions are: use the shell tool exactly "
    "once to run `echo CHILD=${BLZ_PROOF_ENV:-}` and report its exact output. Then report both outputs verbatim and "
    "stop."
)
VERBATIM_PROMPT = "Reply with the word READY and nothing else.\nLine two carries \"double\" and 'single' quotes."
SLEEP_SECONDS = 40
SLEEP_COMMAND = f"echo $$ > proof-sleep.pid; sleep {SLEEP_SECONDS}; echo SLEPT"
SLEEP_PROMPT = f"Use the shell tool exactly once to run `{SLEEP_COMMAND}` and wait for it. Then reply DONE."
CHILD_SLEEP_PROMPT = (
    "Use the subagent tool exactly once to launch a `general` subagent whose instructions are: use the shell tool "
    f"exactly once to run `{SLEEP_COMMAND}` and wait for it. Wait for the subagent, then reply DONE."
)
BACKGROUND_CHILD_PROMPT = (
    "Use the subagent tool exactly once, with background set to true, to launch a `general` subagent whose "
    "instructions are: use the shell tool exactly once to run `sleep 8; echo BACKGROUND-DONE` and report its output. "
    "Do not wait for it; reply STARTED immediately."
)
JUDGEMENT_PROMPT = (
    "Use the shell tool exactly once to run `echo tool-output-not-the-verdict`. Then reply with exactly "
    "`<Choice>pass</Choice>` followed by one sentence of assessment."
)
QUEUE_PROMPT = "Reply with the word QUEUED and nothing else."
STEER_PROMPT = "Stop what you are doing and reply with the word STEERED and nothing else."
DENY_SHELL_COMMAND = "touch proof-denied-shell"
DENY_PROMPT = (
    f"Use the shell tool exactly once to run `{DENY_SHELL_COMMAND}`. If it is refused, do not retry; reply REFUSED."
)
DENY_TOOL_PROMPT = (
    "Use the read tool exactly once to read `README.md`. If it is refused, do not retry and do not use any other "
    "tool; reply REFUSED. Otherwise reply with its first line."
)
ASK_COMMAND = "touch proof-asked"
ASK_PROMPT = f"Use the shell tool exactly once to run `{ASK_COMMAND}`. Then reply ASKED."
CONCURRENT_PROMPT = (
    "Use the shell tool to run `echo LANE=${BLZ_PROOF_ENV:-}`. Then, as a separate shell tool call, run "
    "`touch proof-lane-$BLZ_PROOF_ENV`; if that is refused, do not retry. Then reply with the LANE line only."
)
COMPACT_SEED_PROMPT = "Reply with the word SEED and nothing else."
RESUME_PROMPT = "Reply with the word RESUMED and nothing else."
TAKEOVER_PROMPT = "Reply with the word TAKEN and nothing else."
RESTART_COMMAND = "sleep 20; echo RESTART-SURVIVED"
RESTART_PROMPT = f"Use the shell tool exactly once to run `{RESTART_COMMAND}` and wait for it. Then reply DONE."
RESTART_ENV_PROMPT = "Use the shell tool exactly once to run `echo AFTER=${BLZ_PROOF_ENV:-}`. Reply with its output."
NOTES_FILE = "NOTES.md"
NOTES_TEXT = "first line of the proof notes\n"
SKILL_NAME = "proof-skill"
SKILL_DOCUMENT = (
    f"---\nname: {SKILL_NAME}\ndescription: The blizzard proof skill. Load it when asked to load {SKILL_NAME}.\n---\n"
    "Reply with the word SKILLED.\n"
)
TOOLS_PROMPT = (
    f"Use the read tool exactly once to read `{NOTES_FILE}`. Then use your skill tool exactly once to load the skill "
    f"named `{SKILL_NAME}`, and follow it."
)
