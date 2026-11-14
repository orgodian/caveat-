"""The ledger: what was recommended, what the record said then, what changed since.

  (Decision)-[:RECOMMENDED]->(Item)       an agent or a person recommended an item
  (Decision)-[:SAW {tier}]->(Recall)      recalls that already matched at that moment
  (Recall)-[:AFFECTS {tier, found}]->(Item)  a recall that matched only later

Because decisions live in the same graph as the recalls, "which of my past
recommendations does this week's recall touch?" is one traversal after a sync.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from typing import Any

import relate

from . import match, text

ALERT_TIERS = ("EXACT", "LIKELY", "POSSIBLE")


def item_id(name: str, brand: str | None, upc: str | None) -> str:
    basis = "|".join([" ".join(text.tokens(name)), text.company_key(brand or ""), text.upc(upc)])
    return hashlib.sha1(basis.encode()).hexdigest()[:12]


def record(conn: relate.Connection, name: str, brand: str | None = None, upc: str | None = None,
           agent: str = "cli", note: str = "") -> dict[str, Any]:
    """Screen an item now and write the decision, with what the record showed."""
    result = match.screen(conn, name, brand=brand, upc=upc, limit=50)
    seen = [m for m in result["matches"] if m["tier"] in ALERT_TIERS]
    n = conn.execute("MATCH (d:Decision) RETURN count(d) AS n").single()["n"]
    did = f"D-{n + 1:04d}"
    iid = item_id(name, brand, upc)
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    conn.execute(
        "MERGE (i:Item {id: $iid}) ON CREATE SET i.name = $name, i.brand = $brand, i.upc = $upc "
        "CREATE (d:Decision {id: $did, at: $at, agent: $agent, note: $note, "
        "                    synced_through: date($through), status: $status}) "
        "CREATE (d)-[:RECOMMENDED]->(i)",
        {"iid": iid, "name": name, "brand": brand or "", "upc": text.upc(upc), "did": did, "at": now,
         "agent": agent, "note": note, "through": result["synced_through"],
         "status": "FLAGGED" if seen else "CLEAR"},
    ).consume()
    conn.execute(
        "UNWIND $rows AS r MATCH (d:Decision {id: $did}), (x:Recall {id: r.id}) "
        "CREATE (d)-[:SAW {tier: r.tier, score: r.score}]->(x)",
        {"did": did, "rows": [{"id": m["recall"]["id"], "tier": m["tier"], "score": m["score"]}
                              for m in seen]},
    ).consume()
    return {"decision": did, "item": iid, "status": "FLAGGED" if seen else "CLEAR",
            "synced_through": result["synced_through"], "matches": seen[:5]}


def refresh_alerts(conn: relate.Connection) -> list[dict[str, Any]]:
    """Re-screen every recommended item; link recalls that match now but were not seen then."""
    items = conn.execute(
        "MATCH (i:Item)<-[:RECOMMENDED]-(d:Decision) "
        "OPTIONAL MATCH (d)-[:SAW]->(s:Recall) "
        "OPTIONAL MATCH (a:Recall)-[:AFFECTS]->(i) "
        "RETURN i.id AS id, i.name AS name, i.brand AS brand, i.upc AS upc, "
        "       collect(DISTINCT s.id) + collect(DISTINCT a.id) AS known").data()
    today = dt.date.today().isoformat()
    created = []
    for it in items:
        result = match.screen(conn, it["name"], brand=it["brand"] or None, upc=it["upc"] or None, limit=50)
        for m in result["matches"]:
            if m["tier"] in ALERT_TIERS and m["recall"]["id"] not in it["known"]:
                conn.execute(
                    "MATCH (x:Recall {id: $rid}), (i:Item {id: $iid}) "
                    "MERGE (x)-[a:AFFECTS]->(i) SET a.tier = $tier, a.score = $score, a.found = date($found)",
                    {"rid": m["recall"]["id"], "iid": it["id"], "tier": m["tier"],
                     "score": m["score"], "found": today}).consume()
                created.append({"item": it["name"], "tier": m["tier"], "recall": m["recall"]})
    return created


def alerts(conn: relate.Connection) -> list[dict[str, Any]]:
    """Recalls that reached an already-recommended item, with the decisions they touch."""
    return conn.execute(
        "MATCH (x:Recall)-[a:AFFECTS]->(i:Item)<-[:RECOMMENDED]-(d:Decision) "
        "OPTIONAL MATCH (x)-[:HAS_HAZARD]->(h:Hazard) "
        "WITH x, a, i, d, collect(DISTINCT h.name) AS hazards "
        "RETURN i.name AS item, i.brand AS brand, a.tier AS tier, toString(a.found) AS found, "
        "       x.id AS recall_id, toString(x.date) AS recall_date, x.title AS title, x.url AS url, "
        "       hazards, collect({id: d.id, at: d.at, agent: d.agent, note: d.note}) AS decisions "
        "ORDER BY recall_date DESC, item").data()


def decisions(conn: relate.Connection) -> list[dict[str, Any]]:
    return conn.execute(
        "MATCH (d:Decision)-[:RECOMMENDED]->(i:Item) "
        "OPTIONAL MATCH (d)-[:SAW]->(s:Recall) "
        "OPTIONAL MATCH (a:Recall)-[:AFFECTS]->(i) "
        "RETURN d.id AS id, d.at AS at, d.agent AS agent, d.note AS note, d.status AS status, "
        "       toString(d.synced_through) AS synced_through, i.name AS item, i.brand AS brand, "
        "       count(DISTINCT s) AS seen_then, count(DISTINCT a) AS alerts_since "
        "ORDER BY id").data()
