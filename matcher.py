"""
matcher.py
----------
PURPOSE:
    Takes user input (name, DOB, nationality, passport/IC)
    and searches all loaded sanctions records from MOHA, UNSCR, OFAC.

    Returns a ranked list of matches with:
        - Final score (0-100%)
        - Decision: HIT / POSSIBLE MATCH / CLEAR
        - Which source matched (MOHA / UNSCR / OFAC)
        - Breakdown of how each field scored

HOW SCORING WORKS:
    Every record gets a score from 0-100 based on 4 fields:

    Name similarity    → 50% weight  (fuzzy match via RapidFuzz)
    Date of Birth      → 25% weight  (exact, year-only, or no match)
    Nationality        → 15% weight  (fuzzy country name match)
    Passport / IC      → 10% weight  (exact string match bonus)

    Final score = (name×0.5) + (dob×0.25) + (nat×0.15) + (id×0.10)

DECISION THRESHOLDS:
    ≥ 85% → 🔴 HIT           (strong match — escalate immediately)
    60–84% → 🟡 POSSIBLE     (needs manual review)
    < 60%  → 🟢 CLEAR        (no meaningful match)

WHY RAPIDFUZZ INSTEAD OF EXACT MATCH:
    Names are spelled differently across sources and by users:
        "Mohamed"  vs "Muhammad" vs "Mohammed"
        "Ali"      vs "Aly"      vs "Alee"
        "Hassan"   vs "Hasan"    vs "Hussan"

    RapidFuzz measures string similarity 0-100.
    We use WRatio which handles:
        - Partial matches (substring matching)
        - Token order differences ("Ali Mohamed" vs "Mohamed Ali")
        - Minor spelling differences
"""

import re
from rapidfuzz import fuzz   # Fast fuzzy string matching library


# ─────────────────────────────────────────────
# CONSTANTS — decision thresholds
# ─────────────────────────────────────────────

HIT_THRESHOLD      = 85   # Score ≥ 85 → 🔴 HIT
POSSIBLE_THRESHOLD = 60   # Score 60-84 → 🟡 POSSIBLE MATCH
                          # Score < 60  → 🟢 CLEAR

# How many top candidates to return to the UI
MAX_RESULTS = 10

# Scoring weights (must sum to 1.0)
WEIGHT_NAME        = 0.50
WEIGHT_DOB         = 0.25
WEIGHT_NATIONALITY = 0.15
WEIGHT_ID          = 0.10


# ─────────────────────────────────────────────
# HELPER FUNCTION 1: normalize_name
# ─────────────────────────────────────────────
def normalize_name(text):
    """
    WHAT IT DOES:
        Prepares a name string for fuzzy comparison.
        Removes noise that would reduce match accuracy.

    TRANSFORMATIONS:
        1. Lowercase everything          "MOHAMED ALI" → "mohamed ali"
        2. Remove common titles          "Dr. Mohamed" → "mohamed"
        3. Remove "bin/binti/bte"        "Ali bin Hassan" → "ali hassan"
           (Malay patronymic particles — same person, different formats)
        4. Remove special characters     "Al-Rashid" → "al rashid"
        5. Collapse extra spaces         "ali  hassan" → "ali hassan"

    WHY REMOVE BIN/BINTI?
        Malaysian names use "bin" (son of) and "binti" (daughter of).
        "Muhammad bin Hassan" and "Muhammad Hassan" are the same person.
        Different sources may include or omit these particles.
        Removing them improves match accuracy significantly.

    EXAMPLE:
        Input:  "Dr. Muhammad Aqif bin Rahizat"
        Output: "muhammad aqif rahizat"

        Input:  "HALIMAH BINTI HUSSEIN"
        Output: "halimah hussein"
    """
    if not text:
        return ""

    text = text.lower().strip()

    # Remove common titles/prefixes
    titles = [
        r"\bdr\.?\b", r"\bprof\.?\b", r"\bmr\.?\b", r"\bmrs\.?\b",
        r"\bms\.?\b",  r"\bhaji\b",    r"\bhj\.?\b", r"\bdato\b",
        r"\bdatuk\b",  r"\btan sri\b", r"\btun\b",
    ]
    for title in titles:
        text = re.sub(title, "", text)

    # Remove Malay patronymic particles
    text = re.sub(r"\bbin\b",   " ", text)
    text = re.sub(r"\bbinti\b", " ", text)
    text = re.sub(r"\bbte\.?\b","  ", text)
    text = re.sub(r"\bbt\.?\b", " ", text)
    text = re.sub(r"\bs/o\b",   " ", text)   # son of (Indian names)
    text = re.sub(r"\bd/o\b",   " ", text)   # daughter of

    # Remove special characters (keep letters, digits, spaces)
    text = re.sub(r"[^a-z0-9\s]", " ", text)

    # Collapse multiple spaces into one
    text = re.sub(r"\s+", " ", text).strip()

    return text


