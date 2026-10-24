import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("openrouter_free_canary.py")
spec = importlib.util.spec_from_file_location("openrouter_free_canary", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class CanaryDecoderTests(unittest.TestCase):
    def test_plain_string_content(self):
        self.assertEqual(module.content_to_text('{"entities":[]}'), '{"entities":[]}')

    def test_array_text_blocks(self):
        self.assertEqual(
            module.content_to_text([{"type": "text", "text": '{"entities":'}, {"type": "text", "text": "[]}"}]),
            '{"entities":\n[]}',
        )

    def test_null_content_reports_shape(self):
        with self.assertRaisesRegex(ValueError, "content is null"):
            module.content_to_text(None)

    def test_json_fence(self):
        fence = chr(96) * 3
        self.assertEqual(module.parse_json_object(fence + 'json\n{"entities":[],"relations":[]}\n' + fence)["entities"], [])

    def test_exact_source_quotes(self):
        stats = module.validate_extraction(
            {"entities": [{"name": "Probability", "type": "concept", "source_quote": "Probability forms the foundation"}],
             "relations": [{"source": "Probability", "relation": "forms foundation", "target": "statistics",
                            "source_quote": "Probability forms the foundation"}]},
            "Probability forms the foundation of statistics.",
        )
        self.assertEqual(stats["entities"], 1)
        self.assertEqual(stats["relations"], 1)
        self.assertTrue(stats["all_quotes_exact"])

    def test_reject_non_source_quote(self):
        stats = module.validate_extraction(
            {"entities": [{"name": "Probability", "type": "concept", "source_quote": "made up"}], "relations": []},
            "Probability forms the foundation.",
        )
        self.assertFalse(stats["all_quotes_exact"])

if __name__ == "__main__":
    unittest.main()

