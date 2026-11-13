"""Values resolved for a single harness invocation."""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from blizzard.foundation.roles import domain_model
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.harness_telemetry_plan import HarnessTelemetryPlan
from blizzard.runner.process.child_env import CLAUDE_CODE_SHELL_VARIABLE, AllowlistedEnv, ChildEnv


@domain_model
@dataclass(frozen=True)
class SessionEnvironment:
    """A resolved allowlisted tool profile and worker shell, frozen at launch."""

    pairs: tuple[tuple[str, str], ...]
    shell: str

    @classmethod
    def capture(cls, env: AllowlistedEnv) -> "SessionEnvironment":
        return cls(tuple(env.variables.items()), env.shell)

    @property
    def variables(self) -> dict[str, str]:
        return dict(self.pairs)

    def tool(self) -> ChildEnv:
        return ChildEnv(self.pairs)

    def worker(self, identity: Mapping[str, str]) -> ChildEnv:
        return self.tool().adding({CLAUDE_CODE_SHELL_VARIABLE: self.shell, **identity})


@domain_model
@dataclass(frozen=True)
class SessionSettings:
    """Immutable values supplied to a single worker, judge, or resume launch."""

    model: str | None = None
    effort: str | None = None
    compaction_window: str | None = None
    binary: str = ""
    default_model: str = ""
    autonomy: Autonomy = Autonomy.Dangerous
    environment: SessionEnvironment = field(kw_only=True)
    settings_path: str | None = None
    settings_argv: tuple[str, ...] = ()
    permission_override: str | None = None
    config_dir: str | None = None
    runner_id: str = ""
    driver_command: tuple[str, ...] = ()
    agent: str | None = None
    telemetry: HarnessTelemetryPlan = field(default_factory=HarnessTelemetryPlan)
    transcript_root: str = ""
    harness_id: str = ""
    bundle_path: str | None = None
    model_aliases: tuple[tuple[str, str], ...] = ()
    effort_aliases: tuple[tuple[str, str], ...] = ()
    supported_models: tuple[str, ...] = ()
    prompt_prefix: str = ""

    def for_session(self, origin: "SessionSettings") -> "SessionSettings":
        """Use a session's original locating values with this launch's other values."""
        # These environment entries locate the harness's session/config home;
        # the rest of the allowlisted worker profile belongs to this launch.
        home_keys = (
            "HOME",
            "CLAUDE_CONFIG_DIR",
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
            "OPENCODE_CONFIG_DIR",
            "OPENCODE_CONFIG",
            "OPENCODE_CONFIG_CONTENT",
        )
        env = self.environment.variables
        original = origin.environment.variables
        for key in home_keys:
            if key in original:
                env[key] = original[key]
            else:
                env.pop(key, None)
        return replace(
            self,
            environment=SessionEnvironment(tuple(env.items()), self.environment.shell),
            binary=origin.binary,
            settings_path=origin.settings_path,
            settings_argv=origin.settings_argv,
            config_dir=origin.config_dir,
            transcript_root=origin.transcript_root,
            bundle_path=origin.bundle_path,
            driver_command=origin.driver_command,
            harness_id=origin.harness_id,
        )
