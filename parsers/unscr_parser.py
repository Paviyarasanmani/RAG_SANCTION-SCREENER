"""
unscr_parser.py
---------------
PURPOSE:
    Reads the UNSCR (UN Security Council Resolutions) XML sanctions list
    and extracts all individual and entity records into the same standard
    format that moha_parser.py uses — so matcher.py treats all sources equally.

XML STRUCTURE OVERVIEW:
    <CONSOLIDATED_LIST>
        <INDIVIDUALS>
            <INDIVIDUAL>          ← one sanctioned person
                <FIRST_NAME>
                <SECOND_NAME>
                <THIRD_NAME>
                <FOURTH_NAME>
                <NATIONALITY><VALUE>
                <INDIVIDUAL_DATE_OF_BIRTH><DATE> or <TYPE_OF_DATE><YEAR>
                <INDIVIDUAL_ALIAS><ALIAS_NAME>
                <INDIVIDUAL_DOCUMENT><TYPE_OF_DOCUMENT><NUMBER>
            </INDIVIDUAL>
        </INDIVIDUALS>
        <ENTITIES>
            <ENTITY>              ← one sanctioned organization
                <FIRST_NAME>      ← entity name stored in FIRST_NAME
                <ENTITY_ALIAS><ALIAS_NAME>
            </ENTITY>
        </ENTITIES>
    </CONSOLIDATED_LIST>

NOTE ON SIZE:
    UNSCR XML has 5000+ records. We use Python's built-in `lxml`
    which is fast and memory-efficient for large XML files.
"""

from lxml import etree   # Fast XML parser — handles large files well
import re                # For cleaning text patterns


# ─────────────────────────────────────────────
# HELPER FUNCTION 1: get_text
# ─────────────────────────────────────────────
def get_text(element, tag):
    """
    WHAT IT DOES:
        Safely reads the text content of ONE XML child tag.
        If the tag doesn't exist or is empty, returns "" safely.

    WHY SAFE?
        If we do element.find("FIRST_NAME").text directly and
        the tag is missing, Python crashes with AttributeError.
        This function handles that gracefully.

    EXAMPLE:
        XML:  <FIRST_NAME>MOHAMED</FIRST_NAME>
        Call: get_text(individual_element, "FIRST_NAME")
        Returns: "Mohamed"  (also title-cases the result)

        XML:  <THIRD_NAME/>   (empty tag)
        Call: get_text(individual_element, "THIRD_NAME")
        Returns: ""
    """
    found = element.find(tag)               # Look for the tag
    if found is None or found.text is None: # Tag missing or empty
        return ""
    text = found.text.strip()               # Remove whitespace
    return text.title() if text else ""     # Title case: "MOHAMED" → "Mohamed"


# ─────────────────────────────────────────────
# HELPER FUNCTION 2: get_multiple
# ─────────────────────────────────────────────
def get_multiple(element, tag):
    """
    WHAT IT DOES:
        Some XML fields repeat multiple times.
        For example, a person can have multiple nationalities:
            <NATIONALITY><VALUE>Somalia</VALUE></NATIONALITY>
            <NATIONALITY><VALUE>Kenya</VALUE></NATIONALITY>

        This function finds ALL of them and returns a list.

    EXAMPLE:
        Returns: ["Somalia", "Kenya"]
        If none found: returns []
    """
    results = []
    for child in element.findall(tag):   # Find ALL matching tags
        text = child.text
        if text:
            results.append(text.strip().title())
    return results


# ─────────────────────────────────────────────
# HELPER FUNCTION 3: build_full_name
# ─────────────────────────────────────────────
def build_full_name(element):
    """
    WHAT IT DOES:
        UNSCR stores names split into up to 4 parts:
            FIRST_NAME, SECOND_NAME, THIRD_NAME, FOURTH_NAME

        This joins them into one full name string.

    EXAMPLE:
        FIRST_NAME  = "Mohamed"
        SECOND_NAME = "Ali"
        THIRD_NAME  = "Hassan"
        FOURTH_NAME = ""
        Result      = "Mohamed Ali Hassan"

    WHY IMPORTANT?
        RapidFuzz needs a single string to compare against.
        We also store the parts separately for more precise matching.
    """
    parts = [
        get_text(element, "FIRST_NAME"),
        get_text(element, "SECOND_NAME"),
        get_text(element, "THIRD_NAME"),
        get_text(element, "FOURTH_NAME"),
    ]
    # Filter out empty parts and join with space
    full_name = " ".join(p for p in parts if p)
    return full_name


