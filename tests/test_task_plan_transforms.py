import unittest

from bscli.core.transforms import (
    WORK_ITEMS_TO_LOG_DRAFT,
    TransformRejected,
    build_transform_registry,
)


class TaskPlanTransformTests(unittest.TestCase):
    def setUp(self):
        self.registry = build_transform_registry()

    def test_work_items_are_deduplicated_and_automatic_items_are_excluded(self):
        result = self.registry.invoke(
            WORK_ITEMS_TO_LOG_DRAFT,
            {
                "items": [
                    {
                        "affair_id": "1",
                        "title": "出差申请",
                        "date": "2026-08-30 09:00",
                        "category": "审批",
                    },
                    {
                        "affair_id": "1",
                        "title": "出差申请",
                        "date": "2026-08-30 09:00",
                        "category": "审批",
                    },
                    {
                        "affair_id": "2",
                        "title": "（自动发起）周报发送流程",
                        "date": "2026-08-30 10:00",
                        "category": "通知",
                    },
                ]
            },
        )

        self.assertFalse(result["empty"])
        self.assertEqual(result["source_count"], 3)
        self.assertEqual(result["included_count"], 1)
        self.assertEqual(result["excluded_duplicate_count"], 1)
        self.assertEqual(result["excluded_automatic_count"], 1)
        self.assertIn("出差申请", result["draft"])
        self.assertNotIn("周报发送流程", result["draft"])

    def test_empty_source_produces_explicit_empty_result(self):
        result = self.registry.invoke(WORK_ITEMS_TO_LOG_DRAFT, {"items": []})

        self.assertTrue(result["empty"])
        self.assertEqual(result["draft"], "")
        self.assertEqual(result["included_count"], 0)

    def test_input_limit_is_enforced(self):
        with self.assertRaises(TransformRejected) as raised:
            self.registry.invoke(
                WORK_ITEMS_TO_LOG_DRAFT,
                {"items": [{"title": str(index)} for index in range(101)]},
            )

        self.assertEqual(raised.exception.code, "TRANSFORM_INPUT_TOO_LARGE")


if __name__ == "__main__":
    unittest.main()
