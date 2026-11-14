# Caveat: plan for the Relate by Neo4j hackathon

Deadline: 11 December 2026, 6:00pm Pacific (12 December, 3:00am Lagos).
Status on 14 November: core product works on real data. See "Done" and "Next".

## What the judges score

From the hackathon page, out of 100:

| Criterion | Points | What it means for us |
|---|---|---|
| Relate and connected context | 30 | The graph must do work a table could not, and judges must be able to run it and explore the store easily |
| Functionality and completeness | 25 | The core use works end to end, on first try |
| Usefulness | 20 | A named user with a real problem, shown clearly |
| Documentation and reproducibility | 15 | README in their template; first result in a few commands |
| Creativity | 10 | A fresh idea or an inventive use of Relate |

Hard requirements: public GitHub repo tagged `relate`; a persistent store in the repo;
Relate actually running; a demo video of 1 to 2 minutes; new work started on or after
11 November; AI tool use disclosed.

The eight judges are Neo4j product, engineering and developer-relations staff. One of
them, Michael Hunger, keeps a public list of about 250 project ideas. Its stated
principle is "Take a real thing, make it a graph, put the graph where the questions
are." Ideas on that list that this project answers directly: "Conflict-of-Interest
Finder: join two public registers and query the overlap", "Company Ownership Rings",
"The Judgment Ledger", "Graph-Native Agent Memory".

## What other entrants will likely build

Neo4j's own launch material suggests agent memory, codebase context for coding agents,
agent task coordination and knowledge-base exploration, and its launch demo is a
dependency audit. Expect most entries to be agent-memory wrappers and code graphs,
many on invented or model-generated data. On 14 November only one repo carried the
`relate-neo4j-hackathon` topic.

## How this entry stands out

1. Real public data at real size: every CPSC recall since 1973 (9,669 loaded), not a sample.
2. A real user with a legal reason to care: resellers and marketplace sellers, who may
   not sell recalled products, and shopping agents that recommend products.
3. The agent's own decisions live in the same graph as the evidence, so a recall
   published later finds the recommendations it touches. Nothing else in the field
   suggestions does this.
4. Every match explains itself and links to the government source.
5. Measured, not asserted: a matching-quality evaluation with a baseline (see Next).
6. Runs in one command from a store shipped in the repo, offline.

## The product

Caveat screens a product against the public recall record before you buy, sell or
recommend it, and tells you later if a new recall reaches something you already
recommended.

- `caveat screen "<product>" --brand <brand>`: recalls that match, with a tier and reasons
- `caveat scan inventory.csv`: screen a stock list
- `caveat firm "<name>"`: a firm's whole recall history, pooled across name spellings
- `caveat recommend ...`: record a recommendation and what the record showed that day
- `caveat sync`: fetch only what CPSC published since last time, then raise alerts
- `caveat alerts`: recalls that reached items after they were recommended

## Data sources

| Source | Status | Notes |
|---|---|---|
| CPSC Recalls REST service | In use, tested live | No key. Full history and incremental fetch both work |
| Wikidata (brand ownership) | Planned | Endpoint reachable. GLEIF was tested and rejected: consumer brands such as Fisher-Price have no legal-entity record there |
| openFDA enforcement | Candidate | Endpoint reachable; would add food, drug and device recalls |

## Graph model

    (Recall)-[:COVERS]->(Product)-[:OF_TYPE]->(ProductType)
    (Recall)-[:MANUFACTURED_BY|IMPORTED_BY|DISTRIBUTED_BY|ANNOUNCED_BY]->(Company)
    (Recall)-[:HAS_HAZARD]->(Hazard)     (Recall)-[:SOLD_AT]->(Retailer)
    (Recall)-[:MADE_IN]->(Country)       (Recall)-[:HAS_UPC]->(Upc)
    (Recall)-[:OFFERS]->(Remedy)
    (Decision)-[:RECOMMENDED]->(Item)    (Decision)-[:SAW {tier}]->(Recall)
    (Recall)-[:AFFECTS {tier, found}]->(Item)
    (Sync) one node per fetch, for "what did we know then?"

Relate features in use: single-file store, uniqueness constraints, three full-text
indexes feeding graph traversals, read-only connections for readers, one writer.

## Done (verified on 14 November)

- Full CPSC history fetched and loaded: 9,669 recalls, 11,105 products, 10,654 company
  names, 47 MB single-file store, built in about 6 seconds from the cached files.
- Incremental sync tested against the live service.
- Matching with tiers and reasons; firm history; CSV scan; ledger; alerts.
- Replay test on real records: a recommendation made on the record as of 1 November
  is flagged by the recall CPSC published on 12 November.
- 11 automated tests, all on real records.

## Next, in order

1. Matching evaluation with a baseline, then tune thresholds on a held-out split.
   Known weakness: a generic description with no brand ("magnetic balls 5mm 216
   pieces") is rated LIKELY against one seller's recall; it should be POSSIBLE.
2. MCP server so any agent can call screen, firm, recommend and alerts.
3. Brand ownership from Wikidata, so sibling brands under one parent share a record.
   Entity matching must show its confidence; a wrong link is a false statement about
   a real company.
4. README in the hackathon template, AI disclosure, data notes.
5. Demo video script (under 2 minutes) built on the replay.
6. Stretch: in-browser explorer using Relate's WebAssembly build, so judges can try
   it with no install.

## Risks

- Name matching can miss or over-match. Mitigation: tiers, reasons, source links,
  the evaluation, and plain wording that no match is not a safety finding.
- The 47 MB store is near GitHub's 50 MB warning. Mitigation: ship the 6 MB cache
  as well, so `caveat sync --offline` rebuilds the store in seconds.
- CPSC covers U.S. consumer products only. Stated in every output.
