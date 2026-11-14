"""Screen a product against the recall graph.

Matching free-text product names is the part that can be wrong, so it is kept
explainable. Every match carries a tier and the reasons for it:

  EXACT     the UPC is listed on the recall
  LIKELY    the brand appears on the recall and most of the product words match
  POSSIBLE  the brand appears and some product words match, or (no brand given)
            most product words match
  SIMILAR   the product words match a recall by a different firm: the same kind
            of product, not this one

Candidates come from Relate's full-text indexes; each candidate is then scored
on the words it shares with the query, weighted so that rare words (a brand, a
model name) count for more than common ones ("kids", "chair").

A screen that finds nothing means "no match in the CPSC record as of the last
sync". It is not a statement that a product is safe.
"""

from __future__ import annotations

import math
from typing import Any

import relate

from . import text

LIKELY_WITH_BRAND = 0.60
POSSIBLE_WITH_BRAND = 0.30
LIKELY_NO_BRAND = 0.85
POSSIBLE_NO_BRAND = 0.60
SIMILAR_OTHER_FIRM = 0.80
DISTINCTIVE_DF = 25          # with a brand: a shared word in at most this many recalls is specific
RARE_DF = 5                  # without a brand: only a word this rare can make a match LIKELY
CANDIDATES = 60
TIER_ORDER = {"EXACT": 0, "LIKELY": 1, "POSSIBLE": 2, "SIMILAR": 3}

NOTE = ("Matches are against U.S. CPSC recall records only. No match means no match in "
        "that record as of the last sync; it is not a safety assessment.")

_idf_cache: dict[tuple[str, int], dict[str, Any]] = {}  # one entry: the current store state


def _frequencies(conn: relate.Connection) -> dict[str, Any]:
    """How many recalls each word appears in. Cached per store state."""
    row = conn.execute(
        "MATCH (r:Recall) RETURN count(r) AS n, toString(max(r.last_published)) AS through").single()
    n, key = row["n"], (row["through"] or "", row["n"])
    cached = _idf_cache.get(key)
    if cached:
        return cached
    df: dict[str, int] = {}
    rows = conn.execute(
        "MATCH (r:Recall) OPTIONAL MATCH (r)-[:COVERS]->(p:Product) "
        "RETURN r.title AS title, collect(p.name) AS names").data()
    for row in rows:
        for tok in set(text.tokens(row["title"] + " " + " ".join(row["names"]))):
            df[tok] = df.get(tok, 0) + 1
    out = {"n": n, "df": df}
    _idf_cache.clear()
    _idf_cache[key] = out
    return out


def _idf(freq: dict[str, Any], token: str) -> float:
    return math.log((freq["n"] + 1) / (freq["df"].get(token, 0) + 1)) + 1.0


_CANDIDATE_Q = """
CALL db.index.fulltext.queryNodes($index, $q) YIELD node, score
WITH node, score ORDER BY score DESC LIMIT $k
{hop}
RETURN DISTINCT r.id AS id
"""

_DETAIL_Q = """
MATCH (r:Recall) WHERE r.id IN $ids
OPTIONAL MATCH (r)-[:COVERS]->(p:Product)
WITH r, collect(DISTINCT p.name) AS products
OPTIONAL MATCH (r)-[rel:MANUFACTURED_BY|IMPORTED_BY|DISTRIBUTED_BY|ANNOUNCED_BY]->(c:Company)
WITH r, products, collect(DISTINCT {name: c.name, alias: c.alias, role: type(rel), key: c.key}) AS firms
OPTIONAL MATCH (r)-[:HAS_HAZARD]->(h:Hazard)
WITH r, products, firms, collect(DISTINCT h.name) AS hazards
OPTIONAL MATCH (r)-[:OFFERS]->(m:Remedy)
RETURN r.id AS id, r.number AS number, toString(r.date) AS date, r.title AS title, r.url AS url,
       r.units AS units, products, firms, hazards, collect(DISTINCT m.name) AS remedies
"""


def _candidates(conn: relate.Connection, words: list[str]) -> set[int]:
    if not words:
        return set()
    q = " OR ".join(sorted(set(words)))
    ids: set[int] = set()
    for index, hop in (("product_ft", "MATCH (r:Recall)-[:COVERS]->(node)"),
                       ("recall_ft", "WITH node AS r"),
                       ("company_ft", "MATCH (r:Recall)-[:MANUFACTURED_BY|IMPORTED_BY|DISTRIBUTED_BY|ANNOUNCED_BY]->(node)")):
        rows = conn.execute(_CANDIDATE_Q.format(hop=hop), {"index": index, "q": q, "k": CANDIDATES}).data()
        ids.update(r["id"] for r in rows)
    return ids


def synced_through(conn: relate.Connection) -> str | None:
    return conn.execute("MATCH (r:Recall) RETURN toString(max(r.last_published)) AS d").single()["d"]


