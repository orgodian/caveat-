# Caveat

Screen a product against the public recall record before you buy, sell or recommend it,
and get told when a later recall reaches something you already recommended.

> Work in progress for the Relate by Neo4j hackathon. This README is an interim draft;
> see docs/PLAN.md for status.

## Use case

Resellers and marketplace sellers may not sell recalled products, and a shopping agent
should not recommend one. The U.S. Consumer Product Safety Commission publishes every
recall, but as free text: the same firm appears under several spellings, the importer
and the brand are different companies, and a recall published next week says nothing
about what you recommended last week.

The relationships that matter: which firms stand behind a recalled product (maker,
importer, distributor), what else those firms have had recalled, which hazards recur,
and which past recommendations a new recall touches.

## How it uses Relate

- Mode: single file (`store/caveat.rl`)
- Why this mode: the whole public record, plus your own ledger of decisions, is one
  file you can copy, commit or hand to an agent. Readers open it read-only; the sync
  is the only writer.
- What the graph models: recalls, the products they cover, the companies in each role,
  hazards, retailers, countries, UPCs and remedies, plus the items you recommended,
  the decisions that recommended them, and the recalls that reached them later.

Full-text indexes on product names, recall titles and company names find candidates;
graph traversals then gather the firms, hazards and decisions around each one.

## Setup and run

Requires Python 3.9 or later.

    pip install -e .
    caveat sync --offline          # builds store/caveat.rl from the cached records, about 6 seconds
    caveat screen "Rock 'n Play Sleeper" --brand "Fisher-Price"
    caveat firm "Polaris"
    caveat scan my-stock.csv       # columns: name, brand, upc

`caveat sync` (without `--offline`) fetches what CPSC has published since the last sync.

## Where the data lives

- `store/caveat.rl`: the Relate store
- `data/raw/cpsc/`: the cached CPSC responses the store is built from

## Limits

Matches are against U.S. CPSC recall records only. Name matching can miss or
over-match; every match shows its reasons and links to the CPSC notice. No match
means no match in that record as of the last sync. It is not a safety assessment.

## How AI was used

The code in this repository was written by Claude (Anthropic) working under the
direction of the author, who chose the problem and reviewed the results.
