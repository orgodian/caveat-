"""U.S. Consumer Product Safety Commission recalls: fetch, parse, load.

Source: the CPSC Recalls REST service (https://www.saferproducts.gov/RestWebServices).
It is public and needs no key. Recall records are U.S. government works.

A full sync walks the years from 1973; later syncs ask only for records
published since the last one. Raw responses are cached as gzipped JSON so the
store can be rebuilt offline with `caveat sync --offline`.
"""

from __future__ import annotations

import datetime as dt
import glob
import gzip
import json
import os
import time
import urllib.parse
import urllib.request
from typing import Any, Iterable

import relate

from . import __version__, text
from .store import create_schema

API = "https://www.saferproducts.gov/RestWebServices/Recall"
FIRST_YEAR = 1973
BATCH = 400
ROLES = {
    "Manufacturers": "MANUFACTURED_BY",
    "Importers": "IMPORTED_BY",
    "Distributors": "DISTRIBUTED_BY",
}


def _get(params: dict[str, str], timeout: int = 90) -> list[dict[str, Any]]:
    url = API + "?" + urllib.parse.urlencode({"format": "json", **params})
    req = urllib.request.Request(url, headers={"User-Agent": f"caveat/{__version__} (recall graph)"})
    last: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except Exception as exc:  # network errors are retried, then reported
            last = exc
            time.sleep(2 * (attempt + 1))
    raise ConnectionError(f"CPSC request failed after 3 tries: {last}")


def fetch_year(year: int) -> list[dict[str, Any]]:
    return _get({"RecallDateStart": f"{year}-01-01", "RecallDateEnd": f"{year}-12-31"})


def fetch_published_since(day: str) -> list[dict[str, Any]]:
    return _get({"LastPublishDateStart": day})


def cache_write(cache_dir: str, name: str, records: list[dict[str, Any]]) -> None:
    os.makedirs(cache_dir, exist_ok=True)
    with gzip.open(os.path.join(cache_dir, f"{name}.json.gz"), "wt", encoding="utf-8") as fh:
        json.dump(records, fh)


def cache_read(cache_dir: str) -> list[dict[str, Any]]:
    """All cached records; a recall seen twice keeps its latest published copy."""
    latest: dict[int, dict[str, Any]] = {}
    for path in sorted(glob.glob(os.path.join(cache_dir, "*.json.gz"))):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for rec in json.load(fh):
                old = latest.get(rec["RecallID"])
                if old is None or (rec.get("LastPublishDate") or "") >= (old.get("LastPublishDate") or ""):
                    latest[rec["RecallID"]] = rec
    return list(latest.values())


def _day(value: str | None) -> str | None:
    return value[:10] if value else None


def _joined(items: Iterable[dict[str, Any]], key: str = "Name") -> str:
    return text.clean(" ".join(i.get(key) or "" for i in items or []))


def parse(rec: dict[str, Any]) -> dict[str, Any]:
    """One API record -> the rows the loader writes."""
    rid = int(rec["RecallID"])
    title = text.clean(rec.get("Title"))
    hazard_text = _joined(rec.get("Hazards"))
    sold_at = _joined(rec.get("Retailers"))

    products = []
    for n, p in enumerate(rec.get("Products") or []):
        name = text.clean(p.get("Name"))
        if not name:
            continue
        products.append({"id": f"{rid}:{n}", "name": name, "type": text.clean(p.get("Type")),
                         "units": text.units(p.get("NumberOfUnits")),
                         "units_text": text.clean(p.get("NumberOfUnits"))})

    companies: dict[tuple[str, str], dict[str, str]] = {}
    for field, role in ROLES.items():
        for c in rec.get(field) or []:
            parsed = text.split_company(c.get("Name") or "")
            if parsed:
                companies[(parsed["key"], role)] = {**parsed, "role": role}
    firm = text.title_firm(title)
    if firm:
        parsed = text.split_company(firm)
        if parsed:
            companies[(parsed["key"], "ANNOUNCED_BY")] = {**parsed, "role": "ANNOUNCED_BY"}

    unit_values = [p["units"] for p in products if p["units"]]
    return {
        "recall": {
            "id": rid, "number": str(rec.get("RecallNumber") or ""), "date": _day(rec.get("RecallDate")),
            "title": title, "url": rec.get("URL") or "", "description": text.clean(rec.get("Description")),
            "hazard_text": hazard_text, "remedy_text": _joined(rec.get("Remedies")),
            "injuries": _joined(rec.get("Injuries")), "sold_at": sold_at,
            "units": sum(unit_values) if unit_values else None,
            "last_published": _day(rec.get("LastPublishDate")),
        },
        "products": products,
        "companies": list(companies.values()),
        "hazards": text.hazards(title, hazard_text),
        "retailers": text.retailers(sold_at),
        "countries": sorted({text.clean(c.get("Country")) for c in rec.get("ManufacturerCountries") or []
                             if text.clean(c.get("Country"))}),
        "upcs": sorted({text.upc(u.get("UPC")) for u in rec.get("ProductUPCs") or []
                        if len(text.upc(u.get("UPC"))) >= 8}),
        "remedies": sorted({text.clean(o.get("Option")) for o in rec.get("RemedyOptions") or []
                            if text.clean(o.get("Option"))}),
    }


def _run(conn: relate.Connection, query: str, rows: list[dict[str, Any]]) -> None:
    for start in range(0, len(rows), BATCH):
        conn.execute(query, {"rows": rows[start:start + BATCH]}).consume()


