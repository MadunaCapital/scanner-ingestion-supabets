# scanner-ingestion-supabets

SupaBets ZA odds scraper for the MadunaCapital arbitrage scanner. One of several independent per-bookmaker repos, kept maximally decoupled from the others: a bug, dependency bump, or bad release here can't touch any other bookmaker's scraper.

## How it works

Reads SupaBets' public, unauthenticated matches+odds JSON API — the same endpoint their own sportsbook single-page app (served at `apib2c.supabets.co.za/spa/...`) calls to render the odds board for any visitor. Plain `httpx` GET, no TLS impersonation, no stealth browser, no Cloudflare bypass: none of that is needed because this endpoint isn't behind bot detection. Polls on a plain, fixed 45-second interval by default — no jitter, no randomization to look human.

The `x-api-key: H3DigitalAPIB2CWebSiteUser` header sent on every request is a hardcoded constant baked into SupaBets' own public JS bundle, sent identically by every visitor's browser. It's not a secret, not session-derived, and not gated behind any login flow — confirmed by curling the endpoint with just this header and no cookies and getting live odds back. It identifies the calling application the same way a `User-Agent` string does, not a credential.

Endpoint discovered by ordinary browsing (Playwright, no stealth plugins) of the public site. See `src/supabets/scraper.py`'s module docstring for two non-obvious things worth reading before changing this adapter:

1. SupaBets' main site now redirects to a rebranded Next.js casino frontend that doesn't itself render sports odds — the real sportsbook SPA and its API live at `apib2c.supabets.co.za`.
2. The `EventsProgram/sports-full` and `EventsProgram/program?sportId=…` endpoints only ever return sport/league navigation metadata, never matches or prices — the endpoint that actually returns odds is `/api/frontend/matches/{sportTypeId}/section/leagues`, and it's per-league (no bulk "all soccer matches" call exists).

Because of that per-league constraint, this adapter polls a fixed set of major soccer leagues (`DEFAULT_LEAGUE_IDS` in `scraper.py`) rather than SupaBets' full ~35-competition soccer program, and returns only the nearest match day per league (the API doesn't appear to expand further days). Both are documented scope choices, not oversights — widen `league_ids` via the constructor to cover more.

Depends on:
- [scanner-ingestion](https://github.com/MadunaCapital/scanner-ingestion) (base install only, no `stealth` extra — this adapter doesn't need it) for `BaseScraper`
- [scanner-schemas](https://github.com/MadunaCapital/scanner-schemas) for `OddsEvent`/`MarketOdds`

Both pulled in as git dependencies in `requirements.txt`, same pattern as every other repo in this project.

## Status

Working and verified live: a plain `curl` with just the `x-api-key` header against `https://apib2c.supabets.co.za/api/frontend/matches/1/section/leagues?n=1&league=990625&oddsGroupId=1433` returns real current Premier League matches and sane decimal odds. Tests are fixtured from that real captured response (no live network in tests) — 10 passing, covering the join across the payload's day-bucketed matches / top-level odds dict / markets outcome-label legend, the "1"/"X"/"2" label-driven mapping (not id-order), edge cases (missing market legend, incomplete prices, malformed match/timestamp), multi-league independence, a single league's request failing without losing the others, and the polling loop (fixed interval, continues past transient fetch failures).

## Local dev

```
pip install -r requirements.txt
pytest
```

## Scope note

This adapter intentionally only reads what SupaBets' own frontend already fetches publicly, at a reasonable polling interval — no authentication bypass, no anti-bot evasion. The `x-api-key` value is a public, non-secret application identifier (see above), not a credential being defeated. Terms of Service exposure for scraping public data is a real but different (lower-severity, contractual rather than computer-misuse) question than the Cybercrimes Act question that applies to defeating security measures.
