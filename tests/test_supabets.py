import pytest

from supabets import SupaBetsScraper

# Shape captured from a real, plain GET (x-api-key header only, no session/
# cookies) to SupaBets' public matches endpoint:
#   GET https://apib2c.supabets.co.za/api/frontend/matches/1/section/leagues
#       ?n=1&league=990625&oddsGroupId=1433
# (990625 = Premier League's competition eventId, 1433 = the "1x2"
# moneyline market group id -- see scraper.py's docstring for how both were
# found). Trimmed to two matches' worth of records; field names, ids and
# decimal odds unchanged from the real response.
SAMPLE_MATCHES_PAYLOAD = {
    "events": [
        {
            "id": "639271872000000000",
            "name": "10 October 2026",
            "matchCount": 6,
            "order": 0,
            "sportType": None,
            "matches": [
                {
                    "id": 201775462,
                    "eventName": "Premier League",
                    "participants": [{"name": "Arsenal FC"}, {"name": "Leeds United"}],
                    "slug": "spa/sport/soccer/england/premier-league/201775462-arsenal-fc-leeds-united",
                    "start": "2026-10-10T11:30:00Z",
                    "statisticsCode": "72221292",
                    "oddsCount": 763,
                    "odds": None,
                },
                {
                    "id": 201775536,
                    "eventName": "Premier League",
                    "participants": [{"name": "Aston Villa"}, {"name": "Brentford FC"}],
                    "slug": "spa/sport/soccer/england/premier-league/201775536-aston-villa-brentford-fc",
                    "start": "2026-10-10T14:00:00Z",
                    "statisticsCode": "72221294",
                    "oddsCount": 771,
                    "odds": None,
                },
            ],
        },
        # Later day buckets in the real response only carry matchCount, no
        # expanded `matches` array -- SupaBets' matches endpoint only ever
        # expands the nearest match day (see scraper.py docstring).
        {"id": "639272736000000000", "name": "11 October 2026", "matchCount": 3, "order": 1, "sportType": None},
    ],
    "odds": {
        "201775462": {
            "79117": {
                "911679": {"0.00": {"id": "2c1a9be79", "value": 1.41}},
                "911680": {"0.00": {"id": "2c2ff82e6", "value": 4.64}},
                "911681": {"0.00": {"id": "2c2ff82e7", "value": 6.81}},
            }
        },
        "201775536": {
            "79117": {
                "911679": {"0.00": {"id": "2cxxx1", "value": 2.60}},
                "911680": {"0.00": {"id": "2cxxx2", "value": 3.47}},
                "911681": {"0.00": {"id": "2cxxx3", "value": 2.51}},
            }
        },
    },
    "markets": [
        {
            "id": 79117,
            "name": "1x2",
            "options": [
                {"id": 911679, "name": "1"},
                {"id": 911680, "name": "X"},
                {"id": 911681, "name": "2"},
            ],
            "hndValue": 0.0,
            "oddsClassId": 79117,
        }
    ],
}

SAMPLE_RAW_PAYLOAD = {"leagues": {"990625": {"league_name": "Premier League", "payload": SAMPLE_MATCHES_PAYLOAD}}}


def test_to_odds_events_joins_events_odds_and_markets_legend():
    scraper = SupaBetsScraper()

    events = scraper.to_odds_events(SAMPLE_RAW_PAYLOAD)

    assert len(events) == 2
    arsenal_leeds = next(e for e in events if e.home_team == "Arsenal FC")
    assert arsenal_leeds.away_team == "Leeds United"
    assert arsenal_leeds.sport == "soccer"
    assert arsenal_leeds.league == "Premier League"
    assert arsenal_leeds.bookmaker == "supabets"
    assert arsenal_leeds.event_id is None  # left for the engine to compute
    assert arsenal_leeds.markets["moneyline"].home_odds == 1.41
    assert arsenal_leeds.markets["moneyline"].draw_odds == 4.64
    assert arsenal_leeds.markets["moneyline"].away_odds == 6.81


