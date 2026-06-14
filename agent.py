"""
agent.py

The FitFindr planning loop. Orchestrates the three tools in response to a
natural language user query, passing state between them via a session dict.
"""

import re

from tools import search_listings, suggest_outfit, create_fit_card


# ── query parser ──────────────────────────────────────────────────────────────

def _parse_query(query: str) -> dict:
    """
    Extract description, size, and max_price from a natural language query
    using regex — no LLM call needed for this step.

    Strategy:
      - max_price: look for "$<number>" or "under <number>"
      - size:      look for "size <token>" or a standalone known size token
      - description: what's left after stripping the price/size fragments

    Returns a dict with keys: description (str), size (str|None), max_price (float|None)
    """
    text = query.strip()

    # ── extract max_price ─────────────────────────────────────────────────────
    max_price = None
    price_match = re.search(r"(?:under|below|max|<)?\s*\$?\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    # More specific: require "under/below $N" or "$N" explicitly
    price_pattern = re.search(
        r"(?:under|below|max|less\s+than)\s+\$?(\d+(?:\.\d+)?)"
        r"|\$(\d+(?:\.\d+)?)",
        text, re.IGNORECASE
    )
    if price_pattern:
        raw = price_pattern.group(1) or price_pattern.group(2)
        max_price = float(raw)

    # ── extract size ──────────────────────────────────────────────────────────
    size = None
    # "size M", "size 8", "size US8", etc.
    size_match = re.search(r"\bsize\s+([A-Za-z0-9/]+)\b", text, re.IGNORECASE)
    if size_match:
        size = size_match.group(1).upper()
    else:
        # Standalone size tokens at word boundary
        standalone = re.search(
            r"\b(XS|S/M|M/L|L/XL|XL|XXL|XS|S\b|M\b|L\b)\b",
            text, re.IGNORECASE
        )
        if standalone:
            size = standalone.group(1).upper()

    # ── build description: strip price/size phrases, clean up ─────────────────
    desc = text
    # Remove price phrases
    desc = re.sub(
        r"(?:under|below|max|less\s+than)\s+\$?\d+(?:\.\d+)?|\$\d+(?:\.\d+)?",
        "", desc, flags=re.IGNORECASE
    )
    # Remove size phrases
    desc = re.sub(r"\bsize\s+[A-Za-z0-9/]+\b", "", desc, flags=re.IGNORECASE)
    # Remove filler phrases
    filler = r"\b(looking for|i'm looking for|i want|i need|find me|searching for|in a|in)\b"
    desc = re.sub(filler, "", desc, flags=re.IGNORECASE)
    # Collapse whitespace and strip punctuation at edges
    desc = re.sub(r"\s+", " ", desc).strip(" ,.-")

    return {
        "description": desc if desc else query,
        "size": size,
        "max_price": max_price,
    }


# ── session state ─────────────────────────────────────────────────────────────

def _new_session(query: str, wardrobe: dict) -> dict:
    return {
        "query": query,
        "parsed": {},
        "search_results": [],
        "selected_item": None,
        "wardrobe": wardrobe,
        "outfit_suggestion": None,
        "fit_card": None,
        "error": None,
    }


# ── planning loop ─────────────────────────────────────────────────────────────

def run_agent(query: str, wardrobe: dict) -> dict:
    """
    Main agent entry point. Runs the FitFindr planning loop and returns the
    completed session dict.

    Planning loop (matches planning.md Architecture section):

      1. Parse query → description, size, max_price
      2. search_listings()
            results == [] → set error, RETURN EARLY (suggest_outfit never called)
            results non-empty → selected_item = results[0], continue
      3. suggest_outfit(selected_item, wardrobe)
            error string returned → set error, RETURN EARLY
            success → outfit_suggestion = result, continue
      4. create_fit_card(outfit_suggestion, selected_item)
            error string → set error (fit_card stays None)
            success → fit_card = result
      5. Return session
    """
    # ── Step 1: initialise session ────────────────────────────────────────────
    session = _new_session(query, wardrobe)

    # ── Step 2: parse query ───────────────────────────────────────────────────
    parsed = _parse_query(query)
    session["parsed"] = parsed

    # ── Step 3: search listings ───────────────────────────────────────────────
    results = search_listings(
        description=parsed["description"],
        size=parsed["size"],
        max_price=parsed["max_price"],
    )
    session["search_results"] = results

    if not results:
        # Build a specific, actionable error message
        parts = []
        if parsed["size"]:
            parts.append(f"size {parsed['size']}")
        if parsed["max_price"] is not None:
            parts.append(f"under ${parsed['max_price']:.0f}")
        filter_desc = " and ".join(parts)
        hint = f" ({filter_desc})" if filter_desc else ""

        session["error"] = (
            f"No listings matched \"{parsed['description']}\"{hint}. "
            "Try a broader description, a higher price ceiling, or removing the size filter."
        )
        return session   # ← EARLY RETURN — suggest_outfit never called

    # ── Step 4: select top result ─────────────────────────────────────────────
    session["selected_item"] = results[0]

    # ── Step 5: suggest outfit ────────────────────────────────────────────────
    outfit = suggest_outfit(
        new_item=session["selected_item"],
        wardrobe=session["wardrobe"],
    )
    session["outfit_suggestion"] = outfit

    # Detect error string returned by suggest_outfit
    if outfit.lower().startswith("outfit suggestion unavailable"):
        session["error"] = outfit
        return session   # ← EARLY RETURN — create_fit_card never called

    # ── Step 6: create fit card ───────────────────────────────────────────────
    fit_card = create_fit_card(
        outfit=session["outfit_suggestion"],
        new_item=session["selected_item"],
    )
    session["fit_card"] = fit_card

    # Detect error string returned by create_fit_card
    if fit_card.lower().startswith(("fit card unavailable", "can't generate")):
        session["error"] = fit_card
        session["fit_card"] = None

    # ── Step 7: return session ────────────────────────────────────────────────
    return session


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from utils.data_loader import get_example_wardrobe, get_empty_wardrobe

    print("=== Happy path: graphic tee ===\n")
    session = run_agent(
        query="looking for a vintage graphic tee under $30",
        wardrobe=get_example_wardrobe(),
    )
    if session["error"]:
        print(f"Error: {session['error']}")
    else:
        print(f"Found:        {session['selected_item']['title']} — ${session['selected_item']['price']}")
        print(f"Parsed:       {session['parsed']}")
        print(f"\nOutfit:       {session['outfit_suggestion']}")
        print(f"\nFit card:     {session['fit_card']}")

    print("\n\n=== No-results path ===\n")
    session2 = run_agent(
        query="designer ballgown size XXS under $5",
        wardrobe=get_example_wardrobe(),
    )
    print(f"Error:            {session2['error']}")
    print(f"selected_item:    {session2['selected_item']}   ← should be None")
    print(f"outfit_suggestion:{session2['outfit_suggestion']}   ← should be None")
    print(f"fit_card:         {session2['fit_card']}   ← should be None")