def load(conn: relate.Connection, records: list[dict[str, Any]]) -> dict[str, Any]:
    """Write records into the graph. Returns which recalls were new or changed."""
    create_schema(conn)
    parsed = [parse(r) for r in records if r.get("RecallID") is not None]
    parsed = [p for p in parsed if p["recall"]["date"]]  # a recall with no date cannot be placed in time

    known = {r["id"]: r["lp"] for r in conn.execute(
        "MATCH (r:Recall) RETURN r.id AS id, toString(r.last_published) AS lp").data()}
    new = [p for p in parsed if p["recall"]["id"] not in known]
    changed = [p for p in parsed if p["recall"]["id"] in known
               and known[p["recall"]["id"]] != p["recall"]["last_published"]]
    todo = new + changed
    if not todo:
        return {"new": [], "changed": [], "seen": len(parsed)}

    if changed:  # rebuild a changed recall from scratch so stale edges cannot linger
        ids = [{"id": p["recall"]["id"]} for p in changed]
        _run(conn, "UNWIND $rows AS r MATCH (x:Recall {id: r.id})-[:COVERS]->(p:Product) DETACH DELETE p", ids)
        _run(conn, "UNWIND $rows AS r MATCH (x:Recall {id: r.id})-[rel]->() "
                   "WHERE NOT type(rel) IN ['AFFECTS'] DELETE rel", ids)

    _run(conn,
         "UNWIND $rows AS r MERGE (x:Recall {id: r.id}) "
         "SET x.number = r.number, x.date = date(r.date), x.title = r.title, x.url = r.url, "
         "    x.description = r.description, x.hazard_text = r.hazard_text, "
         "    x.remedy_text = r.remedy_text, x.injuries = r.injuries, x.sold_at = r.sold_at, "
         "    x.units = r.units, "
         "    x.last_published = CASE WHEN r.last_published IS NULL THEN null ELSE date(r.last_published) END",
         [p["recall"] for p in todo if p["recall"]["date"]])
    todo = [p for p in todo if p["recall"]["date"]]

    def rows(key: str, value_name: str = "v") -> list[dict[str, Any]]:
        return [{"rid": p["recall"]["id"], value_name: v} for p in todo for v in p[key]]

    _run(conn,
         "UNWIND $rows AS r MATCH (x:Recall {id: r.rid}) "
         "CREATE (p:Product {id: r.p.id, name: r.p.name, units: r.p.units, units_text: r.p.units_text}) "
         "CREATE (x)-[:COVERS]->(p)",
         rows("products", "p"))
    _run(conn,
         "UNWIND $rows AS r MATCH (p:Product {id: r.pid}) MERGE (t:ProductType {name: r.type}) "
         "MERGE (p)-[:OF_TYPE]->(t)",
         [{"pid": pr["id"], "type": pr["type"]} for p in todo for pr in p["products"] if pr["type"]])

    _run(conn,
         "UNWIND $rows AS r MERGE (c:Company {key: r.key}) "
         "ON CREATE SET c.name = r.name, c.alias = r.alias, c.location = r.location",
         [c for p in todo for c in p["companies"]])
    for role in sorted(set(ROLES.values()) | {"ANNOUNCED_BY"}):
        _run(conn,
             f"UNWIND $rows AS r MATCH (x:Recall {{id: r.rid}}), (c:Company {{key: r.key}}) "
             f"MERGE (x)-[:{role}]->(c)",
             [{"rid": p["recall"]["id"], "key": c["key"]} for p in todo for c in p["companies"]
              if c["role"] == role])

    for key, label, rel, prop in (("hazards", "Hazard", "HAS_HAZARD", "name"),
                                  ("retailers", "Retailer", "SOLD_AT", "name"),
                                  ("countries", "Country", "MADE_IN", "name"),
                                  ("upcs", "Upc", "HAS_UPC", "code"),
                                  ("remedies", "Remedy", "OFFERS", "name")):
        _run(conn,
             f"UNWIND $rows AS r MATCH (x:Recall {{id: r.rid}}) MERGE (n:{label} {{{prop}: r.v}}) "
             f"MERGE (x)-[:{rel}]->(n)",
             rows(key))

    return {"new": sorted(p["recall"]["id"] for p in new),
            "changed": sorted(p["recall"]["id"] for p in changed), "seen": len(parsed)}


def record_sync(conn: relate.Connection, result: dict[str, Any], mode: str) -> str:
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    through = conn.execute(
        "MATCH (r:Recall) RETURN toString(max(r.last_published)) AS d").single()["d"]
    sid = now.strftime("%Y%m%dT%H%M%SZ")
    conn.execute(
        "CREATE (:Sync {id: $id, source: 'cpsc', mode: $mode, at: $at, through: date($through), "
        "               seen: $seen, new: $new, changed: $changed})",
        {"id": sid, "mode": mode, "at": now.isoformat(), "through": through,
         "seen": result["seen"], "new": len(result["new"]), "changed": len(result["changed"])},
    ).consume()
    return sid


def last_sync(conn: relate.Connection) -> dict[str, Any] | None:
    rows = conn.execute(
        "MATCH (s:Sync {source: 'cpsc'}) RETURN s.id AS id, s.at AS at, toString(s.through) AS through, "
        "s.mode AS mode ORDER BY s.id DESC LIMIT 1").data()
    return rows[0] if rows else None