# ─────────────────────────────────────────────
# HELPER FUNCTION 2: score_name
# ─────────────────────────────────────────────
def score_name(input_name, record_names):
    """
    WHAT IT DOES:
        Compares the user's input name against ALL names in a record
        (primary name + all aliases) and returns the HIGHEST score.

        We normalize both sides before comparing.

        Uses RapidFuzz WRatio which combines:
            - Ratio: direct character similarity
            - Partial ratio: checks if one name is contained in other
            - Token sort ratio: handles word order differences
              "Ali Mohamed Hassan" vs "Mohamed Hassan Ali" → high score

    INPUT:
        input_name   → string from user e.g. "Mohamed Ali Hassan"
        record_names → list of strings from sanctions record
                       e.g. ["Hamidi Gula Khan", "Hameedi Gula Khan"]

    RETURNS:
        Tuple (best_score, best_matched_name)
        best_score: 0-100 float
        best_matched_name: which record name gave the best score

    EXAMPLE:
        input_name   = "muhammad aqif rahizat"  (normalized)
        record_names = ["Muhammad Aqif Heusen Rahizat", "..."]
        Returns: (95.0, "Muhammad Aqif Heusen Rahizat")
    """
    if not input_name or not record_names:
        return 0.0, ""

    normalized_input = normalize_name(input_name)
    if not normalized_input:
        return 0.0, ""

    best_score = 0.0
    best_name  = ""

    for record_name in record_names:
        normalized_record = normalize_name(record_name)
        if not normalized_record:
            continue

        # WRatio: best of multiple fuzzy algorithms
        score = fuzz.WRatio(normalized_input, normalized_record)

        if score > best_score:
            best_score = score
            best_name  = record_name

    return best_score, best_name


# ─────────────────────────────────────────────
# HELPER FUNCTION 3: score_dob
# ─────────────────────────────────────────────
def score_dob(input_dob, record_dob):
    """
    WHAT IT DOES:
        Compares dates of birth with 3 levels of matching:

        Level 1 — Exact match:      "1992-06-29" == "1992-06-29" → 100
        Level 2 — Year only match:  "1992-06-29" vs "1992"        → 70
        Level 3 — No match / empty:                                →  0

    WHY YEAR-ONLY?
        Some sanctions records only have a birth year (not full date).
        UNSCR often records year only: "1967"
        We still give partial credit — same birth year is meaningful.

    WHY NOT FUZZY FOR DATES?
        Dates are structured data. "1992-06-29" and "1993-06-29"
        look similar as strings but are completely different dates.
        Fuzzy would give them a high score — wrong.
        We do exact comparison only.

    INPUT:
        input_dob  → string e.g. "1992-06-29" or "29/06/1992" or ""
        record_dob → string e.g. "1992-06-29" or "1992" or ""

    RETURNS:
        Score 0-100
    """
    # If either side is missing → no score (don't penalize missing data)
    if not input_dob or not record_dob:
        return 0

    # Normalize: extract YYYY-MM-DD or YYYY from various formats
    def extract_parts(dob_str):
        dob_str = dob_str.strip()
        # Try YYYY-MM-DD
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", dob_str)
        if m:
            return m.group(1), m.group(2), m.group(3)  # year, month, day
        # Try DD/MM/YYYY
        m = re.match(r"(\d{2})/(\d{2})/(\d{4})", dob_str)
        if m:
            return m.group(3), m.group(2), m.group(1)
        # Try DD-MM-YYYY
        m = re.match(r"(\d{2})-(\d{2})-(\d{4})", dob_str)
        if m:
            return m.group(3), m.group(2), m.group(1)
        # Try YYYY only
        m = re.match(r"(\d{4})$", dob_str)
        if m:
            return m.group(1), None, None
        return None, None, None

    in_year,  in_month,  in_day  = extract_parts(input_dob)
    rec_year, rec_month, rec_day = extract_parts(record_dob)

    if not in_year or not rec_year:
        return 0

    # Both have full dates → exact match required
    if in_month and rec_month and in_day and rec_day:
        if in_year == rec_year and in_month == rec_month and in_day == rec_day:
            return 100  # Exact full date match
        elif in_year == rec_year:
            return 40   # Same year, different month/day
        else:
            return 0    # Different year → no match

    # One or both sides have year only
    if in_year == rec_year:
        return 70   # Year match (partial credit)

    return 0  # Different year


