"""
tools.py

The three required FitFindr tools. Each tool is a standalone function that
can be called and tested independently before being wired into the agent loop.

Tools:
    search_listings(description, size, max_price)  → list[dict]
    suggest_outfit(new_item, wardrobe)              → str
    create_fit_card(outfit, new_item)               → str
"""

import os
import re

from dotenv import load_dotenv
from groq import Groq

from utils.data_loader import load_listings

load_dotenv()

MODEL = "llama-3.3-70b-versatile"


# ── Groq client ───────────────────────────────────────────────────────────────

def _get_groq_client() -> Groq:
    """Initialize and return a Groq client using GROQ_API_KEY from .env."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError(
            "GROQ_API_KEY not set. Add it to a .env file in the project root."
        )
    return Groq(api_key=api_key)


# ── Tool 1: search_listings ───────────────────────────────────────────────────

def search_listings(
    description: str,
    size: str | None = None,
    max_price: float | None = None,
) -> list[dict]:
    """
    Search the mock listings dataset for items matching the description,
    optional size, and optional price ceiling.

    Args:
        description: Keywords describing what the user is looking for
                     (e.g., "vintage graphic tee").
        size:        Size string to filter by, or None to skip size filtering.
                     Matching is case-insensitive (e.g., "M" matches "S/M").
        max_price:   Maximum price (inclusive), or None to skip price filtering.

    Returns:
        A list of matching listing dicts, sorted by relevance (best match first).
        Returns an empty list if nothing matches — does NOT raise an exception.
    """
    try:
        listings = load_listings()
    except Exception:
        return []

    # Step 1: hard filters — price and size
    candidates = []
    for item in listings:
        # Price filter
        if max_price is not None and item["price"] > max_price:
            continue
        # Size filter — case-insensitive substring match so "M" hits "S/M", "M/L"
        if size is not None:
            item_size = item.get("size", "").upper()
            query_size = size.upper().strip()
            if query_size not in item_size:
                continue
        candidates.append(item)

    # Step 2: relevance scoring against description
    # Tokenise description into lowercase words, strip punctuation
    keywords = set(re.sub(r"[^a-z0-9\s]", "", description.lower()).split())
    # Remove very common stop words that add noise
    stop_words = {"a", "an", "the", "for", "in", "on", "at", "to", "of", "and", "or", "is", "im", "looking"}
    keywords -= stop_words

    scored = []
    for item in candidates:
        # Build a bag of words from all searchable fields
        searchable = " ".join([
            item.get("title", ""),
            item.get("description", ""),
            item.get("category", ""),
            item.get("brand", "") or "",
            " ".join(item.get("style_tags", [])),
            " ".join(item.get("colors", [])),
        ]).lower()
        searchable_words = set(re.sub(r"[^a-z0-9\s]", "", searchable).split())

        score = len(keywords & searchable_words)
        if score > 0:
            scored.append((score, item))

    # Step 3: sort by score descending, return just the dicts
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored]


# ── Tool 2: suggest_outfit ────────────────────────────────────────────────────

def suggest_outfit(new_item: dict, wardrobe: dict) -> str:
    """
    Given a thrifted item and the user's wardrobe, suggest 1–2 complete outfits.

    Args:
        new_item: A listing dict (the item the user is considering buying).
        wardrobe: A wardrobe dict with an 'items' key containing a list of
                  wardrobe item dicts. May be empty — handle this gracefully.

    Returns:
        A non-empty string with outfit suggestions. If the wardrobe is empty,
        returns general styling advice. Returns an error string (not an
        exception) if the LLM call fails.
    """
    try:
        client = _get_groq_client()
    except ValueError as e:
        return f"Outfit suggestion unavailable: {e}"

    # Format the new item details
    item_desc = (
        f"'{new_item.get('title', 'Unknown item')}' "
        f"(${new_item.get('price', '?')}, {new_item.get('condition', 'unknown condition')}, "
        f"from {new_item.get('platform', 'unknown platform')}). "
        f"Style tags: {', '.join(new_item.get('style_tags', []))}. "
        f"Colors: {', '.join(new_item.get('colors', []))}."
    )

    wardrobe_items = wardrobe.get("items", [])
    empty_wardrobe = len(wardrobe_items) == 0

    if empty_wardrobe:
        user_content = (
            f"I'm thinking of buying this secondhand item: {item_desc}\n\n"
            "I don't have my wardrobe entered yet. Give me 1–2 general outfit ideas "
            "for this piece — suggest the types of items that would pair well with it "
            "(e.g. wide-leg jeans, chunky sneakers, a cropped jacket). "
            "Be specific about styling details like tucking, layering, and proportions. "
            "Keep it casual and conversational, like a friend giving advice."
        )
    else:
        wardrobe_desc = "\n".join(
            f"- {w.get('name', '?')} "
            f"({', '.join(w.get('colors', []))} | "
            f"tags: {', '.join(w.get('style_tags', []))})"
            for w in wardrobe_items
        )
        user_content = (
            f"I'm thinking of buying this secondhand item: {item_desc}\n\n"
            f"Here's what's already in my wardrobe:\n{wardrobe_desc}\n\n"
            "Suggest 1–2 complete outfit combinations using the new item and "
            "specific pieces from my wardrobe. Name the exact wardrobe pieces. "
            "Include styling details like tucking, layering, and footwear. "
            "Keep it casual and conversational, like a friend giving advice."
        )

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a knowledgeable, friendly personal stylist specialising "
                        "in thrifted and secondhand fashion. Give specific, practical outfit "
                        "advice. Be concise — 3–5 sentences per suggestion. No bullet points, "
                        "just natural conversational prose."
                    ),
                },
                {"role": "user", "content": user_content},
            ],
            max_tokens=400,
            temperature=0.7,
        )
        result = response.choices[0].message.content.strip()
        return result if result else "Outfit suggestion unavailable right now. Try again in a moment."
    except Exception as e:
        return f"Outfit suggestion unavailable right now. Try again in a moment. (Error: {e})"


# ── Tool 3: create_fit_card ───────────────────────────────────────────────────

def create_fit_card(outfit: str, new_item: dict) -> str:
    """
    Generate a short, shareable outfit caption for the thrifted find.

    Args:
        outfit:   The outfit suggestion string from suggest_outfit().
        new_item: The listing dict for the thrifted item.

    Returns:
        A 2–4 sentence string usable as an Instagram/TikTok caption.
        Returns a descriptive error string (not an exception) if outfit is
        empty or the LLM call fails.
    """
    # Guard: empty outfit string
    if not outfit or not outfit.strip():
        return (
            "Can't generate a fit card without an outfit suggestion. "
            "Please try the full flow again."
        )

    try:
        client = _get_groq_client()
    except ValueError as e:
        return f"Fit card unavailable: {e}"

    title = new_item.get("title", "this thrifted find")
    price = new_item.get("price", "?")
    platform = new_item.get("platform", "a thrift app")
    style_tags = new_item.get("style_tags", [])
    vibe = ", ".join(style_tags[:3]) if style_tags else "vintage"

    prompt = (
        f"I thrifted this item: '{title}' for ${price} off {platform}.\n"
        f"Vibe / style tags: {vibe}.\n"
        f"Here's how I'm styling it: {outfit}\n\n"
        "Write a 2–3 sentence Instagram caption for this outfit. "
        "Rules:\n"
        "- Casual, authentic tone — like a real person posting an OOTD, NOT a brand\n"
        "- Mention the item name, price, and platform naturally (each once)\n"
        "- Capture the specific vibe of the outfit\n"
        "- No hashtags\n"
        "- Don't start with 'I'\n"
        "- Make it feel fresh and specific, not generic"
    )

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You write short, authentic social media captions for thrifted outfit posts. "
                        "Your tone is casual and real — not corporate, not cringe. "
                        "Each caption should feel unique and specific to the actual outfit."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=150,
            temperature=0.95,  # High temp = varied output across repeated calls
        )
        result = response.choices[0].message.content.strip()
        return result if result else "Fit card unavailable right now."
    except Exception as e:
        return f"Fit card unavailable right now. (Error: {e})"
