"""Turning the free text in recall records into things that can be joined.

Everything here is a plain, inspectable rule. Nothing is guessed by a model:
if a rule cannot extract something, the field is left empty.
"""

from __future__ import annotations

import re
import unicodedata

LEGAL_SUFFIXES = {
    "inc", "incorporated", "llc", "ltd", "limited", "co", "corp", "corporation", "company",
    "lp", "llp", "plc", "gmbh", "sa", "srl", "bv", "ag", "pty", "nv", "kg", "oy", "ab",
}

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "for", "with", "in", "on", "at", "by", "to", "from",
    "due", "recall", "recalls", "recalled", "hazard", "hazards", "risk", "sold", "exclusively",
    "brand", "branded", "model", "models", "new", "set", "sets", "inch", "pack", "pcs", "piece",
    "pieces", "size", "color", "colour", "style", "series", "version",
} | LEGAL_SUFFIXES

# Hazard vocabulary: canonical name -> pattern. Applied to the recall title
# first ("... Due to Fire and Burn Hazards"), then to the hazard text.
HAZARDS = {
    "fire": r"\bfires?\b|\bflammab|\bignit|\boverheat",
    "burn": r"\bburns?\b|\bscald",
    "electric shock": r"\bshock\b|\belectrocut",
    "choking": r"\bchok",
    "ingestion": r"\bingest|\bswallow|\bmagnets?\b|\bbutton (cell|batter)",
    "laceration": r"\blacerat|\bcuts?\b|\bamputat",
    "fall": r"\bfalls?\b|\bfalling\b",
    "strangulation": r"\bstrangul",
    "suffocation": r"\bsuffocat|\basphyx",
    "entrapment": r"\bentrap",
    "drowning": r"\bdrown",
    "lead": r"\blead\b",
    "poisoning": r"\bpoison|\bcarbon monoxide\b",
    "tip-over": r"\btip[- ]?over",
    "explosion": r"\bexplo|\brupture|\bburst",
    "crash": r"\bcrash|\bcollision",
    "injury": r"\binjur|\bimpact\b",
    "contamination": r"\bbacteria|\bmold\b|\bcontaminat",
    "regulatory violation": r"\bviolat",
}
_HAZARD_RE = {name: re.compile(pattern, re.I) for name, pattern in HAZARDS.items()}

# Retailers recognised in the "sold at" text. A retailer is linked only when
# its name literally appears there.
RETAILERS = {
    "Walmart": r"\bwal-?mart(\.com)?\b",
    "Sam's Club": r"\bsam['’]?s club\b",
    "Target": r"\btarget(\.com)?\b(?! (audience|market|age))",
    "Amazon": r"\bamazon(\.com)?\b",
    "Home Depot": r"\bhome depot\b",
    "Lowe's": r"\blowe['’]?s\b",
    "Costco": r"\bcostco\b",
    "Best Buy": r"\bbest buy\b",
    "Kohl's": r"\bkohl['’]?s\b",
    "Macy's": r"\bmacy['’]?s\b",
    "Kmart": r"\bk-?mart\b",
    "Sears": r"\bsears\b",
    "Toys R Us": r"\btoys\s*[\"“”']?r[\"“”']?\s*us\b",
    "Walgreens": r"\bwalgreens?\b",
    "CVS": r"\bcvs\b",
    "Dollar General": r"\bdollar general\b",
    "Dollar Tree": r"\bdollar tree\b",
    "Family Dollar": r"\bfamily dollar\b",
    "Big Lots": r"\bbig lots\b",
    "TJ Maxx": r"\bt\.?j\.? ?maxx\b",
    "Marshalls": r"\bmarshalls\b",
    "HomeGoods": r"\bhome ?goods\b",
    "Ross": r"\bross (stores|dress)\b",
    "Burlington": r"\bburlington\b",
    "Bed Bath & Beyond": r"\bbed,? bath (&|and) beyond\b",
    "IKEA": r"\bikea\b",
    "Dick's Sporting Goods": r"\bdick['’]?s sporting goods\b",
    "REI": r"\brei\b",
    "Ace Hardware": r"\bace hardware\b",
    "Menards": r"\bmenards\b",
    "Tractor Supply": r"\btractor supply\b",
    "Nordstrom": r"\bnordstrom\b",
    "JCPenney": r"\bj\.?\s?c\.?\s?penney\b",
    "eBay": r"\bebay(\.com)?\b",
    "Temu": r"\btemu(\.com)?\b",
    "Shein": r"\bshein(\.com)?\b",
    "Wayfair": r"\bwayfair(\.com)?\b",
    "Etsy": r"\betsy(\.com)?\b",
    "BJ's Wholesale": r"\bbj['’]?s\b",
    "Kroger": r"\bkroger\b",
    "Staples": r"\bstaples\b",
    "Michaels": r"\bmichaels\b",
    "Hobby Lobby": r"\bhobby lobby\b",
    "Five Below": r"\bfive below\b",
    "Babies R Us": r"\bbabies\s*[\"“”']?r[\"“”']?\s*us\b",
    "Buy Buy Baby": r"\bbuy ?buy baby\b",
    "Pottery Barn": r"\bpottery barn\b",
    "Williams-Sonoma": r"\bwilliams[- ]sonoma\b",
    "Cabela's": r"\bcabela['’]?s\b",
    "Bass Pro Shops": r"\bbass pro\b",
    "Academy Sports": r"\bacademy sports\b",
}
_RETAILER_RE = {name: re.compile(pattern, re.I) for name, pattern in RETAILERS.items()}

