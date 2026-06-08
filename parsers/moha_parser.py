"""
moha_parser.py
--------------
PURPOSE:
    Reads the MOHA (Ministry of Home Affairs) PDF sanctions list and
    extracts all individual and group records into a standard format
    that the matcher.py can use.

THE PDF HAS TWO SECTIONS:
    A. INDIVIDU  → sanctioned persons (rows with name, DOB, IC, passport...)
    B. KUMPULAN  → sanctioned groups/organizations
"""

import pdfplumber   # Library that reads PDF files and extracts tables/text
import re           # Regular expressions — helps us find patterns in text
from datetime import datetime  # For converting date formats


# ─────────────────────────────────────────────
# HELPER FUNCTION 1: clean_text
# ─────────────────────────────────────────────
def clean_text(value):
    """
    WHAT IT DOES:
        PDF text often has extra spaces, newlines (\n), or None values.
        This function cleans all of that up.

    EXAMPLE:
        Input:  "  Muhammad \n Ali  "
        Output: "Muhammad Ali"

        Input:  None
        Output: ""  (empty string, safe to use)
    """
    if value is None:           # If the cell is empty in the PDF
        return ""               # Return empty string instead of crashing
    value = str(value)          # Convert to string (in case it's a number)
    value = value.replace("\n", " ")   # Replace line breaks with space
    value = re.sub(r"\s+", " ", value) # Replace multiple spaces with one space
    return value.strip()        # Remove leading/trailing spaces


# ─────────────────────────────────────────────
# HELPER FUNCTION 2: parse_date
# ─────────────────────────────────────────────
def parse_date(text):
    """
    WHAT IT DOES:
        MOHA PDF stores dates in many formats like:
            "9.12.1961"   → we want "1961-12-09"
            "1.7.1972"    → we want "1972-07-01"
            "29.6.1992"   → we want "1992-06-29"

        We try multiple date formats until one works.
        If none work, we return the original text as-is.

    WHY STANDARD FORMAT?
        So the matcher can easily compare dates between sources.
        "1961-12-09" is easy to compare. "9.12.1961" is not.
    """
    text = clean_text(text)
    if not text:
        return ""

    # List of date formats found in MOHA PDF
    formats = [
        "%d.%m.%Y",   # 9.12.1961
        "%d/%m/%Y",   # 9/12/1961
        "%Y-%m-%d",   # 1961-12-09 (already standard)
        "%d-%m-%Y",   # 9-12-1961
    ]

    for fmt in formats:
        try:
            # Try to parse the date using each format
            dt = datetime.strptime(text.strip(), fmt)
            return dt.strftime("%Y-%m-%d")  # Return in standard format
        except ValueError:
            continue  # This format didn't work, try next one

    return text  # If nothing worked, return original text


# ─────────────────────────────────────────────
# HELPER FUNCTION 3: extract_names
# ─────────────────────────────────────────────
def extract_names(name_str, alias_str):
    """
    WHAT IT DOES:
        Takes the main name and alias field from MOHA,
        combines them into one list of all possible names.

        This is important because a sanctioned person might
        try to use an alias — we need to check all of them.

    EXAMPLE:
        name_str  = "Zahar bin Abdullah"
        alias_str = "Abu Zahar"
        result    = ["Zahar bin Abdullah", "Abu Zahar"]

        name_str  = "Mohamad Alsaied Alhmidan"
        alias_str = "Mohamad Alhmidan; Walid Ayssa; Mohamad Aluoalii"
        result    = ["Mohamad Alsaied Alhmidan", "Mohamad Alhmidan",
                     "Walid Ayssa", "Mohamad Aluoalii"]
    """
    names = []

    # Add main name if it exists
    main_name = clean_text(name_str)
    if main_name:
        names.append(main_name)

    # Add aliases — they can be separated by (a), (b), (c) or newlines or semicolons
    alias = clean_text(alias_str)
    if alias and alias != "-":
        # Remove labels like (a), (b), (c) that MOHA uses for multiple aliases
        alias = re.sub(r"\([a-z]\)", ";", alias)
        # Split by common separators
        parts = re.split(r"[;|/]", alias)
        for part in parts:
            part = part.strip()
            if part and part != "-" and len(part) > 2:
                names.append(part)

    return names  # Returns list of all names including aliases


