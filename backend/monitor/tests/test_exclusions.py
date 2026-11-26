from django.test import SimpleTestCase

from monitor import exclusions

from .helpers import make_aircraft


class ExclusionsTests(SimpleTestCase):
    def test_valid_identifiers(self):
        for value in ["E0B390", "e0b390", "~abc123", "PR-ABC", "PH-ABC", "LV-KNP", "N12345", "pp abc"]:
            self.assertTrue(exclusions.is_valid(value), value)
        for value in ["", "-ABC", "PR_ABC", "ÑANDÚ", "MUY-LARGA-DEMAS"]:
            self.assertFalse(exclusions.is_valid(value), value)

    def test_match_ignores_case_spaces_and_hyphens(self):
        keys = exclusions.excluded_keys(["e0b390", "PH ABC"])
        self.assertTrue(exclusions.is_excluded(make_aircraft("E0B390", registration=None), keys))
        self.assertTrue(exclusions.is_excluded(make_aircraft("484abc", registration="PH-ABC"), keys))
        self.assertFalse(exclusions.is_excluded(make_aircraft("484abd", registration="PH-ABD"), keys))
        self.assertFalse(exclusions.is_excluded(make_aircraft("e0b390"), frozenset()))

    def test_parse_list_from_env(self):
        self.assertEqual(exclusions.parse_list(" e0b390, pr-abc;PH-XYZ\n"), ["E0B390", "PR-ABC", "PH-XYZ"])
        self.assertEqual(exclusions.parse_list(""), [])