def test_to_odds_events_maps_outcome_labels_not_id_order():
    """The "1"/"X"/"2" -> home/draw/away mapping must come from the
    payload's own markets[].options[] legend, not from outcome-id sort
    order (which happens to agree in the real capture, but the code must
    not depend on that coincidence)."""
    payload = {
        **SAMPLE_MATCHES_PAYLOAD,
        "odds": {
            "201775462": SAMPLE_MATCHES_PAYLOAD["odds"]["201775462"],
        },
        "markets": [
            {
                "id": 79117,
                "name": "1x2",
                # Deliberately out-of-order / re-numbered ids relative to
                # the real capture, to prove label lookup drives the
                # mapping rather than id ordering.
                "options": [
                    {"id": 911681, "name": "1"},
                    {"id": 911679, "name": "X"},
                    {"id": 911680, "name": "2"},
                ],
                "hndValue": 0.0,
                "oddsClassId": 79117,
            }
        ],
    }
    raw = {"leagues": {"990625": {"league_name": "Premier League", "payload": payload}}}
    scraper = SupaBetsScraper()

    events = scraper.to_odds_events(raw)

    assert len(events) == 1
    market = events[0].markets["moneyline"]
    # outcome 911681 (value 6.81) is now labelled "1" -> home
    assert market.home_odds == 6.81
    # outcome 911679 (value 1.41) is now labelled "X" -> draw
    assert market.draw_odds == 1.41
    # outcome 911680 (value 4.64) is now labelled "2" -> away
    assert market.away_odds == 4.64


def test_to_odds_events_skips_league_missing_1x2_market_definition():
    payload = {**SAMPLE_MATCHES_PAYLOAD, "markets": []}
    raw = {"leagues": {"990625": {"league_name": "Premier League", "payload": payload}}}
    scraper = SupaBetsScraper()

    assert scraper.to_odds_events(raw) == []


def test_to_odds_events_skips_incomplete_price_data():
    """If a price is missing for home or away, don't publish a partial/misleading market."""
    payload = {
        **SAMPLE_MATCHES_PAYLOAD,
        "odds": {
            "201775462": {
                "79117": {
                    "911679": {"0.00": {"id": "2c1a9be79", "value": 1.41}},
                    # draw/away outcomes missing entirely
                }
            }
        },
    }
    raw = {"leagues": {"990625": {"league_name": "Premier League", "payload": payload}}}
    scraper = SupaBetsScraper()

    assert scraper.to_odds_events(raw) == []


def test_to_odds_events_handles_multiple_leagues_independently():
    other_payload = {
        "events": [
            {
                "id": "1",
                "name": "10 October 2026",
                "matchCount": 1,
                "order": 0,
                "sportType": None,
                "matches": [
                    {
                        "id": 555,
                        "eventName": "LaLiga",
                        "participants": [{"name": "Real Madrid"}, {"name": "Sevilla"}],
                        "slug": "spa/sport/soccer/spain/laliga/555-real-madrid-sevilla",
                        "start": "2026-10-11T19:00:00Z",
                        "statisticsCode": "1",
                        "oddsCount": 100,
                        "odds": None,
                    }
                ],
            }
        ],
        "odds": {
            "555": {
                "79117": {
                    "911679": {"0.00": {"id": "x1", "value": 1.30}},
                    "911680": {"0.00": {"id": "x2", "value": 5.50}},
                    "911681": {"0.00": {"id": "x3", "value": 8.00}},
                }
            }
        },
        "markets": SAMPLE_MATCHES_PAYLOAD["markets"],
    }
    raw = {
        "leagues": {
            "990625": {"league_name": "Premier League", "payload": SAMPLE_MATCHES_PAYLOAD},
            "990618": {"league_name": "LaLiga", "payload": other_payload},
        }
    }
    scraper = SupaBetsScraper()

    events = scraper.to_odds_events(raw)

    assert len(events) == 3
    assert {e.home_team for e in events} == {"Arsenal FC", "Aston Villa", "Real Madrid"}