# ─────────────────────────────────────────────
# HELPER FUNCTION 4: score_nationality
# ─────────────────────────────────────────────
def score_nationality(input_nat, record_nat):
    """
    WHAT IT DOES:
        Compares nationality strings with fuzzy matching.
        This handles variations like:
            "Malaysia" vs "Malaysian"
            "United States" vs "USA" vs "US"
            "Mesir" (Malay) vs "Egypt" (English)

        We use a simple token-based fuzzy match.
        We also check common country name aliases manually.

    INPUT:
        input_nat  → user input e.g. "Malaysia" or "Malaysian"
        record_nat → record value e.g. "Malaysia" or "Mesir"

    RETURNS:
        Score 0-100
    """
    if not input_nat or not record_nat:
        return 0  # Missing → no score, don't penalize

    # Normalize
    a = input_nat.lower().strip()
    b = record_nat.lower().strip()

    # Direct or fuzzy match
    direct_score = fuzz.WRatio(a, b)
    if direct_score >= 80:
        return direct_score

    # Country name alias mapping
    # Maps common variations to a canonical form
    aliases = {
        "malaysia":       ["malaysian", "msia", "my"],
        "egypt":          ["mesir", "egyptian", "eg"],
        "syria":          ["syrian", "syrian arab republic"],
        "indonesia":      ["indonesian", "indo"],
        "philippines":    ["filipino", "filipina", "philippine", "ph"],
        "saudi arabia":   ["arab saudi", "ksa", "saudi"],
        "united states":  ["usa", "us", "america", "american"],
        "united kingdom": ["uk", "britain", "british", "england"],
        "afghanistan":    ["afghan"],
        "pakistan":       ["pakistani"],
        "somalia":        ["somali"],
        "turkey":         ["turki", "turkish"],
        "tunisia":        ["tunisian"],
        "yemen":          ["yaman", "yemeni"],
        "iraq":           ["iraqi"],
        "iran":           ["iranian", "persia", "persian"],
        "libya":          ["libyan"],
        "sudan":          ["sudanese"],
        "nigeria":        ["nigerian"],
        "kenya":          ["kenyan"],
        "jordan":         ["jordanian"],
        "lebanon":        ["lebanese"],
        "morocco":        ["moroccan"],
        "algeria":        ["algerian"],
    }

    def get_canonical(name):
        for canonical, variants in aliases.items():
            if name == canonical or name in variants:
                return canonical
        return name

    if get_canonical(a) == get_canonical(b):
        return 90

    return direct_score


