import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.coordinator.skill_docs import AgentSkillDocument, AgentSkillMetadata
from app.coordinator.skill_loader import (
    filter_skills_by_registered_tools,
    format_agent_skills_for_prompt,
    load_agent_skills,
)


class TestAgentSkillsLoader(unittest.TestCase):
    def test_load_agent_skills_parses_frontmatter_and_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            skills_root = Path(tmp)
            skill_dir = skills_root / "roon-search"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                (
                    "---\n"
                    "name: roon-search\n"
                    "description: Browse the Roon library.\n"
                    "metadata:\n"
                    "  owner: swarpius\n"
                    "---\n\n"
                    "## Instructions\n\n"
                    "Use this skill for browsing.\n"
                ),
                encoding="utf-8",
            )

            docs = load_agent_skills(skills_root)
            self.assertEqual(len(docs), 1)
            self.assertEqual(docs[0].metadata.name, "roon-search")
            self.assertIn("Instructions", docs[0].body)

    def test_load_agent_skills_rejects_invalid_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            skills_root = Path(tmp)
            skill_dir = skills_root / "roon-search"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                (
                    "---\n"
                    "name: roon_search\n"
                    "description: Invalid due to underscore.\n"
                    "---\n\n"
                    "Body.\n"
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                load_agent_skills(skills_root)

    def test_load_agent_skills_rejects_xml_tags_in_description(self):
        with tempfile.TemporaryDirectory() as tmp:
            skills_root = Path(tmp)
            skill_dir = skills_root / "roon-search"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                (
                    "---\n"
                    "name: roon-search\n"
                    "description: <b>Browse</b> the Roon library.\n"
                    "---\n\n"
                    "Body.\n"
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                load_agent_skills(skills_root)

    def test_format_agent_skills_for_prompt_includes_name_description_and_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            skills_root = Path(tmp)
            skill_dir = skills_root / "roon-search"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                (
                    "---\n"
                    "name: roon-search\n"
                    "description: Browse the Roon library.\n"
                    "---\n\n"
                    "Body content here.\n"
                ),
                encoding="utf-8",
            )

            docs = load_agent_skills(skills_root)
            rendered, key_rules = format_agent_skills_for_prompt(docs)
            self.assertIn("<available_skills>", rendered)
            self.assertIn("<name>roon_search</name>", rendered)
            self.assertIn("<description>Browse the Roon library.</description>", rendered)
            self.assertIn("<instructions>", rendered)
            self.assertIn("Body content here.", rendered)
            self.assertEqual(key_rules, "")

    def test_skill_with_requires_env_is_skipped_when_var_unset(self):
        """Skills declaring requires_env: FOO should be skipped when FOO
        is unset or empty. Lets optional capabilities (web search, live
        radio later, etc.) declare their env dependency in the SKILL.md
        rather than being filtered by the caller."""
        with tempfile.TemporaryDirectory() as tmp:
            skills_root = Path(tmp)
            (skills_root / "always-on").mkdir()
            (skills_root / "always-on" / "SKILL.md").write_text(
                "---\nname: always-on\ndescription: Always loads.\n---\n\nbody\n",
                encoding="utf-8",
            )
            (skills_root / "gated").mkdir()
            (skills_root / "gated" / "SKILL.md").write_text(
                "---\nname: gated\ndescription: Needs env.\n"
                "requires_env: SOME_FEATURE_URL\n---\n\nbody\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("SOME_FEATURE_URL", None)
                docs = load_agent_skills(skills_root)

            names = {d.metadata.name for d in docs}
            self.assertEqual(names, {"always-on"})

    def test_skill_with_requires_env_loads_when_var_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            skills_root = Path(tmp)
            (skills_root / "gated").mkdir()
            (skills_root / "gated" / "SKILL.md").write_text(
                "---\nname: gated\ndescription: Needs env.\n"
                "requires_env: SOME_FEATURE_URL\n---\n\nbody\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"SOME_FEATURE_URL": "http://x"}):
                docs = load_agent_skills(skills_root)

            self.assertEqual(len(docs), 1)
            self.assertEqual(docs[0].metadata.name, "gated")
            self.assertEqual(docs[0].metadata.requires_env, "SOME_FEATURE_URL")

    def test_skill_with_requires_tool_is_parsed_but_not_filtered_at_load(self):
        """`requires_tool` is parsed into metadata but the skill loader
        does NOT filter on it — at load time we don't yet know which
        tools will end up registered. Filtering happens later, after
        tool registration in RuntimeState."""
        with tempfile.TemporaryDirectory() as tmp:
            skills_root = Path(tmp)
            (skills_root / "tool-gated").mkdir()
            (skills_root / "tool-gated" / "SKILL.md").write_text(
                "---\nname: tool-gated\ndescription: Needs a tool.\n"
                "requires_tool: web_search\n---\n\nbody\n",
                encoding="utf-8",
            )

            docs = load_agent_skills(skills_root)

            self.assertEqual(len(docs), 1)
            self.assertEqual(docs[0].metadata.name, "tool-gated")
            self.assertEqual(docs[0].metadata.requires_tool, "web_search")


    def test_repository_skills_structure_uses_folder_per_skill(self):
        """Repo-layout invariant check — not a unit test of the skill
        loader, but a guard against the skills/ directory drifting out
        of the folder-per-skill-with-SKILL.md convention. Skips cleanly
        for forks that have restructured or removed skills/."""
        repo_skills_root = Path(__file__).resolve().parent.parent / "skills"
        if not repo_skills_root.exists():
            self.skipTest("No skills/ directory at the expected path")

        direct_json = sorted(
            path.name for path in repo_skills_root.glob("*.json") if path.is_file()
        )
        self.assertEqual(
            direct_json,
            [],
            f"Remove legacy JSON skill files from skills root: {direct_json}",
        )

        skill_dirs = sorted(
            path
            for path in repo_skills_root.iterdir()
            if path.is_dir() and not path.name.startswith("__")
        )
        self.assertGreater(len(skill_dirs), 0, "skills directory should contain skill folders")

        missing_skill_md = sorted(
            path.name for path in skill_dirs if not (path / "SKILL.md").is_file()
        )
        self.assertEqual(
            missing_skill_md,
            [],
            f"Each skill folder must contain SKILL.md: {missing_skill_md}",
        )

        dir_names = {path.name for path in skill_dirs}
        duplicate_forms = sorted(
            name
            for name in dir_names
            if "_" in name and name.replace("_", "-") in dir_names
        )
        self.assertEqual(
            duplicate_forms,
            [],
            f"Do not keep underscore/hyphen duplicate skill folders: {duplicate_forms}",
        )


class TestFilterSkillsByRegisteredTools(unittest.TestCase):
    """`requires_tool` gating: skills with a tool declared but not
    registered get dropped — env-var presence is not enough, since the
    web_search factory may decline to build a tool even when
    credentials are set (WEB_SEARCH_PROVIDER mismatch, unknown value,
    explicit `none`)."""

    def _make_skill(self, name: str, requires_tool: str | None) -> AgentSkillDocument:
        return AgentSkillDocument(
            metadata=AgentSkillMetadata(
                name=name, description=f"{name} skill",
                location=f"/fake/{name}/SKILL.md",
                requires_tool=requires_tool,
            ),
            body="body",
        )

    def test_skill_with_no_requires_tool_always_kept(self):
        skill = self._make_skill("always-on", None)
        kept = filter_skills_by_registered_tools([skill], set())
        self.assertEqual(len(kept), 1)

    def test_skill_kept_when_required_tool_registered(self):
        skill = self._make_skill("web-search", "web_search")
        kept = filter_skills_by_registered_tools(
            [skill], {"roon_search", "web_search"},
        )
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].metadata.name, "web-search")

    def test_skill_dropped_when_required_tool_missing(self):
        skill = self._make_skill("web-search", "web_search")
        kept = filter_skills_by_registered_tools([skill], {"roon_search"})
        self.assertEqual(kept, [])

    def test_filter_preserves_order(self):
        skills = [
            self._make_skill("a", None),
            self._make_skill("b", "missing"),
            self._make_skill("c", "present"),
            self._make_skill("d", None),
        ]
        kept = filter_skills_by_registered_tools(skills, {"present"})
        self.assertEqual([s.metadata.name for s in kept], ["a", "c", "d"])


if __name__ == "__main__":
    unittest.main()
