import unittest

from research_required_checks import apply_research_gate


class ResearchRejectionGateTests(unittest.TestCase):
    def test_rejection_survives_missing_evidence(self):
        summary = {"archive_status": "rejected", "decision": "Excluded", "decision_reason": "Food contact outside scope"}
        checks = {"launch_clearance": False, "checks": {"compliance": {"review_status": "pending"}}}
        result = apply_research_gate(summary, checks, {"profitability_verified": False})
        self.assertEqual(result["decision"], summary["decision"])
        self.assertEqual(result["decision_reason"], summary["decision_reason"])
        self.assertEqual(result["required_checks"], checks)
        self.assertNotIn("required_checks", summary)


if __name__ == "__main__":
    unittest.main()