# ─────────────────────────────────────────────
# HELPER FUNCTION 5: score_ids
# ─────────────────────────────────────────────
def score_ids(input_ids, record_ids):
    """
    WHAT IT DOES:
        Checks if ANY of the user's ID numbers match
        ANY of the record's ID numbers.

        ID matching is EXACT — no fuzzy allowed.
        A passport number is either the same or it isn't.

        We normalize both sides:
        - Uppercase: "a1234567" → "A1234567"
        - Remove spaces/dashes: "A 123-456" → "A123456"

    INPUT:
        input_ids  → list of strings from user (passport + IC combined)
        record_ids → list of strings from record (passport + IC combined)

    RETURNS:
        100 if any match found, 0 if no match
        (Binary — either match or not)
    """
    if not input_ids or not record_ids:
        return 0  # No IDs provided → no score, don't penalize

    def normalize_id(id_str):
        # Uppercase and remove spaces, dashes, dots
        return re.sub(r"[\s\-\.]", "", id_str).upper()

    normalized_inputs  = {normalize_id(i) for i in input_ids  if i}
    normalized_records = {normalize_id(r) for r in record_ids if r}

    # Remove empty strings from normalization
    normalized_inputs.discard("")
    normalized_records.discard("")

    if not normalized_inputs or not normalized_records:
        return 0

    # Check for any intersection
    if normalized_inputs & normalized_records:  # & = set intersection
        return 100  # At least one ID matches exactly

    return 0


# ─────────────────────────────────────────────
# MAIN FUNCTION 6: score_record
# ─────────────────────────────────────────────
def score_record(user_input, record):
    """
    WHAT IT DOES:
        Takes one user input and one sanctions record,
        runs all 4 scoring functions,
        combines into a weighted final score,
        and returns a detailed score breakdown.

    INPUT:
        user_input → dict with keys:
            "name"        → full name string
            "dob"         → date string
            "nationality" → country string
            "passport"    → passport number string
            "ic"          → IC number string

        record → standard record dict from parsers:
            {
                "source":      "MOHA" | "UNSCR" | "OFAC",
                "type":        "individual" | "group",
                "ref":         "reference number",
                "names":       ["Primary Name", "Alias 1"],
                "dob":         "1967-05-12",
                "nationality": "Somalia",
                "passport":    ["A1234567"],
                "ic":          ["123456-01-1234"],
                "address":     "...",
                "date_listed": "..."
            }

    RETURNS:
        Dict with full score breakdown, or None if name score too low.

        {
            "final_score":  85.5,
            "decision":     "HIT",        # HIT / POSSIBLE / CLEAR
            "name_score":   92.0,
            "dob_score":    100,
            "nat_score":    90.0,
            "id_score":     0,
            "matched_name": "Mohamed Ali Hassan",
            "record":       { ...full record dict... },
            "id_matched":   False,
        }
    """

    # ── Step 1: ID exact match check ──
    # Combine passport and IC into one list for checking
    user_ids = []
    if user_input.get("passport", "").strip():
        user_ids.append(user_input["passport"].strip())
    if user_input.get("ic", "").strip():
        user_ids.append(user_input["ic"].strip())

    record_ids = record.get("passport", []) + record.get("ic", [])

    id_score = score_ids(user_ids, record_ids)

    # ── SHORTCUT: If ID matches exactly → instant HIT ──
    # No need to check name/DOB — passport/IC is definitive
    if id_score == 100:
        return {
            "final_score":  100.0,
            "decision":     "HIT",
            "name_score":   100,
            "dob_score":    100,
            "nat_score":    100,
            "id_score":     100,
            "matched_name": record["names"][0] if record["names"] else "",
            "record":       record,
            "id_matched":   True,
        }

    # ── Step 2: Name fuzzy score ──
    name_score, matched_name = score_name(
        user_input.get("name", ""),
        record.get("names", [])
    )

    # ── Optimization: skip further scoring if name score too low ──
    # If name score is below 40%, this record can NEVER reach the
    # POSSIBLE threshold (60%) even with perfect DOB and nationality:
    #   40×0.5 + 100×0.25 + 100×0.15 = 20 + 25 + 15 = 60
    # So 40 is the absolute minimum to even be worth checking further.
    if name_score < 40:
        return None  # Signal to caller: skip this record

    # ── Step 3: DOB score ──
    dob_score = score_dob(
        user_input.get("dob", ""),
        record.get("dob", "")
    )

    # ── Step 4: Nationality score ──
    nat_score = score_nationality(
        user_input.get("nationality", ""),
        record.get("nationality", "")
    )

    # ── Step 5: Weighted final score ──
    final_score = (
        name_score * WEIGHT_NAME        +
        dob_score  * WEIGHT_DOB         +
        nat_score  * WEIGHT_NATIONALITY +
        id_score   * WEIGHT_ID
    )

    # ── Step 6: Decision ──
    if final_score >= HIT_THRESHOLD:
        decision = "HIT"
    elif final_score >= POSSIBLE_THRESHOLD:
        decision = "POSSIBLE"
    else:
        decision = "CLEAR"

    return {
        "final_score":  round(final_score, 1),
        "decision":     decision,
        "name_score":   round(name_score, 1),
        "dob_score":    dob_score,
        "nat_score":    round(nat_score, 1),
        "id_score":     id_score,
        "matched_name": matched_name,
        "record":       record,
        "id_matched":   False,
    }