# ─────────────────────────────────────────────
# HELPER FUNCTION 4: extract_aliases
# ─────────────────────────────────────────────
def extract_aliases(element, alias_tag):
    """
    WHAT IT DOES:
        Extracts all alias names from an individual or entity.
        UNSCR uses <INDIVIDUAL_ALIAS> for persons
        and <ENTITY_ALIAS> for organizations.

        Each alias block looks like:
            <INDIVIDUAL_ALIAS>
                <QUALITY>Good</QUALITY>
                <ALIAS_NAME>Abu Hassan Al-Somali</ALIAS_NAME>
            </INDIVIDUAL_ALIAS>

        We only want the ALIAS_NAME value, not QUALITY.

    INPUT:
        element   → the XML element (<INDIVIDUAL> or <ENTITY>)
        alias_tag → "INDIVIDUAL_ALIAS" or "ENTITY_ALIAS"

    RETURNS:
        List of alias name strings
    """
    aliases = []
    for alias_el in element.findall(alias_tag):
        name = get_text(alias_el, "ALIAS_NAME")
        if name and name not in aliases:
            aliases.append(name)
    return aliases


# ─────────────────────────────────────────────
# HELPER FUNCTION 5: extract_documents
# ─────────────────────────────────────────────
def extract_documents(element):
    """
    WHAT IT DOES:
        Extracts all document numbers (passport, national ID, etc.)
        from an individual's record.

        Each document block looks like:
            <INDIVIDUAL_DOCUMENT>
                <TYPE_OF_DOCUMENT>Passport</TYPE_OF_DOCUMENT>
                <NUMBER>A1234567</NUMBER>
                <ISSUING_COUNTRY>Somalia</ISSUING_COUNTRY>
            </INDIVIDUAL_DOCUMENT>

        We collect all numbers into separate lists by type.

    RETURNS:
        Tuple of (passport_list, id_list)
        Example: (["A1234567", "B9876543"], ["SOM-12345"])
    """
    passports = []
    ids = []

    for doc in element.findall("INDIVIDUAL_DOCUMENT"):
        doc_type = get_text(doc, "TYPE_OF_DOCUMENT").lower()
        number   = get_text(doc, "NUMBER")

        if not number:
            continue

        # Categorize by document type
        if "passport" in doc_type:
            passports.append(number)
        else:
            # National ID, driving license, etc.
            ids.append(number)

    return passports, ids


# ─────────────────────────────────────────────
# HELPER FUNCTION 6: extract_dob
# ─────────────────────────────────────────────
def extract_dob(element):
    """
    WHAT IT DOES:
        UNSCR stores date of birth in two possible ways:

        Way 1 — Exact date:
            <INDIVIDUAL_DATE_OF_BIRTH>
                <TYPE_OF_DATE>EXACT</TYPE_OF_DATE>
                <DATE>1967-05-12</DATE>
            </INDIVIDUAL_DATE_OF_BIRTH>

        Way 2 — Year only (when exact date unknown):
            <INDIVIDUAL_DATE_OF_BIRTH>
                <TYPE_OF_DATE>APPROXIMATELY</TYPE_OF_DATE>
                <YEAR>1967</YEAR>
            </INDIVIDUAL_DATE_OF_BIRTH>

        We handle both cases.

    RETURNS:
        String like "1967-05-12" or "1967" or ""
    """
    dob_el = element.find("INDIVIDUAL_DATE_OF_BIRTH")
    if dob_el is None:
        return ""

    # Try exact date first
    date = get_text(dob_el, "DATE")
    if date:
        return date

    # Fall back to year only
    year = get_text(dob_el, "YEAR")
    if year:
        return year

    return ""


# ─────────────────────────────────────────────
# HELPER FUNCTION 7: extract_nationality
# ─────────────────────────────────────────────
def extract_nationality(element):
    """
    WHAT IT DOES:
        UNSCR stores nationality inside a nested tag:
            <NATIONALITY>
                <VALUE>Somalia</VALUE>
            </NATIONALITY>

        There can be multiple nationalities.
        We return them as a joined string: "Somalia / Kenya"

        This is different from MOHA which has a direct text field.
    """
    nats = []
    for nat_el in element.findall("NATIONALITY"):
        val = get_text(nat_el, "VALUE")
        if val:
            nats.append(val)
    return " / ".join(nats)  # "Somalia / Kenya" if dual nationality


