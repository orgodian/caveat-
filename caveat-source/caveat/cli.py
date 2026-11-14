"""Command line for Caveat. Run `caveat --help`."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
from typing import Any

from . import DEFAULT_STORE, __version__, cpsc, ledger, match
from .store import open_store, store_path

CACHE_DIR = "data/raw/cpsc"


def _emit(args, payload: Any) -> bool:
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return True
    return False


def _print_match(m: dict[str, Any], indent: str = "  ") -> None:
    r = m["recall"]
    print(f"{indent}{m['tier']:8} {r['date']}  {r['title']}")
    print(f"{indent}         hazards: {', '.join(r['hazards']) or 'not classified'}"
          + (f"; units: {r['units']:,}" if r["units"] else "")
          + (f"; remedy: {', '.join(r['remedies'])}" if r["remedies"] else ""))
    firms = "; ".join(sorted({f"{f['name']} ({f['role'].replace('_', ' ').lower()})" for f in m["firms"]}))
    if firms:
        print(f"{indent}         firms: {firms}")
    print(f"{indent}         why: {'; '.join(m['why'])}")
    print(f"{indent}         {r['url']}")


def cmd_sync(args) -> int:
    target = store_path(args.store)
    fresh = not os.path.exists(target)
    if args.offline:
        records, mode = cpsc.cache_read(args.cache_dir), "offline"
        if not records:
            print(f"No cached records in {args.cache_dir}. Run `caveat sync` online first.", file=sys.stderr)
            return 1
    elif fresh or args.full:
        records, mode = [], "full"
        for year in range(cpsc.FIRST_YEAR, dt.date.today().year + 1):
            got = cpsc.fetch_year(year)
            cpsc.cache_write(args.cache_dir, str(year), got)
            records += got
            print(f"  {year}: {len(got)} recalls", file=sys.stderr)
    else:
        with open_store(target, read_only=True) as conn:
            last = cpsc.last_sync(conn)
        since = last["through"] if last else f"{cpsc.FIRST_YEAR}-01-01"
        records, mode = cpsc.fetch_published_since(since), "incremental"
        cpsc.cache_write(args.cache_dir, f"since-{since}-{dt.date.today().isoformat()}", records)
    if args.through:
        dt.date.fromisoformat(args.through)
        records = [r for r in records if (r.get("LastPublishDate") or r.get("RecallDate") or "")[:10] <= args.through]
    with open_store(target) as conn:
        result = cpsc.load(conn, records)
        sync_id = cpsc.record_sync(conn, result, mode)
        new_alerts = ledger.refresh_alerts(conn)
    payload = {"store": target, "mode": mode, "sync": sync_id, "seen": result["seen"],
               "new": len(result["new"]), "changed": len(result["changed"]), "alerts": new_alerts}
    if not _emit(args, payload):
        print(f"{mode} sync into {target}: {result['seen']} records read, "
              f"{len(result['new'])} new, {len(result['changed'])} changed")
        if new_alerts:
            print(f"{len(new_alerts)} new alert(s) on items already recommended. See: caveat alerts")
    return 0


def cmd_stats(args) -> int:
    with open_store(args.store, read_only=True) as conn:
        labels = conn.execute("MATCH (n) UNWIND labels(n) AS l RETURN l AS label, count(*) AS n ORDER BY n DESC").data()
        rels = conn.execute("MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS n ORDER BY n DESC").data()
        span = conn.execute("MATCH (r:Recall) RETURN toString(min(r.date)) AS first, toString(max(r.date)) AS last, "
                            "toString(max(r.last_published)) AS through").single()
    payload = {"store": store_path(args.store), "labels": labels, "relationships": rels, **span}
    if not _emit(args, payload):
        print(f"{payload['store']}: recalls dated {span['first']} to {span['last']}, "
              f"published through {span['through']}")
        print("Nodes:         " + ", ".join(f"{r['label']} {r['n']:,}" for r in labels))
        print("Relationships: " + ", ".join(f"{r['type']} {r['n']:,}" for r in rels))
    return 0


def cmd_screen(args) -> int:
    with open_store(args.store, read_only=True) as conn:
        res = match.screen(conn, args.name, brand=args.brand, upc=args.upc, limit=args.limit)
    if _emit(args, res):
        return 0
    label = args.name + (f" (brand: {args.brand})" if args.brand else "")
    print(f"{label}: record checked through {res['synced_through']}")
    if not res["matches"]:
        print("  No matching recall in the CPSC record.")
    for m in res["matches"]:
        _print_match(m)
    if res["match_count"] > len(res["matches"]):
        print(f"  ... {res['match_count'] - len(res['matches'])} more (use --limit)")
    if res["firm"]:
        _print_firm(res["firm"], brief=True)
    print(f"\n{res['note']}")
    return 0


def _print_firm(f: dict[str, Any], brief: bool = False) -> None:
    top = ", ".join(f"{k} {v}" for k, v in list(f["hazards"].items())[:5])
    print(f"\n  Firm record for '{f['query']}': {f['recalls']} recalls, {f['first']} to {f['latest']}"
          + (f", {f['units']:,} units" if f["units"] else ""))
    print(f"    names pooled: {'; '.join(f['names_pooled'])}")
    print(f"    hazards: {top}")
    if not brief:
        for r in f["recent"]:
            print(f"    {r['date']}  {r['title']}\n               {r['url']}")


def cmd_firm(args) -> int:
    with open_store(args.store, read_only=True) as conn:
        rec = match.firm_record(conn, args.name, recent=args.limit)
    if _emit(args, rec):
        return 0
    if not rec:
        print(f"No firm named '{args.name}' appears on a recall in the record.")
        return 0
    _print_firm(rec)
    return 0


def cmd_scan(args) -> int:
    with open(args.file, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    if not rows or "name" not in rows[0]:
        print("The CSV needs a header row with a 'name' column (optional: brand, upc).", file=sys.stderr)
        return 1
    out = []
    with open_store(args.store, read_only=True) as conn:
        for row in rows:
            res = match.screen(conn, row["name"], brand=row.get("brand") or None,
                               upc=row.get("upc") or None, limit=1)
            best = res["matches"][0] if res["matches"] else None
            out.append({"name": row["name"], "brand": row.get("brand") or "", "upc": row.get("upc") or "",
                        "tier": best["tier"] if best else "",
                        "recall_date": best["recall"]["date"] if best else "",
                        "recall_title": best["recall"]["title"] if best else "",
                        "hazards": ", ".join(best["recall"]["hazards"]) if best else "",
                        "why": "; ".join(best["why"]) if best else "",
                        "url": best["recall"]["url"] if best else ""})
        through = match.synced_through(conn)
    if args.out:
        with open(args.out, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(out[0].keys()))
            writer.writeheader()
            writer.writerows(out)
    if _emit(args, out):
        return 0
    flagged = [o for o in out if o["tier"]]
    print(f"{len(out)} items screened against the CPSC record through {through}; {len(flagged)} flagged")
    for o in flagged:
        print(f"  {o['tier']:8} {o['name']}" + (f" ({o['brand']})" if o["brand"] else ""))
        print(f"           {o['recall_date']}  {o['recall_title']}\n           {o['url']}")
    if args.out:
        print(f"Full results written to {args.out}")
    print(f"\n{match.NOTE}")
    return 0


def cmd_recommend(args) -> int:
    with open_store(args.store) as conn:
        rec = ledger.record(conn, args.name, brand=args.brand, upc=args.upc, agent=args.agent, note=args.note)
    if _emit(args, rec):
        return 0
    print(f"{rec['decision']}: {args.name} recorded as {rec['status']} "
          f"(record checked through {rec['synced_through']})")
    for m in rec["matches"]:
        _print_match(m)
    return 0


def cmd_alerts(args) -> int:
    with open_store(args.store, read_only=True) as conn:
        rows = ledger.alerts(conn)
    if _emit(args, rows):
        return 0
    if not rows:
        print("No recall has reached an item you already recommended.")
        return 0
    for a in rows:
        ds = ", ".join(f"{d['id']} ({d['at'][:10]}, {d['agent']})" for d in a["decisions"])
        print(f"{a['tier']:8} {a['item']}" + (f" ({a['brand']})" if a["brand"] else ""))
        print(f"         recalled {a['recall_date']}: {a['title']}")
        print(f"         hazards: {', '.join(a['hazards']) or 'not classified'}; touches {ds}")
        print(f"         {a['url']}")
    return 0


def cmd_ledger(args) -> int:
    with open_store(args.store, read_only=True) as conn:
        rows = ledger.decisions(conn)
    if _emit(args, rows):
        return 0
    if not rows:
        print("No decisions recorded. Record one with: caveat recommend \"<product>\" --brand <brand>")
    for d in rows:
        print(f"{d['id']} {d['at'][:10]} {d['status']:8} {d['item']}"
              + (f" ({d['brand']})" if d["brand"] else "")
              + f"  [checked through {d['synced_through']}; matched then: {d['seen_then']}; "
                f"alerts since: {d['alerts_since']}]")
    return 0


def cmd_query(args) -> int:
    with open_store(args.store, read_only=True) as conn:
        rows = conn.execute(args.cypher).data()
    print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--store", help=f"path to the Relate store (default {DEFAULT_STORE}, or $CAVEAT_STORE)")
    common.add_argument("--json", action="store_true", help="print JSON instead of text")

    p = argparse.ArgumentParser(prog="caveat", description=__doc__)
    p.add_argument("--version", action="version", version=f"caveat {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, fn, help_):
        sp = sub.add_parser(name, parents=[common], help=help_)
        sp.set_defaults(fn=fn)
        return sp

    s = add("sync", cmd_sync, "fetch CPSC recalls into the store (full the first time, then only what is new)")
    s.add_argument("--full", action="store_true", help="refetch every year")
    s.add_argument("--offline", action="store_true", help="rebuild from the cached files, no network")
    s.add_argument("--through", help="load only records published on or before this date (replay)")
    s.add_argument("--cache-dir", default=CACHE_DIR)

    add("stats", cmd_stats, "what is in the store")

    sc = add("screen", cmd_screen, "check one product against the recall record")
    sc.add_argument("name")
    sc.add_argument("--brand")
    sc.add_argument("--upc")
    sc.add_argument("--limit", type=int, default=5)

    f = add("firm", cmd_firm, "a firm's whole recall history, pooled across spellings of its name")
    f.add_argument("name")
    f.add_argument("--limit", type=int, default=8)

    sn = add("scan", cmd_scan, "screen a CSV of items (columns: name, brand, upc)")
    sn.add_argument("file")
    sn.add_argument("--out", help="write full results to this CSV")

    r = add("recommend", cmd_recommend, "record that an item was recommended, with what the record showed")
    r.add_argument("name")
    r.add_argument("--brand")
    r.add_argument("--upc")
    r.add_argument("--agent", default="cli")
    r.add_argument("--note", default="")

    add("alerts", cmd_alerts, "recalls that reached items after they were recommended")
    add("ledger", cmd_ledger, "every recorded decision")

    q = add("query", cmd_query, "run a read-only Cypher query against the store")
    q.add_argument("cypher")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (ValueError, ConnectionError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
