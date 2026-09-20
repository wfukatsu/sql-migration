"""The skills must stay loadable outside Claude Code (Codex reads .agents/skills and only `name` / `description`)."""
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SKILLS = sorted(p.parent for p in (ROOT / "skills").glob("*/SKILL.md"))


def frontmatter(skill: Path) -> dict:
    text = (skill / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{skill.name}: SKILL.md does not start with frontmatter"
    return yaml.safe_load(text[4:].split("\n---\n", 1)[0])


def test_agents_skills_points_at_the_skills_directory():
    link = ROOT / ".agents" / "skills"
    assert link.is_symlink(), ".agents/skills must be a symlink, not a copy (one source of truth)"
    assert link.resolve() == (ROOT / "skills").resolve()


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.name)
def test_name_and_description_fit_the_common_skill_format(skill):
    meta = frontmatter(skill)
    assert meta["name"] == skill.name
    assert len(meta["name"]) <= 64
    assert len(meta["description"]) <= 1024, f"{skill.name}: description is {len(meta['description'])} characters"


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.name)
def test_description_carries_the_trigger_and_the_exclusions(skill):
    # when_to_use is read by Claude Code only; a reader of `description` alone must still be able to route
    description = frontmatter(skill)["description"]
    assert "使うとき:" in description, f"{skill.name}: description does not say when to use the skill"
    assert "対象外:" in description, f"{skill.name}: description does not say what is out of scope"


# ---- marketplace (Claude Code: .claude-plugin/, Codex: .codex-plugin/ + .agents/plugins/) ----

def _json(path: str) -> dict:
    import json
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_both_plugin_manifests_describe_the_same_plugin():
    claude, codex = _json(".claude-plugin/plugin.json"), _json(".codex-plugin/plugin.json")
    for key in ("name", "version", "description", "license", "repository"):
        assert claude[key] == codex[key], f"plugin.json {key} differs between Claude Code and Codex"


def test_marketplaces_offer_the_repository_root_as_the_plugin():
    # the skills need plsql/, scalardb_migrate/, runtime-java/ and requirements.txt next to them, and a plugin
    # cannot reach outside its own directory -- so the plugin is the repository root, not skills/
    name = _json(".claude-plugin/plugin.json")["name"]
    claude = _json(".claude-plugin/marketplace.json")["plugins"]
    codex = _json(".agents/plugins/marketplace.json")["plugins"]
    assert [(p["name"], p["source"]) for p in claude] == [(name, "./")]
    assert [(p["name"], p["source"]) for p in codex] == [(name, {"source": "local", "path": "./"})]


def test_launcher_is_executable_and_every_skill_says_how_to_run_outside_the_checkout():
    import os
    assert os.access(ROOT / "bin" / "python", os.X_OK)
    for skill in SKILLS:
        text = (skill / "SKILL.md").read_text(encoding="utf-8")
        assert "<root>/bin/python" in text, f"{skill.name}: no instructions for running as an installed plugin"