def test_to_odds_events_skips_a_malformed_match_without_crashing_the_batch():
    """One match missing a required field (e.g. participants) must not take
    down parsing of every other match in the same league's payload."""
    malformed_match = {
        "id": 999999,
        "eventName": "Premier League",
        "participants": [{"name": "OnlyOneTeam"}],  # malformed: needs two
        "slug": "spa/sport/soccer/england/premier-league/999999-x",
        "start": "2026-10-10T14:00:00Z",
        "statisticsCode": "9",
        "oddsCount": 1,
        "odds": None,
    }
    payload = {
        **SAMPLE_MATCHES_PAYLOAD,
        "events": [
            {
                **SAMPLE_MATCHES_PAYLOAD["events"][0],
                "matches": [*SAMPLE_MATCHES_PAYLOAD["events"][0]["matches"], malformed_match],
            }
        ],
        "odds": {
            **SAMPLE_MATCHES_PAYLOAD["odds"],
            "999999": SAMPLE_MATCHES_PAYLOAD["odds"]["201775462"],
        },
    }
    raw = {"leagues": {"990625": {"league_name": "Premier League", "payload": payload}}}
    scraper = SupaBetsScraper()

    events = scraper.to_odds_events(raw)

    # The two well-formed matches still come through; the malformed one is
    # skipped rather than raising and losing the whole league's batch.
    assert len(events) == 2
    assert {e.home_team for e in events} == {"Arsenal FC", "Aston Villa"}


def test_to_odds_events_skips_a_malformed_start_timestamp():
    payload = {
        **SAMPLE_MATCHES_PAYLOAD,
        "events": [
            {
                **SAMPLE_MATCHES_PAYLOAD["events"][0],
                "matches": [
                    {**SAMPLE_MATCHES_PAYLOAD["events"][0]["matches"][0], "start": "not-a-timestamp"},
                    SAMPLE_MATCHES_PAYLOAD["events"][0]["matches"][1],
                ],
            }
        ],
    }
    raw = {"leagues": {"990625": {"league_name": "Premier League", "payload": payload}}}
    scraper = SupaBetsScraper()

    events = scraper.to_odds_events(raw)

    assert len(events) == 1
    assert events[0].home_team == "Aston Villa"


@pytest.mark.asyncio
async def test_poll_yields_events_on_a_fixed_interval(monkeypatch):
    scraper = SupaBetsScraper()

    async def fake_fetch_raw_odds():
        return SAMPLE_RAW_PAYLOAD

    monkeypatch.setattr(scraper, "fetch_raw_odds", fake_fetch_raw_odds)

    results = []
    async for events in scraper.poll(interval_seconds=0.01):
        results.append(events)
        if len(results) == 3:
            break

    assert len(results) == 3
    assert all(len(batch) == 2 for batch in results)


@pytest.mark.asyncio
async def test_poll_continues_past_a_transient_fetch_failure(monkeypatch):
    import httpx

    scraper = SupaBetsScraper()
    call_count = 0

    async def flaky_fetch_raw_odds():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ConnectError("simulated network blip")
        return SAMPLE_RAW_PAYLOAD

    monkeypatch.setattr(scraper, "fetch_raw_odds", flaky_fetch_raw_odds)

    results = []
    async for events in scraper.poll(interval_seconds=0.01):
        results.append(events)
        break  # first successful yield should be the second call, after the failure

    assert call_count == 2
    assert len(results) == 1
    assert len(results[0]) == 2


@pytest.mark.asyncio
async def test_fetch_raw_odds_skips_a_failing_league_without_aborting_others(monkeypatch):
    """A single league's request failing (that competition temporarily
    down, a transient blip) shouldn't lose the other configured leagues'
    odds for the whole poll cycle -- SupaBets has no bulk "all leagues"
    call to fetch instead, unlike Betway ZA/WSB."""
    import httpx

    scraper = SupaBetsScraper(league_ids={1: "Good League", 2: "Bad League"})

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    async def fake_get(url, params=None):
        if params["league"] == 2:
            raise httpx.ConnectError("simulated failure for league 2")
        return FakeResponse(SAMPLE_MATCHES_PAYLOAD)

    monkeypatch.setattr(scraper._client, "get", fake_get)

    raw = await scraper.fetch_raw_odds()

    assert list(raw["leagues"].keys()) == ["1"]