# ─────────────────────────────────────────────
# ENTRY POINT: search
# ─────────────────────────────────────────────
def search(user_input, all_records):
    """
    WHAT IT DOES:
        Main search function called by app.py.
        Searches ALL records from MOHA + UNSCR + OFAC combined.

    PROCESS:
        1. Validates user input (name is required minimum)
        2. Loops every record → calls score_record()
        3. Collects all results that passed the name threshold
        4. Sorts by final score (highest first)
        5. Returns HITs + POSSIBLE + top 3 CLEARs (for context)
        6. Caps at MAX_RESULTS

    INPUT:
        user_input → dict:
            {
                "name":        "Mohamed Ali Hassan",   ← required
                "dob":         "1976-01-01",           ← optional
                "nationality": "Afghanistan",           ← optional
                "passport":    "OR944957",             ← optional
                "ic":          "",                     ← optional
            }

        all_records → combined list from all 3 parsers (MOHA+UNSCR+OFAC)

    RETURNS:
        List of score dicts sorted by final_score descending.

    EXAMPLE OUTPUT:
        [
            {
                "final_score": 95.0,
                "decision":    "HIT",
                "name_score":  92.0,
                "dob_score":   100,
                "nat_score":   90.0,
                "id_score":    0,
                "matched_name": "Hamidi Gula Khan",
                "record": { "source": "OFAC", "ref": "23633", ... },
                "id_matched": False
            },
            ...
        ]
    """
    # Validate — name is required minimum
    name = user_input.get("name", "").strip()
    if not name:
        return []

    print(f"[SEARCH] Searching {len(all_records):,} records for: '{name}'")

    results = []

    for record in all_records:
        scored = score_record(user_input, record)

        if scored is None:
            continue  # Skipped — name score below 40% threshold

        results.append(scored)

    # Sort by final score, highest first
    results.sort(key=lambda x: x["final_score"], reverse=True)

    # Separate into buckets
    hits         = [r for r in results if r["decision"] == "HIT"]
    possibles    = [r for r in results if r["decision"] == "POSSIBLE"]
    clears       = [r for r in results if r["decision"] == "CLEAR"]

    print(f"[SEARCH] HITs: {len(hits)} | POSSIBLE: {len(possibles)} | CLEAR shown: min(3,{len(clears)})")
    if results:
        print(f"[SEARCH] Top score: {results[0]['final_score']}% ({results[0]['decision']})")

    # Return HITs first, then POSSIBLE, then top 3 CLEARs (for reference)
    final_results = hits + possibles + clears[:3]

    # Cap total results
    return final_results[:MAX_RESULTS]


