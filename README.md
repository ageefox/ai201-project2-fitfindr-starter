# FitFindr

A multi-tool AI agent that helps users find secondhand clothing and figure out how to wear it. You describe what you're looking for; FitFindr searches a dataset of 40 mock secondhand listings, suggests complete outfit combinations using your existing wardrobe, and generates a shareable social caption for the look.

---

## Setup

```bash
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```
GROQ_API_KEY=your_key_here
```

Get a free key at [console.groq.com](https://console.groq.com) — no credit card required.

Run the app:

```bash
python app.py
```

Then open the URL shown in your terminal (usually `http://localhost:7860`).

---

## Project Structure

```
fitfindr/
├── data/
│   ├── listings.json          # 40 mock secondhand listings
│   └── wardrobe_schema.json   # Wardrobe schema + example wardrobe
├── utils/
│   └── data_loader.py         # load_listings(), get_example_wardrobe(), get_empty_wardrobe()
├── tests/
│   └── test_tools.py          # 24 unit tests (unittest, no API key needed)
├── tools.py                   # The three agent tools
├── agent.py                   # Planning loop + query parser
├── app.py                     # Gradio UI
├── planning.md                # Spec written before implementation
└── requirements.txt
```

---

## Tool Inventory

### `search_listings(description, size, max_price)`

**Purpose:** Searches the mock listings dataset for items matching the user's description, optional size, and optional price ceiling. No LLM involved — pure Python keyword matching.

**Inputs:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `description` | `str` | Natural-language keywords (e.g. `"vintage graphic tee"`) |
| `size` | `str \| None` | Size filter. Case-insensitive substring match — `"M"` matches `"S/M"` and `"M/L"`. `None` skips size filtering. |
| `max_price` | `float \| None` | Maximum price (inclusive). `None` skips price filtering. |

**Output:** `list[dict]` — matching listing dicts sorted by relevance (keyword overlap score), highest first. Each dict contains: `id`, `title`, `description`, `category`, `style_tags`, `size`, `condition`, `price`, `colors`, `brand`, `platform`. Returns `[]` if nothing matches; never raises an exception.

---

### `suggest_outfit(new_item, wardrobe)`

**Purpose:** Given a specific secondhand item and the user's wardrobe, calls the Groq LLM to suggest 1–2 complete outfit combinations using named wardrobe pieces. Handles the empty-wardrobe case by shifting to general styling advice.

**Inputs:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `new_item` | `dict` | A listing dict from `search_listings` |
| `wardrobe` | `dict` | A wardrobe dict with an `"items"` key (list of wardrobe item dicts). May be empty. |

**Output:** `str` — natural-language outfit suggestion (3–5 sentences). Returns a descriptive error string (starting with `"Outfit suggestion unavailable"`) on LLM failure; never raises an exception.

---

### `create_fit_card(outfit, new_item)`

**Purpose:** Calls the Groq LLM to generate a short, casual social media caption for the outfit — the kind of thing someone would write for an Instagram OOTD post. Temperature is set to 0.95 so captions vary meaningfully across calls.

**Inputs:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `outfit` | `str` | The outfit suggestion string from `suggest_outfit` |
| `new_item` | `dict` | The listing dict, used to include item name, price, and platform in the caption |

**Output:** `str` — a 2–3 sentence casual caption. Returns a descriptive error string on empty outfit input or LLM failure; never raises an exception.

---

## How the Planning Loop Works

The loop runs inside `run_agent()` in `agent.py`. It is not a fixed sequence — each step gates the next on success, so the agent's behavior changes based on what was returned.

**Step 1 — Parse the query** using regex (not an LLM). The parser extracts three things from the user's natural language input: a `description` (keywords after stripping price/size/filler phrases), a `size` (from "size M" or standalone size tokens), and a `max_price` (from "under $30" or "$30"). This is deliberate — query parsing is deterministic pattern matching, not language understanding, so using regex avoids an unnecessary LLM call and failure point.

**Step 2 — Call `search_listings`** with the parsed parameters. If the result is an empty list, the loop sets `session["error"]` to a specific, actionable message and **returns immediately**. `suggest_outfit` and `create_fit_card` are never called. The error message includes the parsed search terms and suggests concrete adjustments (broader description, higher price ceiling, remove size filter).

