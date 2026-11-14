"""The packaged advanced-development-workflow verify node's scoped exceptions."""

from blizzard.hub.graphs import PACKAGED


def test_verify_preserves_results_and_scopes_revisits() -> None:
    verify = PACKAGED.named("advanced-development-workflow").doc.node("verify")
    assert verify is not None and verify.prompt is not None
    prompt = " ".join(verify.prompt.split())
    assert (
        "Re-run a method the prior report records as failed unless that failure is explicitly waived within scope"
        in prompt
    )
    assert "For every declared method, including one with a waived prior failure" in prompt
    assert "run it when it does, unless its execution is explicitly waived" in prompt
    assert "Waiving a prior failure alone does not waive verification of changed behavior" in prompt
    assert "report the actual failed or not-run result, never as passed" in prompt
    assert "identify it and its scope" in prompt


def test_verify_first_visit_and_manual_method_respect_explicit_execution_exceptions() -> None:
    verify = PACKAGED.named("advanced-development-workflow").doc.node("verify")
    assert verify is not None and verify.prompt is not None
    prompt = " ".join(verify.prompt.split())
    assert "A first visit verifies the whole change within the operator-amended scope" in prompt
    assert "subject to explicit operator exceptions" in prompt
    assert "perform it unless a person's answer or note explicitly waives its execution" in prompt


def test_verify_judgement_limits_exceptions_and_keeps_results_honest() -> None:
    verify = PACKAGED.named("advanced-development-workflow").doc.node("verify")
    assert verify is not None and verify.judgement is not None and verify.judgement.prompt is not None
    prompt = " ".join(verify.judgement.prompt.split())
    assert "explicitly waived within scope by a person's answer or note" in prompt
    assert "A waiver does not make a failed or not-run method pass" in prompt
    assert "identify the waiver and preserve the actual result" in prompt
    assert "fail` on any unwaived failure, including a new or unrelated acceptance failure" in prompt


def test_verify_choices_reflect_amended_acceptance() -> None:
    verify = PACKAGED.named("advanced-development-workflow").doc.node("verify")
    assert verify is not None and verify.judgement is not None
    descriptions = {choice.name: choice.description for choice in verify.judgement.choices}
    passed = descriptions["pass"]
    failed = descriptions["fail"]
    assert passed is not None and "acceptance, as amended by any scoped operator exceptions" in passed
    assert failed is not None and "unresolved unwaived acceptance failures" in failed