# ─────────────────────────────────────────────
# HELPER FUNCTION 4: extract_ids
# ─────────────────────────────────────────────
def extract_ids(id_str):
    """
    WHAT IT DOES:
        Some records have multiple passport or IC numbers.
        This splits them into a clean list.

    EXAMPLE:
        "(a) VIN 7332-0882A (b) F21BJAMS20000"
        → ["VIN 7332-0882A", "F21BJAMS20000"]

        "A1211429"
        → ["A1211429"]

        "-"
        → []  (empty list)
    """
    id_str = clean_text(id_str)
    if not id_str or id_str == "-":
        return []

    # Remove (a), (b), (c) labels
    id_str = re.sub(r"\([a-z]\)", ";", id_str)
    # Split by common separators
    parts = re.split(r"[;|/\n]", id_str)
    result = []
    for part in parts:
        part = part.strip()
        if part and part != "-" and len(part) > 2:
            result.append(part)
    return result


# ─────────────────────────────────────────────
# MAIN FUNCTION 5: parse_individuals
# ─────────────────────────────────────────────
def parse_individuals(all_rows):
    """
    WHAT IT DOES:
        Takes all the table rows from the PDF and finds
        the ones that belong to Section A (INDIVIDU).

        Each row in MOHA PDF has these columns:
        [0] No.
        [1] Rujukan (Reference)
        [2] Nama (Name)
        [3] Gelaran (Title)
        [4] Jawatan (Position)
        [5] Tarikh Lahir (DOB)
        [6] Tempat Lahir (Birthplace)
        [7] Nama Lain (Aliases)
        [8] Warganegara (Nationality)
        [9] Nombor Pasport (Passport)
        [10] Nombor Kad Pengenalan (IC)
        [11] Alamat (Address)
        [12] Tarikh Disenaraikan (Date Listed)

    RETURNS:
        List of clean record dictionaries, one per person.
    """
    records = []

    for row in all_rows:
        # Skip rows that don't have enough columns
        if not row or len(row) < 9:
            continue

        # Column [0] is the serial number — skip header rows
        # If it's not a number, it's probably a header row
        try:
            ref_no = clean_text(row[0])
            if not ref_no or not ref_no.replace(".", "").isdigit():
                continue  # Skip header rows like "No." or empty rows
        except:
            continue

        # Extract reference number (KDN.I.08-2014 etc.)
        reference = clean_text(row[1]) if len(row) > 1 else ""

        # Skip if reference doesn't look like a MOHA individual reference
        # MOHA individual refs start with KDN.I or KDN. I
        if reference and "K." in reference or "K.0" in reference:
            continue  # This is a group record, skip here

        # Extract all fields safely
        name        = clean_text(row[2])  if len(row) > 2  else ""
        dob_raw     = clean_text(row[5])  if len(row) > 5  else ""
        birthplace  = clean_text(row[6])  if len(row) > 6  else ""
        alias       = clean_text(row[7])  if len(row) > 7  else ""
        nationality = clean_text(row[8])  if len(row) > 8  else ""
        passport    = clean_text(row[9])  if len(row) > 9  else ""
        ic          = clean_text(row[10]) if len(row) > 10 else ""
        address     = clean_text(row[11]) if len(row) > 11 else ""
        date_listed = clean_text(row[12]) if len(row) > 12 else ""

        # Skip rows with no name — not a real record
        if not name or name == "-":
            continue

        # Build the standard record dictionary
        record = {
            "source":      "MOHA",                        # Which list this came from
            "type":        "individual",                  # Person or group
            "ref":         reference,                     # MOHA reference number
            "names":       extract_names(name, alias),    # All names + aliases
            "dob":         parse_date(dob_raw),           # Standardized date
            "birthplace":  birthplace,                    # City/state of birth
            "nationality": nationality,                   # Country of citizenship
            "passport":    extract_ids(passport),         # List of passport numbers
            "ic":          extract_ids(ic),               # List of IC numbers
            "address":     address,                       # Last known address
            "date_listed": date_listed,                   # When added to sanctions list
        }

        records.append(record)  # Add this person to our results

    return records