**Step 3 — Select `results[0]`** (top-scored item) and store it in `session["selected_item"]`.

**Step 4 — Call `suggest_outfit`** with the selected item and the user's wardrobe. If the returned string starts with `"Outfit suggestion unavailable"`, the loop sets `session["error"]` and **returns early** again — `create_fit_card` is never called with bad input.

**Step 5 — Call `create_fit_card`** with the outfit suggestion and selected item. If it returns an error string, `session["error"]` is set and `session["fit_card"]` is left as `None`.

**Step 6 — Return the session dict.** The app layer reads `session["error"]`, `session["selected_item"]`, `session["outfit_suggestion"]`, and `session["fit_card"]` to populate the three Gradio output panels.

The key design decision: **the loop never calls a downstream tool with None or empty input.** Each step checks the previous result before proceeding.

---

## State Management

A single `session` dict is created at the start of each `run_agent()` call and threaded through every step. No values are re-entered, re-fetched, or hardcoded between steps.

```python
session = {
    "query": query,              # original user input, never modified
    "parsed": {},                # description, size, max_price extracted by parser
    "search_results": [],        # full list from search_listings
    "selected_item": None,       # results[0] — same dict object passed to suggest_outfit
    "wardrobe": wardrobe,        # user's wardrobe, passed directly to suggest_outfit
    "outfit_suggestion": None,   # string from suggest_outfit, passed to create_fit_card
    "fit_card": None,            # string from create_fit_card
    "error": None,               # set on any early-exit condition
}
```

The state flow is strictly linear and can be traced at any point:

```
search_listings result → session["selected_item"]
                                    ↓
                         suggest_outfit(session["selected_item"], session["wardrobe"])
                                    ↓
                         session["outfit_suggestion"]
                                    ↓
                         create_fit_card(session["outfit_suggestion"], session["selected_item"])
                                    ↓
                         session["fit_card"]
```

`session["selected_item"]` is the same Python dict object that was returned by `search_listings` — it's not copied or re-serialized. This was verified in testing with `assert call_item is session["selected_item"]`.

---

## Error Handling

Each tool owns its own failure mode and returns a usable string in every case. The agent never crashes, and it never calls a downstream tool with bad input.

### `search_listings` — no results

**Failure mode:** Query is too specific (impossible size + price combination) or terms don't match anything in the dataset.

**What the tool does:** Returns `[]` — an empty list, no exception.

**What the agent does:** Detects `results == []`, constructs a specific error message using the parsed search parameters, sets `session["error"]`, and returns the session immediately without calling `suggest_outfit`.

**Example from testing:**

```python
session = run_agent("designer ballgown size XXS under $5", wardrobe)
# session["error"] →
# 'No listings matched "designer ballgown" (size XXS and under $5).
#  Try a broader description, a higher price ceiling, or removing the size filter.'
# session["selected_item"] → None
# suggest_outfit was never called (confirmed by mock assertion)
```

---

### `suggest_outfit` — empty wardrobe

**Failure mode:** The user selects "Empty wardrobe (new user)" or has no items in their wardrobe dict.

**What the tool does:** Detects `wardrobe["items"] == []` before calling the LLM and adds a note to the system prompt instructing the model to give general styling advice instead of wardrobe-specific pairings. Returns a non-empty, useful string.

**Example from testing:**

```python
result = suggest_outfit(results[0], get_empty_wardrobe())
# Returns something like:
# "This band tee works well with wide-leg jeans or baggy cargos for a 90s grunge feel.
#  Try layering it under an open flannel shirt and pairing with chunky platform sneakers
#  or worn-in boots for extra texture."
# — not an exception, not an empty string
```

---

### `suggest_outfit` — LLM API error

**Failure mode:** Network timeout, invalid API key, or rate limit from Groq.

**What the tool does:** Catches the exception and returns `"Outfit suggestion unavailable right now. Try again in a moment."`.

**What the agent does:** Detects that the returned string starts with `"Outfit suggestion unavailable"`, sets `session["error"]`, and returns early without calling `create_fit_card`.

