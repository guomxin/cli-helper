import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from bscli.adapters.seeyon_write import (
    append_oa_write_audit,
    build_oa_write_plan,
)


class SeeyonWriteTests(unittest.TestCase):
    def test_build_write_plan_includes_non_sent_payload_preview(self):
        plan = build_oa_write_plan(
            affair_id="affair-1",
            action="ContinueSubmit",
            opinion="approve",
            mode="dry-run",
            source_url="http://oa.example.test/detail?a=1",
        )

        self.assertEqual(plan["request"]["status"], "not_sent")
        self.assertIsNone(plan["request"]["method"])
        self.assertIsNone(plan["request"]["url"])
        self.assertEqual(
            plan["request"]["payload_preview"],
            {
                "affairId": "affair-1",
                "actionCode": "ContinueSubmit",
                "opinionText": "approve",
                "sourceUrl": "http://oa.example.test/detail?a=1",
                "dryRunOnly": True,
            },
        )
        self.assertEqual(
            plan["request"]["payload_fields"],
            [
                {"name": "affairId", "value_present": True},
                {"name": "actionCode", "value_present": True},
                {"name": "opinionText", "value_present": True, "length": 7},
                {"name": "sourceUrl", "value_present": True},
            ],
        )

    def test_write_audit_redacts_payload_preview_opinion_text(self):
        plan = build_oa_write_plan(
            affair_id="affair-1",
            action="ContinueSubmit",
            opinion="approve",
            mode="dry-run",
        )

        with TemporaryDirectory() as tmp:
            audit_path = append_oa_write_audit(Path(tmp), plan)
            rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(len(rows), 1)
        self.assertNotIn("approve", json.dumps(rows[0], ensure_ascii=False))
        self.assertIsNone(rows[0]["request"]["body"])
        self.assertEqual(rows[0]["request"]["payload_preview"]["opinionText"], None)
        self.assertEqual(rows[0]["request"]["payload_fields"][2]["length"], 7)


if __name__ == "__main__":
    unittest.main()