# ─────────────────────────────────────────────
# TEST BLOCK — run: python matcher.py
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("MATCHER.PY — SELF TEST")
    print("=" * 60)

    # Sample records simulating parsed data from MOHA
    sample_records = [
        {
            "source": "MOHA", "type": "individual",
            "ref": "KDN.I.08-2014",
            "names": ["Halimah binti Hussein", "Halimah Hussein"],
            "dob": "1961-12-09", "nationality": "Malaysia",
            "passport": ["A1211429"], "ic": ["611209-01-5514"],
            "address": "Kuala Lumpur", "date_listed": "12 November 2014",
        },
        {
            "source": "MOHA", "type": "individual",
            "ref": "KDN.I.31-2014",
            "names": ["Muhammad Aqif Heusen bin Rahizat", "Aqif Rahizat"],
            "dob": "1992-06-29", "nationality": "Malaysia",
            "passport": ["A31691838"], "ic": ["920629-10-5777"],
            "address": "Selangor", "date_listed": "12 November 2014",
        },
        {
            "source": "UNSCR", "type": "individual",
            "ref": "QI.H.205.03",
            "names": ["Hamidi Gula Khan", "Hameedi Gula", "Hamidi Khan"],
            "dob": "1976", "nationality": "Afghanistan",
            "passport": ["OR944957"], "ic": [],
            "address": "", "date_listed": "2003-09-10",
        },
    ]

    # ── TEST 1: Exact IC match ──
    print("\n[TEST 1] Exact IC match → should be HIT (100%)")
    result = search(
        {"name": "Muhammad Aqif", "dob": "", "nationality": "", "passport": "", "ic": "920629-10-5777"},
        sample_records
    )
    if result:
        r = result[0]
        print(f"  Decision:  {r['decision']}")
        print(f"  Score:     {r['final_score']}%")
        print(f"  Matched:   {r['matched_name']}")
        print(f"  Source:    {r['record']['source']} | Ref: {r['record']['ref']}")
        print(f"  ID Match:  {r['id_matched']}")

    # ── TEST 2: Fuzzy name + DOB match ──
    print("\n[TEST 2] Fuzzy name + DOB → should be HIT or POSSIBLE")
    result = search(
        {"name": "Halimah Hussein", "dob": "1961-12-09", "nationality": "Malaysia", "passport": "", "ic": ""},
        sample_records
    )
    if result:
        r = result[0]
        print(f"  Decision:      {r['decision']}")
        print(f"  Final Score:   {r['final_score']}%")
        print(f"  Name Score:    {r['name_score']}%")
        print(f"  DOB Score:     {r['dob_score']}%")
        print(f"  Nat Score:     {r['nat_score']}%")
        print(f"  Matched name:  {r['matched_name']}")

    # ── TEST 3: Exact passport match ──
    print("\n[TEST 3] Exact passport match → should be HIT (100%)")
    result = search(
        {"name": "Hamidi Khan", "dob": "", "nationality": "Afghan", "passport": "OR944957", "ic": ""},
        sample_records
    )
    if result:
        r = result[0]
        print(f"  Decision:  {r['decision']}")
        print(f"  Score:     {r['final_score']}%")
        print(f"  Source:    {r['record']['source']}")

    # ── TEST 4: Spelling variation ──
    print("\n[TEST 4] Spelling variation — 'Mohammed Al-Rashid' → should find Halimah or show CLEAR")
    result = search(
        {"name": "Mohammed Al-Rashid", "dob": "1990-01-01", "nationality": "Saudi Arabia", "passport": "", "ic": ""},
        sample_records
    )
    decision = result[0]["decision"] if result else "CLEAR (no results)"
    score    = result[0]["final_score"] if result else 0
    print(f"  Decision: {decision} | Score: {score}%")

    # ── TEST 5: No match at all ──
    print("\n[TEST 5] No match → should be CLEAR")
    result = search(
        {"name": "John Michael Smith", "dob": "1980-03-15", "nationality": "United States", "passport": "AB123456", "ic": ""},
        sample_records
    )
    decision = result[0]["decision"] if result else "CLEAR (no results)"
    print(f"  Decision: {decision}")

    # ── TEST 6: Empty name → should return nothing ──
    print("\n[TEST 6] Empty name → should return []")
    result = search(
        {"name": "", "dob": "1980-01-01", "nationality": "Malaysia", "passport": "", "ic": ""},
        sample_records
    )
    print(f"  Results returned: {len(result)} (expected: 0)")

    print("\n" + "=" * 60)
    print("All tests complete.")
    print("=" * 60)