"""
ofac_parser.py
--------------
PURPOSE:
    Reads OFAC SDN_ENHANCED.XML and extracts sanctioned
    individuals and entities into our standard record format.

ACTUAL XML STRUCTURE (confirmed from real file):
    <sanctionsData>
        <entity id="23633">
            <generalInfo>
                <entityType refId="600">Individual</entityType>
            </generalInfo>
            <names>
                <name>
                    <isPrimary>true</isPrimary>
                    <translations>
                        <translation>
                            <formattedFullName>HAMIDI, Gula Khan</formattedFullName>
            <features>
                <feature>
                    <type featureTypeId="8">Birthdate</type>      ← DOB
                    <value>1976</value>
                    <type featureTypeId="9">Place of Birth</type>  ← birthplace
                    <type featureTypeId="10">Nationality Country</type> ← nationality
            <identityDocuments>
                <identityDocument>
                    <type refId="1571">Passport</type>
                    <documentNumber>OR944957</documentNumber>
"""

from lxml import etree
import re
from datetime import datetime


# ─────────────────────────────────────────────
# HELPER: detect namespace
# ─────────────────────────────────────────────
def detect_namespace(root):
    """
    Detects XML namespace from root tag.
    OFAC Enhanced XML has a long namespace URL.
    Returns it as a prefix string like "{https://...}"
    so we can use it in every find() call.
    """
    tag = root.tag
    if tag.startswith("{"):
        return tag[:tag.index("}") + 1]
    return ""


# ─────────────────────────────────────────────
# HELPER: safely get text from ONE child tag
# ─────────────────────────────────────────────
def get_text(element, tag, ns=""):
    """
    Safely reads text from a single child tag.
    Returns "" if tag is missing or has no text.
    Never crashes on missing elements.
    """
    found = element.find(ns + tag)
    if found is None or found.text is None:
        return ""
    return found.text.strip()


# ─────────────────────────────────────────────
# HELPER: clean and title-case a name string
# ─────────────────────────────────────────────
def clean_name(text):
    """
    Cleans a name:
    - "HAMIDI, Gula Khan" → "Hamidi Gula Khan"  (removes comma)
    - "MOHAMED ALI"       → "Mohamed Ali"        (title case)
    - Collapses extra spaces
    """
    if not text:
        return ""
    # Remove commas (OFAC uses "LASTNAME, Firstname" format)
    text = text.replace(",", " ")
    # Collapse multiple spaces into one
    text = " ".join(text.split())
    # Title case
    return text.title()


# ─────────────────────────────────────────────
# MAIN FUNCTION: extract_names
# ─────────────────────────────────────────────
def extract_names(entity, ns):
    """
    WHAT IT DOES:
        Reads all <name> elements inside <names>.
        Each <name> has:
            <isPrimary>true/false</isPrimary>   ← primary or alias
            <isLowQuality>true/false</isLowQuality>
            <translations>
                <translation>
                    <formattedFullName>HAMIDI, Gula Khan</formattedFullName>

        We use <formattedFullName> because it's already assembled.
        We collect ALL names (primary + aliases) into one list.
        Low quality aliases are included too — better to have more.

    RETURNS:
        List of clean name strings, primary name first.
        Example: ["Hamidi Gula Khan", "Hameedi Gula Khan", "Hamidi Gul Muhammad"]
    """
    primary_names = []
    alias_names   = []

    names_el = entity.find(ns + "names")
    if names_el is None:
        return []

    for name_el in names_el.findall(ns + "name"):

        # Check if this is a primary name or alias
        is_primary = get_text(name_el, "isPrimary", ns).lower() == "true"

        # Get all translations of this name
        translations_el = name_el.find(ns + "translations")
        if translations_el is None:
            continue

        for trans in translations_el.findall(ns + "translation"):
            # Use formattedFullName — already complete
            full_name = get_text(trans, "formattedFullName", ns)
            if not full_name:
                # Fallback: build from first + last name parts
                first = get_text(trans, "formattedFirstName", ns)
                last  = get_text(trans, "formattedLastName",  ns)
                full_name = f"{first} {last}".strip()

            cleaned = clean_name(full_name)
            if not cleaned:
                continue

            # Only take the primary (Latin script) translation
            # to avoid duplicate Arabic/Chinese versions of same name
            is_primary_trans = get_text(trans, "isPrimary", ns).lower() == "true"
            if not is_primary_trans:
                continue

            if is_primary:
                if cleaned not in primary_names:
                    primary_names.append(cleaned)
            else:
                if cleaned not in alias_names and cleaned not in primary_names:
                    alias_names.append(cleaned)

    # Primary names first, then aliases
    return primary_names + alias_names