# ─────────────────────────────────────────────
# MAIN FUNCTION 8: parse_individuals
# ─────────────────────────────────────────────
def parse_individuals(root):
    """
    WHAT IT DOES:
        Loops through every <INDIVIDUAL> element in the XML
        and builds a standard record dictionary for each one.

        The root element is <CONSOLIDATED_LIST>.
        We navigate: root → INDIVIDUALS → INDIVIDUAL (×5000+)

    RETURNS:
        List of individual record dicts in our standard format.
    """
    records = []

    # Navigate to the INDIVIDUALS section
    individuals_section = root.find("INDIVIDUALS")
    if individuals_section is None:
        print("[UNSCR] WARNING: No INDIVIDUALS section found in XML")
        return []

    # Loop every <INDIVIDUAL> element
    for individual in individuals_section.findall("INDIVIDUAL"):

        # Build full name from the 4 name parts
        full_name = build_full_name(individual)
        if not full_name:
            continue  # Skip records with no name

        # Get aliases
        aliases = extract_aliases(individual, "INDIVIDUAL_ALIAS")

        # Combine full name + aliases into one list
        all_names = [full_name] + [a for a in aliases if a != full_name]

        # Get documents
        passports, ids = extract_documents(individual)

        # Build standard record
        record = {
            "source":      "UNSCR",
            "type":        "individual",
            "ref":         get_text(individual, "REFERENCE_NUMBER"),
            "names":       all_names,
            "dob":         extract_dob(individual),
            "birthplace":  "",   # UNSCR stores this differently, skip for now
            "nationality": extract_nationality(individual),
            "passport":    passports,
            "ic":          ids,
            "address":     "",   # Address parsing complex, skip for demo
            "date_listed": get_text(individual, "LISTED_ON"),
        }

        records.append(record)

    return records


# ─────────────────────────────────────────────
# MAIN FUNCTION 9: parse_entities
# ─────────────────────────────────────────────
def parse_entities(root):
    """
    WHAT IT DOES:
        Loops through every <ENTITY> element (organizations, groups)
        in the XML and builds standard records.

        Structure is simpler than individuals:
            <ENTITY>
                <FIRST_NAME>Al-Qaeda</FIRST_NAME>
                <ENTITY_ALIAS><ALIAS_NAME>AQ</ALIAS_NAME></ENTITY_ALIAS>
            </ENTITY>

    RETURNS:
        List of entity record dicts.
    """
    records = []

    entities_section = root.find("ENTITIES")
    if entities_section is None:
        print("[UNSCR] WARNING: No ENTITIES section found in XML")
        return []

    for entity in entities_section.findall("ENTITY"):

        # Entity name is stored in FIRST_NAME tag (unusual but that's UNSCR format)
        name = get_text(entity, "FIRST_NAME")
        if not name:
            continue

        aliases = extract_aliases(entity, "ENTITY_ALIAS")
        all_names = [name] + [a for a in aliases if a != name]

        record = {
            "source":      "UNSCR",
            "type":        "group",
            "ref":         get_text(entity, "REFERENCE_NUMBER"),
            "names":       all_names,
            "dob":         "",
            "birthplace":  "",
            "nationality": "",
            "passport":    [],
            "ic":          [],
            "address":     "",
            "date_listed": get_text(entity, "LISTED_ON"),
        }

        records.append(record)

    return records


# ─────────────────────────────────────────────
# ENTRY POINT: parse_unscr
# ─────────────────────────────────────────────
def parse_unscr(filepath):
    """
    WHAT IT DOES:
        Main function called by app.py.
        1. Opens the UNSCR XML file
        2. Parses it with lxml (fast, handles large files)
        3. Calls parse_individuals() and parse_entities()
        4. Returns combined list of all records

    INPUT:
        filepath → path to UNSCR.xml e.g. "data/UNSCR.xml"

    OUTPUT:
        List of standard record dicts (same format as MOHA)

    EXAMPLE USAGE (from app.py):
        from parsers.unscr_parser import parse_unscr
        records = parse_unscr("data/UNSCR.xml")
        print(f"Loaded {len(records)} UNSCR records")
    """
    print(f"[UNSCR] Opening XML: {filepath}")

    try:
        # Parse XML — lxml is fast even for 50MB+ files
        tree = etree.parse(filepath)
        root = tree.getroot()
        print(f"[UNSCR] XML root tag: {root.tag}")
    except Exception as e:
        print(f"[UNSCR] ERROR reading XML: {e}")
        return []

    # Parse both sections
    individuals = parse_individuals(root)
    entities    = parse_entities(root)

    total = individuals + entities
    print(f"[UNSCR] Parsed: {len(individuals)} individuals, {len(entities)} entities")
    print(f"[UNSCR] Total records: {len(total)}")

    return total


# ─────────────────────────────────────────────
# TEST BLOCK
# Run: python parsers/unscr_parser.py
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import json

    records = parse_unscr("data/UNSCR.xml")

    print("\n=== SAMPLE OUTPUT (first 3 records) ===")
    for r in records[:3]:
        print(json.dumps(r, indent=2, ensure_ascii=False))

    print(f"\nTotal records loaded: {len(records)}")