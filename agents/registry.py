"""Architecture ids -> runnable architectures.

An LLM architecture is a harness (see harness/): a preset like `retry`, or a
preset edited slot by slot like `single+tools=retrying+feedback=critic`. The
scripted controls (`oracle`, `oracle_retry`) have no model, so only their
tools and environment slots can be edited.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache, partial

from harness import BadHarness, HarnessSpec, PRESETS, SLOTS, apply_edits, code_hashes, fingerprint, manifest, split
from harness.base import code_hash
from harness import ablation as harness_ablation
from harness.spec import dedupe
from harness import runner as harness_runner
from harness.components import option
from tasks.spec import Task

from . import oracle
from .loop import AgentContext, Conversation
from .toolbox import Toolbox

SCRIPTED_SLOTS = ("tools", "environment")


@dataclass(frozen=True)
class Architecture:
    id: str
    run: Callable[[Task, AgentContext], str | None]
    spec: HarnessSpec
    needs_llm: bool = True
    description: str = ""
    policy: str = "llm"

    @property
    def toolbox(self) -> type[Toolbox]:
        return option("tools", self.spec.tools).impl

    @property
    def slots(self) -> tuple[str, ...]:
        return SLOTS if self.needs_llm else SCRIPTED_SLOTS

    def make_toolbox(self, base_url: str, on_event=None, **kw) -> Toolbox:
        env = option("environment", self.spec.environment).impl
        return self.toolbox(base_url, on_event=on_event, idempotent=env["idempotent"], **kw)

    def manifest(self) -> dict:
        policy = {"id": self.policy, "version": oracle.VERSION} if self.policy == "oracle" else None
        return manifest(self.spec, self.slots, policy)

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.manifest())

    def code(self) -> dict:
        """Hashes of the source behind each part, for the audit's version-bump check."""
        policy = (oracle,) if self.policy == "oracle" else (harness_runner, Conversation, AgentContext)
        return {"policy": code_hash(policy), **code_hashes(self.spec, self.slots)}

    def record(self) -> dict:
        """What a trial stores about the harness that produced it."""
        return {"fingerprint": self.fingerprint, "manifest": self.manifest(), "code": self.code()}


SCRIPTED = {
    "oracle": (HarnessSpec(), "scripted per-task policy, no LLM (control)"),
    "oracle_retry": (HarnessSpec(tools="retrying"), "oracle on the retrying client, no LLM (control)"),
}


def _describe(head: str, base: HarnessSpec, spec: HarnessSpec, preset_description: str) -> str:
    changed = spec.differs(base)
    if not changed:
        return preset_description
    return f"{head} with " + ", ".join(f"{s} = {getattr(spec, s)}" for s in changed)


@lru_cache(maxsize=None)
def resolve(arch_id: str) -> Architecture:
    """Raises BadHarness for an id that doesn't name a valid harness."""
    head, edits = split(arch_id)
    if head in SCRIPTED:
        base, desc = SCRIPTED[head]
        spec = apply_edits(base, edits, allowed=SCRIPTED_SLOTS)
        return Architecture(arch_id, oracle.run, spec, False, _describe(head, base, spec, desc), "oracle")
    if head not in PRESETS:
        raise BadHarness(f"unknown architecture {head!r}; have {', '.join([*PRESETS, *SCRIPTED])}")
    preset = PRESETS[head]
    spec = apply_edits(preset.spec, edits)
    run = partial(harness_runner.run, spec=spec)
    return Architecture(arch_id, run, spec, True, _describe(head, preset.spec, spec, preset.description))


def ablation(arch_id: str) -> list[str]:
    """harness.ablation for LLM harnesses; the scripted controls ablate around oracle."""
    head, edits = split(arch_id)
    if head not in SCRIPTED:
        return harness_ablation(arch_id)
    spec = resolve(arch_id).spec
    changed = spec.differs(SCRIPTED["oracle"][0])
    if not changed:
        raise BadHarness(f"{arch_id} is the plain oracle; nothing to ablate")
    names = (["oracle"] + [f"oracle+{s}={getattr(spec, s)}" for s in changed]
             + [f"{arch_id}-{s}" for s in changed] + [arch_id])
    return dedupe(names, lambda n: resolve(n).spec)


def is_valid(arch_id: str) -> bool:
    try:
        resolve(arch_id)
    except BadHarness:
        return False
    return True


# the named architectures, for listings: harness presets, then the scripted controls
ARCHITECTURES = {a: resolve(a) for a in [*PRESETS, *SCRIPTED]}
LLM_PRESETS = list(PRESETS)
