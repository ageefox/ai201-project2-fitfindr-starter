"""
tests/test_tools.py  — unittest-compatible (also runs with pytest when installed)
"""

import sys
import os
import types
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Stub groq before tools.py is imported (package not available in this env)
fake_groq = types.ModuleType("groq")
fake_groq.Groq = MagicMock()
sys.modules.setdefault("groq", fake_groq)

fake_dotenv = types.ModuleType("dotenv")
fake_dotenv.load_dotenv = lambda: None
sys.modules.setdefault("dotenv", fake_dotenv)

from tools import search_listings, suggest_outfit, create_fit_card


# ── helpers ───────────────────────────────────────────────────────────────────

def _mock_response(text):
    r = MagicMock()
    r.choices[0].message.content = text
    return r

def _sample_item():
    return {
        "id": "lst_033",
        "title": "Vintage Band Tee — Faded Grey",
        "description": "Worn-in band tee with a faded grey wash.",
        "category": "tops",
        "style_tags": ["vintage", "grunge", "band tee", "graphic tee", "streetwear"],
        "size": "L",
        "condition": "good",
        "price": 19.0,
        "colors": ["grey", "black"],
        "brand": None,
        "platform": "depop",
    }

def _example_wardrobe():
    from utils.data_loader import get_example_wardrobe
    return get_example_wardrobe()

def _empty_wardrobe():
    from utils.data_loader import get_empty_wardrobe
    return get_empty_wardrobe()


# ── Tool 1: search_listings ───────────────────────────────────────────────────

class TestSearchListings(unittest.TestCase):

    def test_returns_list(self):
        self.assertIsInstance(search_listings("vintage graphic tee"), list)

    def test_returns_results_for_known_query(self):
        results = search_listings("vintage graphic tee")
        self.assertGreater(len(results), 0)

    def test_empty_results_for_impossible_query(self):
        """Failure mode: no matches -> empty list, not an exception."""
        results = search_listings("designer ballgown", size="XXS", max_price=5)
        self.assertEqual(results, [])

    def test_price_filter_respected(self):
        results = search_listings("jacket", max_price=45)
        self.assertGreater(len(results), 0)
        for item in results:
            self.assertLessEqual(item["price"], 45,
                f"{item['title']} costs ${item['price']} but max was $45")

    def test_price_filter_too_low_returns_empty(self):
        results = search_listings("jacket", max_price=10)
        self.assertEqual(results, [])

    def test_size_filter_case_insensitive(self):
        results = search_listings("top", size="m")
        self.assertGreater(len(results), 0)
        for item in results:
            self.assertIn("M", item["size"].upper(),
                f"Size filter failed: got size '{item['size']}'")

    def test_size_substring_match(self):
        """'S' should match items with size 'S/M'."""
        results = search_listings("mesh top", size="S")
        self.assertGreater(len(results), 0)
        for item in results:
            self.assertIn("S", item["size"].upper())

    def test_results_have_required_fields(self):
        required = {"id", "title", "description", "category", "style_tags",
                    "size", "condition", "price", "colors", "platform"}
        for item in search_listings("vintage"):
            missing = required - item.keys()
            self.assertFalse(missing, f"Missing fields {missing} in {item.get('id')}")

    def test_top_result_most_relevant_for_track_jacket(self):
        results = search_listings("90s track jacket", size="M")
        self.assertGreater(len(results), 0)
        top_title = results[0]["title"].lower()
        self.assertTrue("track" in top_title or "jacket" in top_title,
            f"Expected track/jacket at top, got: {results[0]['title']}")

    def test_empty_description_does_not_raise(self):
        try:
            result = search_listings("")
            self.assertIsInstance(result, list)
        except Exception as e:
            self.fail(f"search_listings('') raised an exception: {e}")


# ── Tool 2: suggest_outfit ────────────────────────────────────────────────────

