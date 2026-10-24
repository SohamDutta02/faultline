import argparse
import json

import pytest

import harness
from agents.llm import Reply, ScriptedBackend, ToolCall
from agents.registry import ARCHITECTURES, resolve
from harness import BASELINE, SLOTS, BadHarness, HarnessSpec, name_of, parse
from harness.instructions import FAULT_AWARE
from runner.trial import arch_id, run_trial
from tasks.catalog import BY_ID


def test_every_slot_has_a_baseline_option_and_descriptions():
    assert SLOTS == ("instructions", "tools", "environment", "state", "feedback")
    for slot in SLOTS:
        assert getattr(BASELINE, slot) in harness.OPTIONS[slot]
        assert harness.SLOT_DESCRIPTIONS[slot]
        assert all(o.description for o in harness.OPTIONS[slot].values())


def test_presets_are_harness_specs():
    assert parse("single") == BASELINE
    assert parse("retry") == parse("single+tools=retrying") == HarnessSpec(tools="retrying")
    assert parse("critic") == HarnessSpec(feedback="critic")


def test_edits_set_and_reset_slots():
    spec = parse("critic+tools=retrying+instructions=fault_aware")
    assert spec.as_dict() == {**BASELINE.as_dict(), "tools": "retrying", "feedback": "critic",
                              "instructions": "fault_aware"}
    assert parse("retry-tools") == BASELINE
    assert spec.differs(BASELINE) == ["instructions", "tools", "feedback"]


@pytest.mark.parametrize("name, message", [
    ("nope", "unknown harness preset"),
    ("single+colour=red", "unknown subsystem"),
    ("single+tools=magic", "unknown tools option"),
    ("single+tools", "needs an option"),
    ("single-tools=plain", "takes no value"),
    ("Single", "can't read"),
    ("", "can't read"),
])
def test_bad_names_are_explained(name, message):
    with pytest.raises(BadHarness, match=message):
        parse(name)


def test_canonical_names():
    assert name_of(HarnessSpec(tools="retrying")) == "retry"
    assert name_of(HarnessSpec(tools="retrying", feedback="critic")) == "single+tools=retrying+feedback=critic"
    assert parse(name_of(HarnessSpec(instructions="fault_aware"))) == HarnessSpec(instructions="fault_aware")


def test_architectures_resolve_with_fingerprints():
    assert set(ARCHITECTURES) >= {"single", "retry", "critic", "oracle", "oracle_retry"}
    assert resolve("retry").fingerprint == resolve("single+tools=retrying").fingerprint
    assert resolve("single").fingerprint != resolve("single+instructions=fault_aware").fingerprint
    assert resolve("single+feedback=critic").description == "single with feedback = critic"
    manifest = resolve("critic").manifest()
    assert set(manifest) == {"policy", *SLOTS} and "model" not in manifest
    assert manifest["instructions"]["content"]["system"] == harness.instructions.BASE


def test_scripted_controls_only_edit_tools_and_environment():
    assert resolve("oracle+tools=retrying").spec == resolve("oracle_retry").spec
    assert not resolve("oracle").needs_llm
    assert set(resolve("oracle").manifest()) == {"policy", "tools", "environment"}
    # the oracle's fingerprint ignores prompts it never sends
    assert resolve("oracle").manifest()["policy"]["id"] == "oracle"
    with pytest.raises(BadHarness, match="can't be changed"):
        resolve("oracle+feedback=critic")


def test_cli_validates_architecture_ids():
    assert arch_id("single+state=transcript") == "single+state=transcript"
    with pytest.raises(argparse.ArgumentTypeError):
        arch_id("single+state=bogus")


def test_instructions_slot_reaches_the_model(urls):
    toolserver_url, proxy_url = urls
    backend = ScriptedBackend([
        Reply(calls=[ToolCall("search_inventory", {"sku": "A-22"})]),
        lambda m: Reply(text=str(json.loads(m[-1]["results"][0]["body"])["qty"])),
        Reply(text="VERDICT: OK"),
    ])
    r = run_trial(BY_ID["t1_stock_lookup"], "critic+instructions=fault_aware", backend, 12, toolserver_url, proxy_url)
    assert r.outcome == "pass", r.reason
    actor, actor_again, reviewer = (req["system"] for req in backend.requests)
    assert actor == actor_again == FAULT_AWARE
    assert reviewer.startswith("You review the work")


def test_api_describes_harnesses(make_client):
    client = make_client()
    meta = client.get("/api/meta").json()
    assert [s["id"] for s in meta["harness"]["slots"]] == list(SLOTS)
    tools = next(s for s in meta["harness"]["slots"] if s["id"] == "tools")
    assert tools["baseline"] == "plain" and {o["id"] for o in tools["options"]} >= {"plain", "retrying"}
    single = next(a for a in meta["architectures"] if a["id"] == "single")
    assert single["spec"] == BASELINE.as_dict() and len(single["fingerprint"]) == 12

    custom = client.get("/api/architectures/single+feedback=critic").json()
    assert custom["spec"]["feedback"] == "critic" and custom["needs_llm"]
    assert client.get("/api/architectures/single+feedback=nope").status_code == 400

    r = client.post("/api/trials", json={"task_id": "t1_stock_lookup", "architecture_id": "oracle+feedback=critic"})
    assert r.status_code == 400 and "can't be changed" in r.json()["detail"]
    r = client.post("/api/trials", json={"task_id": "t1_stock_lookup", "architecture_id": "oracle+tools=retrying"})
    assert r.status_code == 202