# ─────────────────────────────────────────────
# MAIN FUNCTION: extract_features
# ─────────────────────────────────────────────
def extract_features(entity, ns):
    """
    WHAT IT DOES:
        The <features> section stores DOB, birthplace, nationality
        using numeric type codes:

            featureTypeId="8"  → Birthdate
            featureTypeId="9"  → Place of Birth
            featureTypeId="10" → Nationality Country

        Each feature has a <value> tag with the text.
        DOB also has a <valueDate> with structured date fields:
            <fromDateBegin>1976-01-01</fromDateBegin>

        We prefer the structured date over the text value
        because text can be "1967 to 1969" (a range).
        For ranges we take the start date.

    RETURNS:
        Dict with keys: dob, birthplace, nationality
    """
    result = {
        "dob":         "",
        "birthplace":  "",
        "nationality": "",
    }

    features_el = entity.find(ns + "features")
    if features_el is None:
        return result

    for feature in features_el.findall(ns + "feature"):

        # Get the feature type ID from the <type> element's attribute
        type_el = feature.find(ns + "type")
        if type_el is None:
            continue

        feature_type_id = type_el.get("featureTypeId", "")
        value_text      = get_text(feature, "value", ns)

        # ── Birthdate (featureTypeId = 8) ──
        if feature_type_id == "8" and not result["dob"]:
            # Try structured date first (more reliable)
            value_date = feature.find(ns + "valueDate")
            if value_date is not None:
                # fromDateBegin is always present for DOB
                from_date = get_text(value_date, "fromDateBegin", ns)
                if from_date:
                    # Check if it's a range (isDateRange = true)
                    is_range = get_text(value_date, "isDateRange", ns).lower() == "true"
                    if is_range:
                        # For ranges like "1967 to 1969", just use the year
                        year_match = re.search(r"\b(19|20)\d{2}\b", value_text)
                        result["dob"] = year_match.group() if year_match else from_date[:4]
                    else:
                        # Exact or approximate — use full date
                        result["dob"] = from_date
            else:
                # No structured date, use text value
                year_match = re.search(r"\b(19|20)\d{2}\b", value_text)
                result["dob"] = year_match.group() if year_match else value_text

        # ── Place of Birth (featureTypeId = 9) ──
        elif feature_type_id == "9" and not result["birthplace"]:
            result["birthplace"] = value_text

        # ── Nationality (featureTypeId = 10) ──
        elif feature_type_id == "10" and not result["nationality"]:
            result["nationality"] = value_text

    return result


# ─────────────────────────────────────────────
# MAIN FUNCTION: extract_documents
# ─────────────────────────────────────────────
def extract_documents(entity, ns):
    """
    WHAT IT DOES:
        Reads <identityDocuments><identityDocument> elements.
        Each document has:
            <type refId="1571">Passport</type>   ← type text
            <documentNumber>OR944957</documentNumber>
            <isValid>true</isValid>
            <issuingCountry>Afghanistan</issuingCountry>

        Document type refIds:
            1571 → Passport
            1608 → Identification Number (national ID)
            Others → other ID types

        We include ALL valid documents.
        Invalid ones (isValid=false) are forgeries — skip them.

    RETURNS:
        Tuple (passport_list, id_list)
    """
    passports = []
    ids       = []

    id_docs_el = entity.find(ns + "identityDocuments")
    if id_docs_el is None:
        return [], []

    for doc in id_docs_el.findall(ns + "identityDocument"):

        # Skip invalid/fraudulent documents
        is_valid = get_text(doc, "isValid", ns).lower()
        if is_valid == "false":
            continue

        doc_number = get_text(doc, "documentNumber", ns)
        if not doc_number:
            continue

        # Get document type from <type> element text
        type_el   = doc.find(ns + "type")
        type_text = type_el.text.strip().lower() if (type_el is not None and type_el.text) else ""
        type_ref  = type_el.get("refId", "") if type_el is not None else ""

        # refId 1571 = Passport, or text contains "passport"
        if type_ref == "1571" or "passport" in type_text:
            passports.append(doc_number)
        else:
            ids.append(doc_number)

    return passports, ids