class TestSuggestOutfit(unittest.TestCase):

    def _call(self, item, wardrobe, reply="Pair it with your baggy jeans and chunky sneakers!"):
        mc = MagicMock()
        mc.chat.completions.create.return_value = _mock_response(reply)
        with patch("tools._get_groq_client", return_value=mc):
            return suggest_outfit(item, wardrobe)

    def test_returns_string(self):
        self.assertIsInstance(self._call(_sample_item(), _example_wardrobe()), str)

    def test_returns_non_empty_string(self):
        result = self._call(_sample_item(), _example_wardrobe())
        self.assertGreater(len(result.strip()), 0)

    def test_empty_wardrobe_returns_non_empty_string(self):
        """Failure mode: empty wardrobe -> general advice, not a crash."""
        result = self._call(
            _sample_item(),
            _empty_wardrobe(),
            reply="This tee pairs well with wide-leg jeans and platform sneakers."
        )
        self.assertIsInstance(result, str)
        self.assertGreater(len(result.strip()), 0)

    def test_llm_error_returns_string_not_exception(self):
        """Failure mode: API error -> error string, not exception."""
        mc = MagicMock()
        mc.chat.completions.create.side_effect = Exception("timeout")
        with patch("tools._get_groq_client", return_value=mc):
            try:
                result = suggest_outfit(_sample_item(), _example_wardrobe())
            except Exception as e:
                self.fail(f"suggest_outfit raised an exception instead of returning a string: {e}")
        self.assertIsInstance(result, str)
        self.assertIn("unavailable", result.lower())

    def test_missing_api_key_returns_string_not_exception(self):
        with patch("tools._get_groq_client", side_effect=ValueError("no key")):
            try:
                result = suggest_outfit(_sample_item(), _example_wardrobe())
            except Exception as e:
                self.fail(f"suggest_outfit raised instead of returning error string: {e}")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result.strip()), 0)

    def test_llm_called_exactly_once(self):
        mc = MagicMock()
        mc.chat.completions.create.return_value = _mock_response("outfit!")
        with patch("tools._get_groq_client", return_value=mc):
            suggest_outfit(_sample_item(), _example_wardrobe())
        mc.chat.completions.create.assert_called_once()


# ── Tool 3: create_fit_card ───────────────────────────────────────────────────

class TestCreateFitCard(unittest.TestCase):

    def _call(self, outfit, item, reply="thrifted this vintage band tee off depop for $19 and it just works 🖤"):
        mc = MagicMock()
        mc.chat.completions.create.return_value = _mock_response(reply)
        with patch("tools._get_groq_client", return_value=mc):
            return create_fit_card(outfit, item)

    def test_returns_string(self):
        self.assertIsInstance(self._call("Pair with baggy jeans.", _sample_item()), str)

    def test_returns_non_empty_string(self):
        result = self._call("Pair with baggy jeans.", _sample_item())
        self.assertGreater(len(result.strip()), 0)

    def test_empty_outfit_returns_error_string_not_exception(self):
        """Failure mode: empty outfit -> error string, not crash."""
        try:
            result = create_fit_card("", _sample_item())
        except Exception as e:
            self.fail(f"create_fit_card raised an exception on empty outfit: {e}")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result.strip()), 0)
        self.assertTrue(
            any(w in result.lower() for w in ["can't", "cannot", "outfit", "generate"]),
            f"Expected meaningful error, got: {result!r}"
        )

    def test_whitespace_outfit_returns_error_string(self):
        """Whitespace-only string should be caught the same as empty."""
        result = create_fit_card("   ", _sample_item())
        self.assertIsInstance(result, str)
        self.assertTrue(
            any(w in result.lower() for w in ["can't", "cannot", "outfit", "generate"])
        )

    def test_llm_error_returns_string_not_exception(self):
        """Failure mode: API error -> error string, not exception."""
        mc = MagicMock()
        mc.chat.completions.create.side_effect = Exception("rate limited")
        with patch("tools._get_groq_client", return_value=mc):
            try:
                result = create_fit_card("Some outfit.", _sample_item())
            except Exception as e:
                self.fail(f"create_fit_card raised instead of returning error string: {e}")
        self.assertIsInstance(result, str)
        self.assertIn("unavailable", result.lower())

    def test_missing_api_key_returns_string_not_exception(self):
        with patch("tools._get_groq_client", side_effect=ValueError("no key")):
            try:
                result = create_fit_card("Some outfit.", _sample_item())
            except Exception as e:
                self.fail(f"create_fit_card raised instead of returning error string: {e}")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result.strip()), 0)

    def test_llm_called_once_with_valid_outfit(self):
        mc = MagicMock()
        mc.chat.completions.create.return_value = _mock_response("caption here")
        with patch("tools._get_groq_client", return_value=mc):
            create_fit_card("Baggy jeans and chunky sneakers.", _sample_item())
        mc.chat.completions.create.assert_called_once()

    def test_llm_not_called_with_empty_outfit(self):
        """Guard should short-circuit before ever touching the LLM."""
        mc = MagicMock()
        with patch("tools._get_groq_client", return_value=mc):
            create_fit_card("", _sample_item())
        mc.chat.completions.create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