def screen(
    conn: relate.Connection,
    name: str,
    brand: str | None = None,
    upc: str | None = None,
    limit: int = 5,
    only_recalls: set[int] | None = None,
) -> dict[str, Any]:
    """Find recalls that may cover a product. See the module docstring for tiers.

    ``only_recalls`` restricts the answer to the given recall ids; the ledger
    uses it to ask "does anything published since my last check match?".
    """
    freq = _frequencies(conn)
    brand_tokens = list(dict.fromkeys(text.tokens(brand, keep_stopwords=True))) if brand else []
    name_tokens = list(dict.fromkeys(text.tokens(name)))
    product_tokens = [t for t in name_tokens if t not in brand_tokens] or name_tokens
    code = text.upc(upc)

    ids = _candidates(conn, name_tokens + brand_tokens)
    exact: set[int] = set()
    if len(code) >= 8:
        exact = {r["id"] for r in conn.execute(
            "MATCH (:Upc {code: $code})<-[:HAS_UPC]-(r:Recall) RETURN r.id AS id", {"code": code}).data()}
        ids |= exact
    if only_recalls is not None:
        ids &= only_recalls

    matches = []
    total = sum(_idf(freq, t) for t in product_tokens) or 1.0
    for row in conn.execute(_DETAIL_Q, {"ids": sorted(ids)}).data():
        doc = set(text.tokens(row["title"] + " " + " ".join(row["products"])))
        firm_words = set()
        for f in row["firms"]:
            if f["name"]:
                firm_words.update(text.tokens(f"{f['name']} {f['alias'] or ''}", keep_stopwords=True))
        shared = [t for t in product_tokens if t in doc]
        coverage = sum(_idf(freq, t) for t in shared) / total
        distinctive = [t for t in shared if freq["df"].get(t, 0) <= DISTINCTIVE_DF]
        brand_hit = bool(brand_tokens) and all(t in doc or t in firm_words for t in brand_tokens)

        tier, why = None, []
        if row["id"] in exact:
            tier, why = "EXACT", [f"UPC {code} is listed on this recall"]
        elif brand_tokens:
            whole = coverage >= 0.999 and len(shared) >= 2
            if brand_hit and coverage >= LIKELY_WITH_BRAND and (distinctive or whole):
                tier = "LIKELY"
            elif brand_hit and coverage >= POSSIBLE_WITH_BRAND:
                tier = "POSSIBLE"
            elif not brand_hit and coverage >= SIMILAR_OTHER_FIRM and len(shared) >= 2:
                tier = "SIMILAR"
            if tier:
                why.append(f"brand '{brand}' " + ("appears on this recall" if brand_hit
                                                  else "does not appear on this recall"))
        else:
            rare = [t for t in shared if freq["df"].get(t, 0) <= RARE_DF]
            if coverage >= LIKELY_NO_BRAND and rare:
                tier = "LIKELY"
            elif coverage >= POSSIBLE_NO_BRAND and len(shared) >= 2:
                tier = "POSSIBLE"
        if tier is None:
            continue
        if tier != "EXACT":
            why.append(f"shares {len(shared)} of {len(product_tokens)} product words: {', '.join(shared)}")
            if distinctive:
                why.append(f"including rare words: {', '.join(distinctive)}")
        matches.append({
            "tier": tier, "score": round(coverage, 3), "why": why,
            "recall": {k: row[k] for k in ("id", "number", "date", "title", "url", "units",
                                           "hazards", "remedies")},
            "products": row["products"],
            "firms": [f for f in row["firms"] if f["name"]],
        })

    matches.sort(key=lambda m: (TIER_ORDER[m["tier"]], -m["score"], _neg_date(m["recall"]["date"])))
    return {
        "query": {"name": name, "brand": brand, "upc": code or None},
        "synced_through": synced_through(conn),
        "matches": matches[:limit],
        "match_count": len(matches),
        "firm": firm_record(conn, brand) if brand else None,
        "note": NOTE,
    }


def _neg_date(day: str) -> int:
    return -int(day.replace("-", ""))


_FIRM_Q = """
MATCH (c:Company) WHERE c.key IN $keys
MATCH (c)<-[rel:MANUFACTURED_BY|IMPORTED_BY|DISTRIBUTED_BY|ANNOUNCED_BY]-(r:Recall)
WITH collect(DISTINCT c.name) AS names, collect(DISTINCT r) AS recalls
UNWIND recalls AS r
OPTIONAL MATCH (r)-[:HAS_HAZARD]->(h:Hazard)
WITH names, r, collect(h.name) AS hz
ORDER BY r.date DESC
RETURN names, count(r) AS recalls, toString(min(r.date)) AS first, toString(max(r.date)) AS latest,
       sum(coalesce(r.units, 0)) AS units,
       collect({id: r.id, date: toString(r.date), title: r.title, url: r.url, hazards: hz})[0..$recent] AS recent
"""


def firm_keys(conn: relate.Connection, name: str) -> list[dict[str, str]]:
    """Company records a firm name refers to: same key, or the name as a whole-word prefix."""
    key = text.company_key(name)
    if not key:
        return []
    rows = conn.execute(
        "MATCH (c:Company) WHERE c.key = $key OR c.key STARTS WITH $prefix "
        "RETURN c.key AS key, c.name AS name ORDER BY key",
        {"key": key, "prefix": key + " "}).data()
    return rows


def firm_record(conn: relate.Connection, name: str, recent: int = 5) -> dict[str, Any] | None:
    """Every recall a firm is named on, pooled across the spellings of its name."""
    found = firm_keys(conn, name)
    if not found:
        return None
    rows = conn.execute(_FIRM_Q, {"keys": [f["key"] for f in found], "recent": recent}).data()
    if not rows or not rows[0]["recalls"]:
        return None
    row = rows[0]
    hazards: dict[str, int] = {}
    for r in conn.execute(
            "MATCH (c:Company)<-[:MANUFACTURED_BY|IMPORTED_BY|DISTRIBUTED_BY|ANNOUNCED_BY]-(r:Recall)"
            "-[:HAS_HAZARD]->(h:Hazard) WHERE c.key IN $keys "
            "RETURN h.name AS hazard, count(DISTINCT r) AS n ORDER BY n DESC",
            {"keys": [f["key"] for f in found]}).data():
        hazards[r["hazard"]] = r["n"]
    return {"query": name, "names_pooled": sorted(row["names"]), "recalls": row["recalls"],
            "first": row["first"], "latest": row["latest"], "units": row["units"],
            "hazards": hazards, "recent": row["recent"]}