_TITLE_FIRM = [
    re.compile(r"^(?P<firm>.+?)\s+(?:re-?announces?|expands?|announces?|recalls?)\b", re.I),
    re.compile(r"\brecalled by (?P<firm>.+?)(?:;|\(| due to|$)", re.I),
]
_DBA = re.compile(r"\s*,?\s*\(?\b(?:d/b/a|dba|doing business as)\b\.?\s*", re.I)
_LOCATION = re.compile(r",?\s+of\s+(?=[A-Z])")


def clean(text: str | None) -> str:
    """Collapse whitespace and drop trademark marks."""
    if not text:
        return ""
    text = text.replace("®", "").replace("™", "").replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip()


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def _stem(token: str) -> str:
    if len(token) > 3 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokens(text: str | None, keep_stopwords: bool = False) -> list[str]:
    """Lower-case word tokens, singularised, without filler words."""
    out = []
    for raw in re.findall(r"[a-z0-9]+", _ascii(clean(text)).lower().replace("'", "")):
        tok = _stem(raw)
        if len(tok) < 2 and not tok.isdigit():
            continue
        if not keep_stopwords and (raw in STOPWORDS or tok in STOPWORDS):
            continue
        out.append(tok)
    return out


def company_key(name: str) -> str:
    """A join key for company names: case, punctuation and legal suffix removed."""
    words = re.findall(r"[a-z0-9]+", _ascii(clean(name)).lower().replace("&", " and ").replace("'", ""))
    if words and words[0] == "the":
        words = words[1:]
    while len(words) > 1 and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


def split_company(raw: str) -> dict[str, str] | None:
    """'Acme Corp. d/b/a Zap, of Reno, Nev.' -> name, alias, location, key."""
    text = clean(raw)
    if not text:
        return None
    location = ""
    parts = _LOCATION.split(text, maxsplit=1)
    if len(parts) == 2:
        text, location = parts[0], parts[1].rstrip(".")
    alias = ""
    dba = _DBA.split(text, maxsplit=1)
    if len(dba) == 2:
        text, alias = dba[0], dba[1].strip(" ,.)")
    name = text.strip(" ,;")
    key = company_key(name)
    if not key or len(name) > 120:
        return None
    return {"key": key, "name": name, "alias": alias, "location": location}


def title_firm(title: str) -> str:
    """The firm named in a recall title, or '' when the title names none."""
    title = clean(title)
    for pattern in _TITLE_FIRM:
        m = pattern.search(title)
        if m:
            firm = m.group("firm").strip(" ,;")
            # "MJKSARE High Chairs Recalled ..." names a product, not a firm.
            if firm and len(firm) <= 80 and not re.search(r"\brecalled$", firm, re.I):
                return firm
    return ""


def hazards(title: str, hazard_text: str = "") -> list[str]:
    due = re.search(r"\bdue to\b(.*)", clean(title), re.I)
    source = due.group(1) if due else ""
    found = [name for name, rx in _HAZARD_RE.items() if rx.search(source)]
    if not found:
        found = [name for name, rx in _HAZARD_RE.items() if rx.search(clean(hazard_text))]
    return found


def retailers(sold_at: str) -> list[str]:
    text = clean(sold_at)
    return [name for name, rx in _RETAILER_RE.items() if rx.search(text)]


def units(text: str | None) -> int | None:
    """'About 265,000' -> 265000, 'About 1.2 million' -> 1200000."""
    text = clean(text).lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*(million|billion)", text)
    if m:
        return int(float(m.group(1)) * (10**6 if m.group(2) == "million" else 10**9))
    m = re.search(r"\d[\d,]*", text)
    if m:
        digits = m.group(0).replace(",", "")
        return int(digits) if digits.isdigit() else None
    return None


def upc(code: str | None) -> str:
    return re.sub(r"\D", "", code or "")
