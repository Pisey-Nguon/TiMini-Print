from __future__ import annotations

import unittest

from timiniprint.devices.profiles import DetectionRule
from timiniprint.protocol.family import ProtocolFamily


class DetectionRuleTests(unittest.TestCase):
    def test_mac_suffix_rule_does_not_match_uuid_address(self) -> None:
        rule = DetectionRule(
            rule_key="mx05_mac59",
            prefixes=("MX05",),
            exact_names=(),
            profile_key="mx05",
            protocol_family=ProtocolFamily.V5X,
            mac_suffixes=("59",),
        )
        self.assertFalse(rule.matches("MX05-ABCD", "F4B3C8E3-C284-9C3A-C549-D786345CB553"))

    def test_mac_suffix_rule_matches_mac_address_suffix(self) -> None:
        rule = DetectionRule(
            rule_key="mx05_mac59",
            prefixes=("MX05",),
            exact_names=(),
            profile_key="mx05",
            protocol_family=ProtocolFamily.V5X,
            mac_suffixes=("59",),
        )
        self.assertTrue(rule.matches("MX05-ABCD", "AA:BB:CC:DD:EE:59"))
        self.assertTrue(rule.matches("MX05-ABCD", "AA-BB-CC-DD-EE-59"))

    def test_exact_name_rule_matches_only_exact_name(self) -> None:
        rule = DetectionRule(
            rule_key="x6_exact",
            prefixes=(),
            exact_names=("X6",),
            profile_key="v5g_small_203",
            protocol_family=ProtocolFamily.V5G,
        )
        self.assertTrue(rule.matches("X6", None))
        self.assertFalse(rule.matches("X6H-1234", None))


if __name__ == "__main__":
    unittest.main()