---

### `create_fit_card` — empty outfit string

**Failure mode:** Called with an empty or whitespace-only `outfit` argument (e.g. if `suggest_outfit` had been bypassed somehow).

**What the tool does:** Checks `if not outfit or not outfit.strip()` *before* initializing the Groq client. Returns `"Can't generate a fit card without an outfit suggestion. Please try the full flow again."`. The LLM is never called.

**Example from testing:**

```python
result = create_fit_card("", results[0])
# Returns: "Can't generate a fit card without an outfit suggestion.
#           Please try the full flow again."
# LLM call count: 0 (confirmed by mock assertion)
```

---

## Spec Reflection

**One way the spec helped:** Writing the planning loop in plain conditional logic in `planning.md` before touching `agent.py` made the implementation almost mechanical. The spec said "if `results == []`, set `session["error"]` and return early" — so that's exactly what the code does, one-to-one. When I ran the branch tests and they passed first try, it was because the logic was already worked out on paper.

**One way implementation diverged from the spec:** The spec described query parsing as a step that "can use regex, string splitting, or ask the LLM." I originally planned to use the LLM for parsing to handle messier inputs. In practice, after looking at the actual example queries (`"vintage graphic tee under $30"`, `"90s track jacket in size M"`), regex was clearly sufficient and much faster — adding an LLM call for parsing would have added ~2 seconds and a new failure point for something that's just pattern extraction. The planning.md was updated to document this choice.

---

## AI Usage

### Instance 1 — `search_listings` implementation

I gave Claude the Tool 1 spec block from `planning.md` (inputs, return value, scoring approach, failure mode) and the instruction to use `load_listings()` from the data loader without reimplementing file loading. Claude generated a function that scored by keyword overlap and sorted by score. Before using it, I checked: does it filter by all three parameters? Does it handle `size=None` and `max_price=None` correctly? Does it return `[]` without raising?

I overrode one thing: Claude's initial size filter used exact matching (`item["size"] == size`), which would have missed `"S/M"` when the user typed `"S"`. I changed it to a case-insensitive substring check (`query_size in item_size`) after reading the actual size values in the dataset (`"S/M"`, `"M/L"`, `"XL (oversized)"`, etc.).

### Instance 2 — planning loop implementation

I gave Claude the full ASCII architecture diagram from `planning.md` plus the Planning Loop and State Management sections. The prompt asked it to implement `run_agent()` following the exact branches shown in the diagram. Claude generated code that branched on `results == []` and stored values in the session dict as specified.

I overrode two things: (1) Claude returned from `run_agent()` using `return session` inside the if-block, which was correct, but it also called all three tools at module scope in an `if __name__ == "__main__"` block that would have run on import — I moved that into the existing CLI block. (2) Claude's error message for the no-results case was generic ("No results found"). I replaced it with the version that includes the parsed search terms and specific suggestions, because "try a broader description or higher price ceiling" is actually actionable, while "no results found" isn't.

---

## Running the Tests

Tests run without a Groq API key — `groq` and `dotenv` are stubbed at import time, and LLM calls are mocked per test.

```bash
python -m unittest discover -s tests -v
# or, if pytest is installed:
pytest tests/ -v
```

Expected output: 24 tests, all passing.

---

## Demo Script (for reference)

**Happy path query:** `"vintage graphic tee under $30"`

1. Parser extracts: `description="vintage graphic tee"`, `size=None`, `max_price=30.0`
2. `search_listings` returns 3 matches — top result is the Y2K Baby Tee at $18 (Depop)
3. `suggest_outfit` receives the tee + 10-item wardrobe → returns specific outfit combinations
4. `create_fit_card` receives the outfit → returns a casual Instagram-style caption
5. All three panels populate

**Error path query:** `"designer ballgown size XXS under $5"`

1. Parser extracts: `description="designer ballgown"`, `size="XXS"`, `max_price=5.0`
2. `search_listings` returns `[]`
3. Agent sets error message, returns early — panels 2 and 3 are empty
4. Panel 1 shows: *"No listings matched 'designer ballgown' (size XXS and under $5). Try a broader description, a higher price ceiling, or removing the size filter."*
