#!/usr/bin/env python3
import unittest

from import_sorftime_candidates import validate_sorftime_response


class SorftimeResponseTest(unittest.TestCase):
    def test_success_response_is_accepted(self) -> None:
        validate_sorftime_response({"Code": 0, "Message": "Success", "Data": []})

    def test_quota_business_error_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "694.*Insufficient request quota"):
            validate_sorftime_response({"Code": 694, "Message": "Insufficient request quota"})

    def test_payload_without_envelope_is_accepted(self) -> None:
        validate_sorftime_response([{"asin": "B000TEST"}])


if __name__ == "__main__":
    unittest.main()
