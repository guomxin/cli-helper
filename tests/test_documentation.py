from pathlib import Path
import re
import unittest
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


class DocumentationTests(unittest.TestCase):
    def test_relative_markdown_links_resolve(self):
        paths = [
            *ROOT.glob("*.md"),
            *(ROOT / "docs").rglob("*.md"),
            ROOT / "integrations" / "openclaw-agentbridge" / "README.md",
        ]
        for path in paths:
            text = path.read_text(encoding="utf-8")
            for raw_target in MARKDOWN_LINK.findall(text):
                target = raw_target.strip().strip("<>")
                parsed = urlparse(target)
                if parsed.scheme or target.startswith("#"):
                    continue
                relative = unquote(target.split("#", 1)[0])
                if not relative:
                    continue
                resolved = (path.parent / relative).resolve()
                with self.subTest(document=path.relative_to(ROOT), target=target):
                    self.assertTrue(resolved.exists(), f"missing documentation link: {resolved}")

    def test_current_documentation_has_one_entry_point(self):
        index = ROOT / "docs" / "README.md"
        text = index.read_text(encoding="utf-8")
        for expected in (
            "./project-status.md",
            "./architecture/governed-write-model.md",
            "./operations/current-deployment-plan.md",
            "./operations/development-and-release-workflow.md",
            "./systems/README.md",
            "./acceptance/README.md",
            "./roadmap/deferred-considerations.md",
            "./archive/README.md",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, text)

    def test_current_documentation_uses_category_directories(self):
        self.assertEqual(
            {path.name for path in ROOT.glob("*.md")},
            {"README.md"},
        )
        self.assertEqual(
            {path.name for path in (ROOT / "docs").glob("*.md")},
            {"README.md", "project-status.md"},
        )
        for category in (
            "architecture",
            "platform",
            "operations",
            "systems",
            "acceptance",
            "roadmap",
            "archive",
        ):
            with self.subTest(category=category):
                self.assertTrue((ROOT / "docs" / category).is_dir())

    def test_retired_documents_are_archived(self):
        for retired_path in (
            ROOT / "BSCLI_DESIGN.md",
            ROOT / "BSCLI_DESIGN_ZH.md",
            ROOT / "docs" / "oa-write-safety.md",
            ROOT / "docs" / "oa-write-discovery.md",
        ):
            with self.subTest(path=retired_path.relative_to(ROOT)):
                self.assertFalse(retired_path.exists())

        for archived_path in (
            ROOT / "docs" / "archive" / "bscli-browser-bridge-design.md",
            ROOT / "docs" / "archive" / "bscli-browser-bridge-design-zh.md",
            ROOT / "docs" / "archive" / "oa-write-safety-legacy.md",
            ROOT / "docs" / "archive" / "oa-write-discovery-legacy.md",
            ROOT / "docs" / "archive" / "legacy-bridge-retirement.md",
            ROOT / "docs" / "archive" / "current-deployment-plan-history.md",
        ):
            with self.subTest(path=archived_path.relative_to(ROOT)):
                self.assertTrue(archived_path.exists())

    def test_current_write_guides_do_not_publish_retired_commands(self):
        for relative_path in (
            "docs/architecture/governed-write-model.md",
            "docs/systems/oa/oa-write-action-expansion-playbook.md",
        ):
            text = (ROOT / relative_path).read_text(encoding="utf-8")
            for forbidden in (
                "oa__",
                "bscli daemon",
                "chrome_extension",
                "browser_bridge_used",
            ):
                with self.subTest(document=relative_path, forbidden=forbidden):
                    self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
