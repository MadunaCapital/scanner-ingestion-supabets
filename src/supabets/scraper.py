"""SupaBets ZA adapter.

Reads SupaBets' public, unauthenticated odds feed -- the same JSON API their
own sportsbook single-page app (served at apib2c.supabets.co.za/spa/...)
calls to render the page for any visitor. Plain HTTP GET with no TLS
impersonation, no stealth browser, no Cloudflare bypass, same as Betway ZA
and WSB.

The `x-api-key: H3DigitalAPIB2CWebSiteUser` header is a hardcoded constant
baked into SupaBets' own public JS bundle (H3 Digital is their platform
vendor) and sent identically by every visitor's browser -- it is not a
secret, not session-derived, and not tied to any login flow. It identifies
the *caller application* the same way a User-Agent does, not a credential
that gates access to non-public data. Confirmed unauthenticated: curl with
just this header and no cookies/session returns live odds.

Endpoint discovered by ordinary browsing (Playwright, no stealth plugins) of
the public site. Two wrinkles worth documenting because they cost real time
to work out:

1. SupaBets' main site (supabets.co.za) now redirects to a rebranded
   Next.js casino frontend (new.supabets.co.za) that only teases sports
   ("4 leagues", "8 competitions" cards) and does not itself render an odds
   board. The actual sportsbook is still live, just moved to a plain SPA
   served directly off the API host at
   `https://apib2c.supabets.co.za/spa/sport/<slug>/<country-slug>/<league-slug>`.
   That SPA is what calls the endpoints below.

2. The sport/league taxonomy endpoints are under `/api/b2c/EventsProgram/`
   (e.g. `sports-full` for the sport list, `program?sportId=163` for the
   country/league tree under a sport) -- these only return navigation
   metadata (sport/league names, ids, match counts), never actual matches
   or prices, no matter what's passed. The endpoint that actually returns
   matches + odds is a different, undocumented-looking one:
   `/api/frontend/matches/{sportTypeId}/section/leagues?n=1&league={leagueId}&oddsGroupId={oddsGroupId}`.
   `sportTypeId` is the sport's *type* id (1 for Soccer, distinct from its
   `sportId`/163), `league` is a competition's `eventId` from the program
   tree (e.g. 990625 for the Premier League), and `oddsGroupId` selects
   which market to return (1433 = "1x2" i.e. moneyline -- confirmed
   constant across leagues via `/api/frontend/events/{leagueId}/categories-and-odds-groups`
   for both Premier League and LaLiga).

This matches-endpoint is per-league, not a single bulk "all soccer matches"
call like Betway ZA's Highlights feed or WSB's drilldown-tag feed -- there is
no such bulk endpoint here. Polling every league in SupaBets' full soccer
program (100+ competitions) every cycle would mean 100+ HTTP calls per poll,
so this adapter scopes itself to a fixed list of major leagues (see
DEFAULT_LEAGUE_IDS below), the same kind of deliberate scope narrowing as
Betway ZA's single `sport_id` and WSB's single drilldown tag id. Widen
coverage later by passing a bigger `league_ids` dict into the constructor.

It also only returns matches for the *nearest* match day per league (the
`n` query param does not appear to expand further days -- verified n=1..7
all return the identical single expanded day), so this adapter is
"next matchday per major league", not a multi-day-ahead feed the way WSB's
is. Documented here rather than silently assumed.

Rugby (South Africa's #2 sport after soccer) was added the same way soccer
was originally scoped:

1. `/api/b2c/EventsProgram/sports-full` (same navigation endpoint used to
   confirm soccer's sportTypeId) lists `{"sportId": 197, "sportTypeId": 14,
   "name": "Rugby", "slug": "rugby", "subEventsCount": 30}` -- rugby's
   `sportTypeId` for the matches endpoint is 14 (distinct from its sportId,
   197, same relationship as soccer's 1 vs 163). This one sportTypeId lumps
   *both* rugby codes together on this platform.
2. `/api/b2c/EventsProgram/program?sportId=197` lists the full live rugby
   program: 30 subEvents across two groups -- "Rugby League" (NRL
   Premiership 990812, NRL, Women 991109, Super League 990809) and "Rugby
   Union" (English Premiership 990802, National Provincial Championship
   990884, Top 14 990797, United Rugby Championship 990807). No Currie Cup /
   Rugby Championship / Six Nations test window was live in the program at
   discovery time (2026-09-28) -- this adapter scopes to what's actually
   listed rather than guessing at ids for competitions not currently on the
   board. United Rugby Championship is the most South-Africa-relevant entry
   here since its ids include the four SA franchises (Bulls, Sharks,
   Stormers, Lions).
3. `/api/frontend/events/{leagueId}/categories-and-odds-groups` for all
   seven of the above returned the *same* `idGruppoQuota` 1433 / `"1x2"`
   moneyline group as soccer's Premier League/LaLiga check -- oddsGroupId
   1433 is evidently a platform-wide market id, not soccer-specific, so no
   separate rugby oddsGroupId was needed.
4. A plain curl of `/api/frontend/matches/14/section/leagues?n=1&league=<id>&oddsGroupId=1433`
   for each of the seven leagues (just the `x-api-key` header, no
   cookies/session) returned 200s with real matches and live decimal odds,
   in the *same* response shape soccer uses -- down to reusing the same
   `markets[].id` (79117) and outcome-option ids (911679/911680/911681 for
   "1"/"X"/"2") observed in the soccer capture. Rugby matches do carry a
   priced draw outcome (rare but not impossible, e.g. golden-point draws in
   NRL's regular season) rather than omitting the "X" slot, so no market
   -shape special-casing was needed beyond what the soccer code already does
   (draw_odds stays optional; only home/away are required to publish).

Because rugby reuses the exact same matches-endpoint shape, market id, and
"1"/"X"/"2" label legend as soccer, `to_odds_events` below is unchanged
except for tagging each parsed event with the sport it actually came from
(read off the raw payload, not hardcoded) instead of assuming soccer.

Cricket was added the same way, but its moneyline market turned out to be
genuinely different from soccer/rugby's shared "1x2"/1433 -- verified rather
than assumed, per the discovery discipline above:

1. `/api/b2c/EventsProgram/sports-full` lists `{"sportId": 193,
   "sportTypeId": 9, "name": "Cricket", "slug": "cricket",
   "subEventsCount": 14}` -- cricket's `sportTypeId` for the matches
   endpoint is 9 (distinct from its sportId, 193).
2. `/api/b2c/EventsProgram/program?sportId=193` lists the full live cricket
   program: 5 competitions totalling exactly 14 subEvents (matching
   sports-full's count for sportId 193 precisely) -- T20 South Africa Cup
   (1009161, 7 matches), ODI Series South Africa vs Australia (1539799),
   ODI Series India vs. West Indies (1539912), T20 Asian Games (1542020, 2
   matches) and National Cricket League, Women (1054464, 3 matches). No
   Test-match series was live in the program at discovery time
   (2026-09-28), same "scope to what's actually listed" discipline as
   rugby -- see DEFAULT_CRICKET_LEAGUE_IDS.
3. `/api/frontend/events/{leagueId}/categories-and-odds-groups`, checked
   for all five of the above, did **not** reuse soccer/rugby's 1433/"1x2"
   group at all -- cricket's only moneyline-shaped group is idGruppoQuota
   3764, `gruppoQuota` "Winner (incl. super over)". So, unlike rugby,
   cricket needed its own oddsGroupId (CRICKET_ODDS_GROUP_ID = 3764) and
   its own market name to match on (CRICKET_MARKET_NAME = "Winner (incl.
   super over)", not "1x2") -- confirming this platform's market ids/names
   are not universally shared across sports, so each new sport must still
   be checked rather than assumed to inherit 1433/"1x2".
4. A plain curl of
   `/api/frontend/matches/9/section/leagues?n=1&league=<id>&oddsGroupId=3764`
   for all five leagues (just the `x-api-key` header, no cookies/session)
   returned 200s with real matches and live decimal odds. The market shape
   is **2-way only** in every sampled match (`markets[0].options` is just
   `[{"id": 986175, "name": "1"}, {"id": 986176, "name": "2"}]`, no "X"/draw
   slot at all) -- consistent with all five live competitions being
   limited-overs cricket (T20/ODI), where a match-winner draw isn't a
   possible result the way it is in Test cricket (not currently live on
   this platform). The existing outcome-label-driven mapping and
   already-optional `draw_odds` handle this shape natively: `home_odds`/
   `away_odds` get populated from labels "1"/"2", `draw_odds` simply never
   gets set because no "X"-labelled option exists in the payload's own
   `markets[].options[]` legend -- no new market-shape branching was
   needed, matching the module's existing "verify, don't force a
   structure" approach.

Tennis was added the same way, verified rather than assumed per the same
discipline:

1. `/api/b2c/EventsProgram/sports-full` lists `{"sportId": 167,
   "sportTypeId": 4, "name": "Tennis", "slug": "tennis",
   "subEventsCount": 242}` -- tennis's `sportTypeId` for the matches
   endpoint is 4 (distinct from its sportId, 167).
2. `/api/b2c/EventsProgram/program?sportId=167` lists the full live tennis
   program: 31 events across six groups -- ATP (8: singles+doubles draws
   for Beijing/Chengdu/Hangzhou/Tokyo), WTA (1: Beijing Women Singles),
   Challenger (12: ATP Challenger Tour singles+doubles draws), UTR Men (4)
   and UTR Women (2) (UTR Pro Tennis Tour, a lower rung below the
   Challenger tour), and WTA 125K (4: singles+doubles). Their subEventsCount
   values sum to exactly 242, matching sports-full's count for sportId 167
   precisely -- same "scope to what's actually listed" discipline as rugby
   and cricket, not a hand-picked "major tournaments" subset the way
   soccer's list is (tennis's whole live program here, unlike soccer's
   100+ competitions, is still a bounded, enumerable list, and it rotates
   week to week by nature of the tour calendar rather than being a stable
   set of leagues, so there's nothing stabler to narrow down to) -- see
   DEFAULT_TENNIS_LEAGUE_IDS. No ITF-branded tournament was live in the
   program at discovery time (2026-09-28).
3. `/api/frontend/events/{leagueId}/categories-and-odds-groups`, checked for
   an ATP singles event (990659), an ATP doubles event (990880), and the
   WTA singles event (990595), returned the *same* match-winner group in
   all three: idGruppoQuota 1583, gruppoQuota "Winner" -- a tennis-specific
   group id/name, not soccer/rugby's 1433/"1x2" nor cricket's 3764/"Winner
   (incl. super over)". So tennis needed its own oddsGroupId
   (TENNIS_ODDS_GROUP_ID = 1583) and market name (TENNIS_MARKET_NAME =
   "Winner"), same as cricket needed its own and rugby didn't.
4. A plain curl of `/api/frontend/matches/4/section/leagues?n=1&league=<id>
   &oddsGroupId=1583` for the singles, doubles, and WTA sample leagues above
   (just the `x-api-key` header, no cookies/session) returned 200s with real
   matches and live decimal odds. The market shape is **2-way only**, same
   as cricket's currently-live competitions: `markets[0].options` is just
   `[{"id": 912161, "name": "1"}, {"id": 912162, "name": "2"}]`, no "X"/draw
   slot -- expected and structural for tennis, which has no drawn result at
   all (unlike cricket's Test-match exception). Doubles matches carry the
   exact same two-`participants`-entries shape as singles (each entry's
   `name` is the pair as one string, e.g. "Andreozzi G / Guinard M"), so no
   parsing branch was needed for doubles vs. singles either -- the existing
   `participants[0]`/`participants[1]` join and optional-`draw_odds`
   handling cover tennis natively, confirmed against the real response
   rather than assumed.

Because the market *name* and *group id* now vary by sport (soccer/rugby
share "1x2"/1433; cricket is "Winner (incl. super over)"/3764; tennis is
"Winner"/1583), `fetch_raw_odds` and `to_odds_events` resolve both per-sport
via MARKET_NAME_BY_SPORT / ODDS_GROUP_ID_BY_SPORT (keyed off the same
`sport` tag used for OddsEvent.sport) instead of the single hardcoded
constant used before cricket existed.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import httpx
from ingestion.base_scraper import BaseScraper
from schemas import MarketOdds, OddsEvent

logger = logging.getLogger(__name__)

SUPABETS_MATCHES_URL_TEMPLATE = "https://apib2c.supabets.co.za/api/frontend/matches/{sport_type_id}/section/leagues"

API_KEY_HEADER = {"x-api-key": "H3DigitalAPIB2CWebSiteUser", "Accept": "application/json"}

# Soccer's sportTypeId (distinct from its sportId, 163, which the
# EventsProgram/sports-full taxonomy endpoint uses) -- required by the
# matches/section/leagues path.
SOCCER_SPORT_TYPE_ID = 1

# Rugby's sportTypeId (distinct from its sportId, 197) -- found the same way
# as soccer's, via EventsProgram/sports-full. Covers both rugby codes on
# this platform (rugby union and rugby league both live under sportTypeId
# 14); see module docstring.
RUGBY_SPORT_TYPE_ID = 14

# Cricket's sportTypeId (distinct from its sportId, 193) -- found the same
# way as soccer's/rugby's, via EventsProgram/sports-full. See module
# docstring.
CRICKET_SPORT_TYPE_ID = 9

# Tennis's sportTypeId (distinct from its sportId, 167) -- found the same
# way as soccer's/rugby's/cricket's, via EventsProgram/sports-full. See
# module docstring.
TENNIS_SPORT_TYPE_ID = 4

# "1x2" (moneyline) market group id, confirmed constant across leagues by
# checking /api/frontend/events/{leagueId}/categories-and-odds-groups for
# both Premier League (990625) and LaLiga (990618) -- both returned
# idGruppoQuota 1433 for gruppoQuota "1x2". Also confirmed to be what rugby
# reuses (see module docstring), but NOT what cricket uses -- cricket has
# its own group/name below.
ONE_X_TWO_ODDS_GROUP_ID = 1433
ONE_X_TWO_MARKET_NAME = "1x2"

# Cricket's moneyline-equivalent market group, confirmed via
# categories-and-odds-groups for all of DEFAULT_CRICKET_LEAGUE_IDS -- this
# platform does NOT reuse 1433/"1x2" for cricket the way it does for rugby;
# cricket's only match-winner-shaped group is idGruppoQuota 3764 named
# "Winner (incl. super over)". See module docstring.
CRICKET_ODDS_GROUP_ID = 3764
CRICKET_MARKET_NAME = "Winner (incl. super over)"

# Tennis's moneyline-equivalent market group, confirmed via
# categories-and-odds-groups for an ATP singles, ATP doubles, and WTA
# singles league -- this platform does NOT reuse 1433/"1x2" (soccer/rugby)
# or 3764/"Winner (incl. super over)" (cricket) for tennis either; tennis's
# match-winner group is idGruppoQuota 1583 named "Winner". See module
# docstring.
TENNIS_ODDS_GROUP_ID = 1583
TENNIS_MARKET_NAME = "Winner"

# Per-sport lookup for the oddsGroupId to request and the markets[].name to
# match on when parsing -- soccer and rugby share the platform-wide
# "1x2"/1433 pair, cricket and tennis each have their own (see module
# docstring). Keyed off the same `sport` tag fetch_raw_odds attaches to each
# league and to_odds_events reads back off the payload.
ODDS_GROUP_ID_BY_SPORT: dict[str, int] = {
    "soccer": ONE_X_TWO_ODDS_GROUP_ID,
    "rugby": ONE_X_TWO_ODDS_GROUP_ID,
    "cricket": CRICKET_ODDS_GROUP_ID,
    "tennis": TENNIS_ODDS_GROUP_ID,
}
MARKET_NAME_BY_SPORT: dict[str, str] = {
    "soccer": ONE_X_TWO_MARKET_NAME,
    "rugby": ONE_X_TWO_MARKET_NAME,
    "cricket": CRICKET_MARKET_NAME,
    "tennis": TENNIS_MARKET_NAME,
}

# Outcome label -> universal market field, from the response's own
# `markets[].options[].name` legend ("1"/"X"/"2"), not inferred from
# outcome-id ordering. Cricket's live payloads only ever carry "1"/"2" (no
# "X") since every currently-live competition is limited-overs (T20/ODI),
# where a drawn result isn't possible -- draw_odds simply stays unset for
# those events, the same already-optional handling rugby's rare-but-present
# draw slot exercises the other direction. Tennis's payloads are always
# "1"/"2" only too, structurally rather than incidentally -- a tennis match
# has no drawn result at all. See module docstring.
OUTCOME_LABEL_TO_FIELD = {"1": "home_odds", "X": "draw_odds", "2": "away_odds"}

# Soccer competition eventIds (from /api/b2c/EventsProgram/program?sportId=163
# aka /api/frontend/events/program), name is cosmetic fallback only -- the
# real per-match league name comes from the matches payload's own
# `eventName` field. Deliberately scoped to a handful of major leagues; see
# module docstring for why there's no bulk "all soccer" call to use instead.
DEFAULT_LEAGUE_IDS: dict[int, str] = {
    990625: "Premier League",
    990618: "LaLiga",
    990826: "Serie A",
    990865: "Bundesliga",
    990645: "Ligue 1",
    990677: "UEFA Champions League",
}

# Rugby competition eventIds (from /api/b2c/EventsProgram/program?sportId=197),
# same discovery path as soccer's DEFAULT_LEAGUE_IDS. This is the *entire*
# live rugby program on the platform (30 subEvents total, matching
# sports-full's count for sportId 197) -- not a narrowed-down subset the way
# soccer's list is, since rugby's whole program here is already small. See
# module docstring for why no Currie Cup / Rugby Championship / Six Nations
# ids are included (not listed as live at discovery time).
DEFAULT_RUGBY_LEAGUE_IDS: dict[int, str] = {
    990807: "United Rugby Championship",
    990802: "English Premiership",
    990884: "National Provincial Championship",
    990797: "Top 14",
    990812: "NRL Premiership",
    990809: "Super League",
    991109: "NRL, Women",
}

# Cricket competition eventIds (from /api/b2c/EventsProgram/program?sportId=193),
# same discovery path as soccer's/rugby's default league lists. This is the
# *entire* live cricket program on the platform (5 competitions summing to
# exactly 14 subEvents, matching sports-full's count for sportId 193) -- not
# a narrowed-down subset, since cricket's whole program here is already
# small. See module docstring for why no Test-match series ids are included
# (not listed as live at discovery time).
DEFAULT_CRICKET_LEAGUE_IDS: dict[int, str] = {
    1009161: "T20 South Africa Cup",
    1539799: "ODI Series South Africa vs Australia",
    1539912: "ODI Series India vs. West Indies",
    1542020: "T20 Asian Games",
    1054464: "National Cricket League, Women",
}

# Tennis event/draw eventIds (from /api/b2c/EventsProgram/program?sportId=167),
# same discovery path as soccer's/rugby's/cricket's default league lists.
# This is the *entire* live tennis program on the platform (31 events across
# six groups, summing to exactly 242 subEventsCount, matching sports-full's
# count for sportId 167) -- not a narrowed-down subset, the same "scope to
# what's actually listed" discipline as rugby/cricket rather than soccer's
# hand-picked major-leagues list. Tennis tournaments rotate week to week by
# nature of the tour calendar, so this list is expected to go stale faster
# than soccer/rugby/cricket's; widen/replace it via the `tennis_league_ids`
# constructor param the same way the other sports' lists are overridden. See
# module docstring for why no ITF-branded tournament is included (none was
# live at discovery time).
DEFAULT_TENNIS_LEAGUE_IDS: dict[int, str] = {
    # ATP tour (Beijing/Chengdu/Hangzhou/Tokyo singles + doubles draws)
    990880: "ATP Beijing, China Men Doubles",
    990659: "ATP Beijing, China Men Singles",
    991138: "ATP Chengdu, China Men Doubles",
    991040: "ATP Chengdu, China Men Singles",
    991122: "ATP Hangzhou, China Men Doubles",
    991024: "ATP Hangzhou, China Men Singles",
    991102: "ATP Tokyo, Japan Men Doubles",
    990725: "ATP Tokyo, Japan Men Singles",
    # WTA tour
    990595: "WTA Beijing, China Women Singles",
    # ATP Challenger tour
    1545756: "ATP Challenger Bari, Italy Men Doubles",
    1544826: "ATP Challenger Bari, Italy Men Singles",
    1258922: "ATP Challenger Columbus, USA Men Doubles",
    1258771: "ATP Challenger Columbus, USA Men Singles",
    1008111: "ATP Challenger Curitiba, Brazil Men Doubles",
    1007473: "ATP Challenger Curitiba, Brazil Men Singles",
    1265019: "ATP Challenger Jingshan, China, Men Doubles",
    1264321: "ATP Challenger Jingshan, China, Men Singles",
    990723: "ATP Challenger Mouilleron-Le-Captif, France Men Doubles",
    990700: "ATP Challenger Mouilleron-Le-Captif, France Men Singles",
    1242009: "ATP Challenger Porto 2, Portugal Men Doubles",
    1242376: "ATP Challenger Porto 2, Portugal Men Singles",
    # UTR Pro Tennis Tour
    1253524: "UTR PTT Ithaca Men 01",
    1545530: "UTR PTT Le Cap d Agde Men 02",
    1544869: "UTR PTT Newport Beach Men 23",
    1544897: "UTR PTT Saitama Men 10",
    1544868: "UTR PTT Ithaca Women 01",
    1544867: "UTR PTT Newport Beach Women 23",
    # WTA 125K tour
    1545531: "WTA 125K Adana, Turkiye Women Doubles",
    1542951: "WTA 125K Adana, Turkiye Women Singles",
    1264313: "WTA 125K Jingshan, China Women Doubles",
    1261833: "WTA 125K Jingshan, China Women Singles",
}

# Plain, fixed-interval polling -- same cadence as a normal page refresh,
# not randomized or disguised to look human.
DEFAULT_POLL_INTERVAL_SECONDS = 45


class SupaBetsScraper(BaseScraper):
    bookmaker_id = "supabets"

    def __init__(
        self,
        league_ids: dict[int, str] | None = None,
        sport_type_id: int = SOCCER_SPORT_TYPE_ID,
        rugby_league_ids: dict[int, str] | None = None,
        cricket_league_ids: dict[int, str] | None = None,
        tennis_league_ids: dict[int, str] | None = None,
    ):
        """`league_ids`/`sport_type_id` are the original soccer-only
        constructor params and keep their original meaning: passing
        `league_ids` explicitly scopes this scraper to *just* that single
        sport (soccer by default, or whichever `sport_type_id` is given),
        the same override behavior as before rugby/cricket/tennis support
        existed -- it does not also implicitly pull in rugby, cricket or
        tennis.

        Leave `league_ids`, `rugby_league_ids`, `cricket_league_ids` and
        `tennis_league_ids` all unset (the default) to get the additive
        behavior: soccer's, rugby's, cricket's and tennis's default league
        lists polled together every cycle. Pass `rugby_league_ids`/
        `cricket_league_ids`/`tennis_league_ids` to widen or narrow that
        sport's scope the same way `league_ids` does for soccer.
        """
        self.league_ids = league_ids if league_ids is not None else dict(DEFAULT_LEAGUE_IDS)
        self.sport_type_id = sport_type_id

        if league_ids is not None:
            # Explicit legacy override: single sport, exactly as specified.
            if sport_type_id == RUGBY_SPORT_TYPE_ID:
                sport_name = "rugby"
            elif sport_type_id == CRICKET_SPORT_TYPE_ID:
                sport_name = "cricket"
            elif sport_type_id == TENNIS_SPORT_TYPE_ID:
                sport_name = "tennis"
            else:
                sport_name = "soccer"
            self._sport_scopes: list[tuple[int, str, dict[int, str]]] = [
                (sport_type_id, sport_name, self.league_ids)
            ]
        else:
            self._sport_scopes = [
                (SOCCER_SPORT_TYPE_ID, "soccer", dict(DEFAULT_LEAGUE_IDS)),
                (
                    RUGBY_SPORT_TYPE_ID,
                    "rugby",
                    rugby_league_ids if rugby_league_ids is not None else dict(DEFAULT_RUGBY_LEAGUE_IDS),
                ),
                (
                    CRICKET_SPORT_TYPE_ID,
                    "cricket",
                    cricket_league_ids if cricket_league_ids is not None else dict(DEFAULT_CRICKET_LEAGUE_IDS),
                ),
                (
                    TENNIS_SPORT_TYPE_ID,
                    "tennis",
                    tennis_league_ids if tennis_league_ids is not None else dict(DEFAULT_TENNIS_LEAGUE_IDS),
                ),
            ]

        self._client = httpx.AsyncClient(timeout=10, headers=API_KEY_HEADER)

    async def fetch_raw_odds(self) -> dict:
        """Fetches the nearest-matchday odds for each configured league,
        across every configured sport (soccer and, by default, rugby,
        cricket and tennis).

        Unlike Betway ZA/WSB's single bulk call, SupaBets has no "all
        matches" endpoint -- each league is its own HTTP request. A single
        league's request failing (transient network blip, that one
        competition temporarily unavailable) is caught and logged here
        rather than aborting the whole poll cycle: the other leagues'
        odds -- soccer, rugby, cricket or tennis -- are still worth
        publishing that cycle.

        The oddsGroupId requested is resolved per sport (via
        ODDS_GROUP_ID_BY_SPORT) rather than a single hardcoded constant --
        cricket's and tennis's moneyline-equivalent markets each live under
        their own group id, distinct from soccer/rugby's shared 1433 (see
        module docstring).
        """
        leagues_raw: dict[str, dict] = {}
        for sport_type_id, sport_name, league_ids in self._sport_scopes:
            odds_group_id = ODDS_GROUP_ID_BY_SPORT.get(sport_name, ONE_X_TWO_ODDS_GROUP_ID)
            for league_id, league_name in league_ids.items():
                try:
                    response = await self._client.get(
                        SUPABETS_MATCHES_URL_TEMPLATE.format(sport_type_id=sport_type_id),
                        params={"n": 1, "league": league_id, "oddsGroupId": odds_group_id},
                    )
                    response.raise_for_status()
                    leagues_raw[str(league_id)] = {
                        "league_name": league_name,
                        "payload": response.json(),
                        "sport": sport_name,
                    }
                except httpx.HTTPError as exc:
                    logger.warning(
                        "%s: failed to fetch %s league %s (%s): %s",
                        self.bookmaker_id,
                        sport_name,
                        league_id,
                        league_name,
                        exc,
                    )
                    continue
        return {"leagues": leagues_raw}

    def to_odds_events(self, raw: dict) -> list[OddsEvent]:
        """Maps SupaBets' per-league matches+odds payload onto the universal
        OddsEvent schema, moneyline-equivalent market only for now.

        Each league payload has matches nested under day buckets
        (`events[].matches[]`) and prices in a separate top-level `odds`
        dict keyed by match id -> oddsTypeId -> outcomeId -> handicap ->
        {id, value} -- joined here the same way Betway ZA joins its
        parallel events/markets/outcomes/prices arrays. Outcome labels
        ("1"/"X"/"2", or just "1"/"2" for cricket's currently-live
        limited-overs matches and for tennis, which has no drawn result at
        all) come from the payload's own `markets[].options[]` legend, not
        inferred from outcome-id sort order. Rugby's odds payloads use this
        exact same shape and market/label legend as soccer's (verified live
        for all of DEFAULT_RUGBY_LEAGUE_IDS -- see module docstring), so no
        sport-specific parsing branch is needed for rugby, only the `sport`
        tag on the resulting OddsEvent. Cricket's and tennis's payloads use
        the same shape too, but each with its own market name/id (see
        MARKET_NAME_BY_SPORT and the module docstring) -- resolved here
        per-sport rather than assumed. Tennis's `participants` entries (one
        per side, singles or doubles alike -- a doubles entry's `name` is
        the pair as one string, e.g. "Cash R / Erler A") also fit the
        existing two-participant join without any sport-specific branching.

        Note event_id (the OddsEvent field) is left unset here -- that's
        the engine's job downstream (see the note on OddsEvent.event_id in
        scanner-schemas).
        """
        scraped_at = datetime.now(timezone.utc)
        odds_events: list[OddsEvent] = []

        for league_id, league_data in raw.get("leagues", {}).items():
            payload = league_data.get("payload") or {}
            league_name = league_data.get("league_name", "unknown")
            # Defaults to "soccer" for back-compat with raw payloads built
            # before rugby/cricket support existed (and by any caller/test
            # that constructs a leagues dict without a "sport" key).
            sport = league_data.get("sport", "soccer")
            market_name = MARKET_NAME_BY_SPORT.get(sport, ONE_X_TWO_MARKET_NAME)

            # Every step below is defensive against a single malformed
            # record (a match/market/odds entry missing an expected field):
            # skip and log just that record/league rather than let one bad
            # entry take down parsing of every other league in the batch.

            try:
                market_def = next(
                    (m for m in payload.get("markets", []) if m.get("name") == market_name), None
                )
                if market_def is None:
                    continue
                outcome_label_by_id = {opt["id"]: opt["name"] for opt in market_def.get("options", [])}
            except (KeyError, TypeError, AttributeError) as exc:
                logger.warning("Skipping league %s (%s), malformed markets legend: %s", league_id, league_name, exc)
                continue

            matches_by_id: dict[int, dict] = {}
            for day in payload.get("events", []) or []:
                for match in day.get("matches", []) or []:
                    try:
                        matches_by_id[match["id"]] = match
                    except (KeyError, TypeError):
                        logger.warning("Skipping match missing id in league %s: %r", league_id, match)

            for match_id_str, outcome_groups in (payload.get("odds") or {}).items():
                try:
                    match_id = int(match_id_str)
                except (TypeError, ValueError):
                    logger.warning("Skipping odds entry with non-integer match id: %r", match_id_str)
                    continue

                match = matches_by_id.get(match_id)
                if match is None:
                    continue

                try:
                    participants = match.get("participants", [])
                    if len(participants) < 2:
                        continue
                    home_team = participants[0]["name"]
                    away_team = participants[1]["name"]
                    start_time = datetime.fromisoformat(match["start"].replace("Z", "+00:00"))

                    prices: dict[str, float] = {}
                    for outcomes in (outcome_groups or {}).values():
                        for outcome_id_str, handicaps in (outcomes or {}).items():
                            label = outcome_label_by_id.get(int(outcome_id_str))
                            if label is None or not handicaps:
                                continue
                            price_entry = next(iter(handicaps.values()))
                            value = price_entry.get("value")
                            field = OUTCOME_LABEL_TO_FIELD.get(label)
                            if field is not None and value is not None:
                                prices[field] = value

                    home_odds = prices.get("home_odds")
                    away_odds = prices.get("away_odds")
                    draw_odds = prices.get("draw_odds")
                    if home_odds is None or away_odds is None:
                        continue  # incomplete market, don't publish a partial price

                    odds_events.append(
                        OddsEvent(
                            sport=sport,
                            league=match.get("eventName") or league_name,
                            home_team=home_team,
                            away_team=away_team,
                            start_time=start_time,
                            bookmaker=self.bookmaker_id,
                            markets={
                                "moneyline": MarketOdds(home_odds=home_odds, away_odds=away_odds, draw_odds=draw_odds)
                            },
                            scraped_at=scraped_at,
                        )
                    )
                except (KeyError, TypeError, ValueError, AttributeError) as exc:
                    logger.warning("Skipping malformed match %s in league %s: %s", match_id, league_id, exc)
                    continue

        return odds_events

    async def poll(
        self, interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS
    ) -> AsyncIterator[list[OddsEvent]]:
        """Fetches odds on a fixed interval and yields the parsed events each
        time. A transient fetch failure (network blip, momentary 5xx, or a
        non-JSON error page served with a 200 status) is logged and the loop
        continues on schedule rather than crashing -- this loop is meant to
        run unattended for the life of the process.
        """
        while True:
            try:
                raw = await self.fetch_raw_odds()
                yield self.to_odds_events(raw)
            except httpx.HTTPError as exc:
                logger.warning("%s: poll fetch failed: %s", self.bookmaker_id, exc)
            except Exception:
                logger.exception("%s: unexpected error in poll cycle", self.bookmaker_id)

            await asyncio.sleep(interval_seconds)

    async def close(self) -> None:
        await self._client.aclose()
