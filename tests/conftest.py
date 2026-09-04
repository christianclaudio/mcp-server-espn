"""Pytest fixtures and mocked HTTP transports for ESPN MCP tests."""

import httpx
import pytest


@pytest.fixture
def mock_transport():
    """Create a mock transport with pre-configured ESPN responses."""

    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)

        if "scoreboard" in url_str:
            if "not-found" in url_str:
                return httpx.Response(404, json={"error": "Not Found"})
            return httpx.Response(
                200,
                json={
                    "events": [
                        {
                            "id": "401816789",
                            "name": "San Francisco Giants at Pittsburgh Pirates",
                            "date": "2026-09-04T02:00:00Z",
                            "status": {
                                "type": {
                                    "state": "in",
                                    "detail": "Top 8th",
                                    "shortDetail": "Top 8th",
                                },
                                "displayClock": "0:00",
                                "period": 8,
                            },
                            "competitions": [
                                {
                                    "broadcasts": [{"names": ["ESPN", "NESN"]}],
                                    "competitors": [
                                        {
                                            "homeAway": "home",
                                            "team": {
                                                "id": "23",
                                                "displayName": "Pittsburgh Pirates",
                                                "abbreviation": "PIT",
                                            },
                                            "score": "5",
                                            "records": [{"summary": "65-72"}],
                                            "probables": [
                                                {"athlete": {"displayName": "Paul Skenes"}}
                                            ],
                                            "winner": False,
                                        },
                                        {
                                            "homeAway": "away",
                                            "team": {
                                                "id": "26",
                                                "displayName": "San Francisco Giants",
                                                "abbreviation": "SF",
                                            },
                                            "score": "3",
                                            "records": [{"summary": "68-70"}],
                                            "probables": [
                                                {"athlete": {"displayName": "Logan Webb"}}
                                            ],
                                            "winner": False,
                                        },
                                    ],
                                }
                            ],
                        }
                    ]
                },
            )

        if "summary" in url_str:
            if "not-found" in url_str:
                return httpx.Response(404, json={"error": "Not Found"})
            return httpx.Response(
                200,
                json={
                    "header": {
                        "season": {"year": 2026},
                        "week": 1,
                        "competitions": [{"id": "401816789"}],
                    },
                    "gameInfo": {"venue": {"fullName": "PNC Park"}},
                    "pickcenter": [
                        {
                            "provider": {"name": "DraftKings"},
                            "details": "PIT -175",
                            "overUnder": 8.5,
                            "spread": -1.5,
                            "awayTeamOdds": {"moneyLine": 150},
                            "homeTeamOdds": {"moneyLine": -175},
                        }
                    ],
                    "predictor": {
                        "header": "Matchup Predictor",
                        "homeTeam": {"gameProjection": "62.4"},
                        "awayTeam": {"gameProjection": "37.6"},
                    },
                    "winprobability": [
                        {"homeWinPercentage": 0.5, "playId": "1"},
                        {"homeWinPercentage": 0.72, "playId": "2"},
                    ],
                    "seasonseries": [{"summary": "PIT leads 2-1"}],
                    "lastFiveGames": [{"team": {"abbreviation": "PIT"}}],
                    "injuries": [{"team": {"displayName": "Pirates"}, "injuries": []}],
                    "boxscore": {
                        "teams": [
                            {
                                "team": {
                                    "id": "23",
                                    "displayName": "Pirates",
                                    "abbreviation": "PIT",
                                },
                                "statistics": [{"name": "hits", "displayValue": "8"}],
                            }
                        ],
                        "players": [
                            {
                                "team": {
                                    "id": "23",
                                    "displayName": "Pirates",
                                    "abbreviation": "PIT",
                                },
                                "statistics": [
                                    {
                                        "type": "batting",
                                        "labels": ["AB", "R", "H", "RBI"],
                                        "athletes": [
                                            {
                                                "athlete": {
                                                    "id": "12345",
                                                    "displayName": "Bryan Reynolds",
                                                    "jersey": "10",
                                                    "position": {"abbreviation": "LF"},
                                                },
                                                "stats": ["4", "1", "2", "2"],
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    },
                },
            )

        if "standings" in url_str:
            return httpx.Response(
                200,
                json={
                    "standings": {
                        "entries": [
                            {
                                "team": {
                                    "id": "23",
                                    "displayName": "Pirates",
                                    "abbreviation": "PIT",
                                },
                                "stats": [{"name": "wins", "displayValue": "65"}],
                            }
                        ]
                    }
                },
            )

        if "news" in url_str:
            return httpx.Response(
                200,
                json={
                    "articles": [
                        {
                            "headline": "Skenes pitches gem",
                            "description": "Paul Skenes struck out 9",
                            "published": "2026-09-04T03:00:00Z",
                            "type": "HeadlineNews",
                            "byline": "ESPN Staff",
                            "links": {"web": {"href": "https://espn.com/mlb/story"}},
                        }
                    ]
                },
            )

        if "rankings" in url_str:
            return httpx.Response(
                200,
                json={
                    "rankings": [
                        {
                            "name": "AP Top 25",
                            "type": "poll",
                            "headline": "Week 1",
                            "ranks": [
                                {
                                    "current": 1,
                                    "previous": 1,
                                    "points": 1500,
                                    "firstPlaceVotes": 60,
                                    "recordSummary": "1-0",
                                    "team": {
                                        "id": "333",
                                        "displayName": "Alabama Crimson Tide",
                                        "abbreviation": "ALA",
                                    },
                                }
                            ],
                        }
                    ]
                },
            )

        if "roster" in url_str:
            return httpx.Response(
                200,
                json={
                    "team": {"displayName": "Pittsburgh Pirates"},
                    "season": {"year": 2026},
                    "athletes": [
                        {
                            "position": "pitchers",
                            "items": [
                                {
                                    "id": "5000",
                                    "displayName": "Paul Skenes",
                                    "jersey": "30",
                                    "position": {"abbreviation": "SP"},
                                    "experience": {"years": 2},
                                    "injuries": [],
                                }
                            ],
                        }
                    ],
                },
            )

        if "depthcharts" in url_str:
            return httpx.Response(
                200,
                json={
                    "depthchart": {
                        "QB": {
                            "athletes": [
                                {
                                    "slot": 1,
                                    "rank": 1,
                                    "athlete": {
                                        "id": "99",
                                        "displayName": "Quarterback One",
                                        "jersey": "7",
                                    },
                                }
                            ]
                        }
                    }
                },
            )

        if "schedule" in url_str:
            return httpx.Response(
                200,
                json={
                    "team": {"displayName": "Pittsburgh Pirates"},
                    "season": {"year": 2026},
                    "events": [
                        {
                            "id": "401816789",
                            "date": "2026-09-04T02:00:00Z",
                            "name": "SF at PIT",
                            "competitions": [
                                {
                                    "competitors": [
                                        {"id": "23", "team": {"displayName": "Pirates"}},
                                        {"id": "26", "team": {"displayName": "Giants"}},
                                    ],
                                    "status": {"type": {"state": "post", "detail": "Final"}},
                                }
                            ],
                        }
                    ],
                },
            )

        if "athletes" in url_str and "overview" in url_str:
            return httpx.Response(
                200,
                json={
                    "statistics": {"seasons": [{"year": 2026, "era": 2.15}]},
                    "nextGame": {"name": "Pirates vs Reds"},
                    "gameLog": {"games": []},
                    "rotowire": [{"headline": "Scheduled to start Friday"}],
                    "awards": ["All-Star"],
                },
            )

        if "auth-fail" in url_str:
            return httpx.Response(401, text="Unauthorized")
        if "server-error" in url_str:
            return httpx.Response(500, text="Internal Server Error")
        if "rate-limit" in url_str:
            return httpx.Response(429, text="Too Many Requests")
        if "empty" in url_str:
            return httpx.Response(204)

        return httpx.Response(200, json={"ok": True})

    return httpx.MockTransport(handler)