# ─────────────────────────────────────────────
# ENTRY POINT: parse_ofac
# ─────────────────────────────────────────────
def parse_ofac(filepath):
    """
    WHAT IT DOES:
        Main function called by app.py.
        1. Opens OFAC Enhanced XML
        2. Finds the parent tag containing <entity> elements
        3. Loops every <entity>
        4. Extracts: type, names, features (DOB/nationality), documents
        5. Returns list of standard records

    STRUCTURE NAVIGATION:
        <sanctionsData>          ← root
            <entities>           ← container (may vary)
                <entity id="...">  ← one record per entity

    INPUT:  filepath → "data/OFAC.xml"
    OUTPUT: list of standard record dicts
    """
    print(f"[OFAC] Opening XML: {filepath}")

    try:
        # Use iterparse for memory efficiency on large files
        # But for simplicity in demo, use regular parse
        tree = etree.parse(filepath)
        root = tree.getroot()
        print(f"[OFAC] Root tag: {root.tag}")
    except Exception as e:
        print(f"[OFAC] ERROR reading XML: {e}")
        return []

    ns = detect_namespace(root)
    print(f"[OFAC] Namespace detected")

    records     = []
    individuals = 0
    groups      = 0
    skipped     = 0

    # Find all <entity> elements anywhere under root
    # They may be direct children or inside a wrapper tag
    all_entities = root.findall(".//" + ns + "entity")
    print(f"[OFAC] Found {len(all_entities)} entity elements")

    for entity in all_entities:

        # ── Get entity type ──
        general_info = entity.find(ns + "generalInfo")
        if general_info is None:
            skipped += 1
            continue

        entity_type_el = general_info.find(ns + "entityType")
        entity_type    = entity_type_el.text.strip().lower() if (entity_type_el is not None and entity_type_el.text) else ""

        # Skip vessels and aircraft
        if entity_type in ("vessel", "aircraft"):
            skipped += 1
            continue

        # ── Get entity ID ──
        entity_id = entity.get("id", "")

        # ── Extract names ──
        all_names = extract_names(entity, ns)
        if not all_names:
            skipped += 1
            continue

        # ── Extract features (DOB, birthplace, nationality) ──
        features = extract_features(entity, ns)

        # ── Extract documents (passport, IDs) ──
        passports, ids = extract_documents(entity, ns)

        # ── Build standard record ──
        record = {
            "source":      "OFAC",
            "type":        "individual" if entity_type == "individual" else "group",
            "ref":         entity_id,
            "names":       all_names,
            "dob":         features["dob"],
            "birthplace":  features["birthplace"],
            "nationality": features["nationality"],
            "passport":    passports,
            "ic":          ids,
            "address":     "",
            "date_listed": "",
        }

        records.append(record)

        if entity_type == "individual":
            individuals += 1
        else:
            groups += 1

    print(f"[OFAC] Skipped {skipped} entries (vessels/aircraft/no-name)")
    print(f"[OFAC] Parsed: {individuals} individuals, {groups} entities")
    print(f"[OFAC] Total records: {len(records)}")

    return records


# ─────────────────────────────────────────────
# TEST BLOCK — run: python parsers/ofac_parser.py
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import json

    records = parse_ofac("data/OFAC.xml")

    print("\n=== SAMPLE OUTPUT (first 3 records) ===")
    for r in records[:3]:
        print(json.dumps(r, indent=2, ensure_ascii=False))

    print(f"\nTotal: {len(records)}")