# ─────────────────────────────────────────────
# MAIN FUNCTION 6: parse_groups
# ─────────────────────────────────────────────
def parse_groups(all_rows):
    """
    WHAT IT DOES:
        Reads Section B (KUMPULAN) rows from the PDF.
        Groups have different columns:
        [0] No.
        [1] No. Ruj. (Reference)
        [2] Nama (Name)
        [3] Alias
        [4] Nama Lain (Other names)
        [5] Alamat (Address)
        [6] Tarikh Disenaraikan (Date Listed)

    RETURNS:
        List of group record dictionaries.
    """
    records = []

    for row in all_rows:
        if not row or len(row) < 3:
            continue

        # Groups have reference starting with KDN.K
        reference = clean_text(row[1]) if len(row) > 1 else ""
        if not reference or "K." not in reference:
            continue  # Skip if not a group reference

        name      = clean_text(row[2]) if len(row) > 2 else ""
        alias     = clean_text(row[3]) if len(row) > 3 else ""
        alt_names = clean_text(row[4]) if len(row) > 4 else ""
        address   = clean_text(row[5]) if len(row) > 5 else ""

        if not name or name == "-":
            continue

        # Combine alias and alt_names into one names list
        all_names = extract_names(name, alias + " ; " + alt_names)

        record = {
            "source":      "MOHA",
            "type":        "group",
            "ref":         reference,
            "names":       all_names,
            "dob":         "",           # Groups don't have DOB
            "birthplace":  "",
            "nationality": "",
            "passport":    [],
            "ic":          [],
            "address":     address,
            "date_listed": clean_text(row[6]) if len(row) > 6 else "",
        }

        records.append(record)

    return records


# ─────────────────────────────────────────────
# ENTRY POINT: parse_moha
# ─────────────────────────────────────────────
def parse_moha(filepath):
    """
    WHAT IT DOES:
        This is the MAIN function that app.py will call.
        It:
        1. Opens the MOHA PDF file
        2. Reads every page
        3. Extracts all table rows
        4. Sends rows to parse_individuals() and parse_groups()
        5. Combines results and returns everything

    INPUT:
        filepath → path to MOHA.pdf, e.g. "data/MOHA.pdf"

    OUTPUT:
        List of record dicts — both individuals and groups combined.

    EXAMPLE USAGE (from app.py):
        from parsers.moha_parser import parse_moha
        records = parse_moha("data/MOHA.pdf")
        print(f"Loaded {len(records)} MOHA records")
    """
    all_rows = []  # We'll collect every table row from every page here

    print(f"[MOHA] Opening PDF: {filepath}")

    # Open the PDF using pdfplumber
    with pdfplumber.open(filepath) as pdf:
        print(f"[MOHA] PDF has {len(pdf.pages)} pages")

        for page_num, page in enumerate(pdf.pages, start=1):
            # Extract tables from this page
            # pdfplumber finds table borders and returns rows as lists
            tables = page.extract_tables()

            if not tables:
                print(f"[MOHA] Page {page_num}: no tables found, skipping")
                continue

            for table in tables:
                for row in table:
                    all_rows.append(row)  # Collect every row

            print(f"[MOHA] Page {page_num}: extracted {sum(len(t) for t in tables)} rows")

    print(f"[MOHA] Total rows collected: {len(all_rows)}")

    # Parse individuals and groups from all rows
    individuals = parse_individuals(all_rows)
    groups      = parse_groups(all_rows)

    total = individuals + groups  # Combine both lists
    print(f"[MOHA] Parsed: {len(individuals)} individuals, {len(groups)} groups")
    print(f"[MOHA] Total records: {len(total)}")

    return total  # Return the full list to app.py


# ─────────────────────────────────────────────
# TEST BLOCK
# Run this file directly to test it:
#   python parsers/moha_parser.py
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import json

    # Test with your actual file
    records = parse_moha("data/MOHA.pdf")

    # Print first 3 records so you can see the output
    print("\n=== SAMPLE OUTPUT (first 3 records) ===")
    for r in records[:3]:
        print(json.dumps(r, indent=2, ensure_ascii=False))

    print(f"\nTotal records loaded: {len(records)}")