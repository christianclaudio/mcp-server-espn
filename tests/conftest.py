"""Pytest fixtures and mocked HTTP transports for ESPN MCP tests."""

import httpx
import pytest


@pytest.fixture
def mock_transport():
    """Create a mock transport with pre-configured ESPN responses."""

    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)

        if "auth-fail" in url_str:
            return httpx.Response(401, text="Unauthorized")
        if "server-error" in url_str:
            return httpx.Response(500, text="Internal Server Error")
        if "rate-limit" in url_str:
            return httpx.Response(429, text="Too Many Requests")
        if "empty" in url_str:
            return httpx.Response(204)

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
                    "scoringPlays": [
                        {
                            "period": {"number": 1},
                            "clock": {"displayValue": "10:24"},
                            "type": {"text": "Home Run"},
                            "text": "Bryan Reynolds homers to right.",
                            "awayScore": 0,
                            "homeScore": 1,
                            "team": {"id": "23"},
                        }
                    ],
                    "drives": {
                        "current": {
                            "description": "Punt",
                            "plays": 5,
                            "yards": 22,
                            "start": {"period": {"number": 2}},
                        },
                        "previous": [{"id": "1"}],
                    },
                    "leaders": [
                        {
                            "name": "homeRuns",
                            "displayName": "Home Runs",
                            "leaders": [
                                {
                                    "displayValue": "24",
                                    "athlete": {"id": "12345", "displayName": "Bryan Reynolds"},
                                    "team": {"id": "23"},
                                }
                            ],
                        }
                    ],
                    "againstTheSpread": [
                        {
                            "team": {"id": "23", "displayName": "Pirates"},
                            "favorite": True,
                            "underdog": False,
                            "line": "-1.5",
                            "record": "70-65",
                        }
                    ],
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
                    "coach": [
                        {
                            "id": "999",
                            "firstName": "Derek",
                            "lastName": "Shelton",
                            "experience": 5,
                        }
                    ],
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

        if "search" in url_str:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "12483",
                            "displayName": "Matthew Stafford",
                            "type": "player",
                            "description": "Quarterback for Los Angeles Rams",
                            "league": "nfl",
                            "sport": "football",
                            "link": {"web": "https://espn.com/nfl/player/_/id/12483"},
                        }
                    ]
                },
            )

        if "byathlete" in url_str:
            return httpx.Response(
                200,
                json={
                    "athletes": [
                        {
                            "athlete": {"id": "12483", "displayName": "Matthew Stafford"},
                            "displayValue": "4200",
                        }
                    ]
                },
            )

        if "byteam" in url_str:
            return httpx.Response(
                200,
                json={
                    "teams": [
                        {
                            "team": {"id": "14", "displayName": "Los Angeles Rams"},
                            "displayValue": "450.5",
                        }
                    ]
                },
            )

        if "statistics" in url_str:
            return httpx.Response(
                200,
                json={
                    "results": {
                        "stats": {
                            "categories": [
                                {
                                    "name": "passing",
                                    "displayName": "Passing",
                                    "stats": [{"name": "passingYards", "displayValue": "4200"}],
                                }
                            ]
                        },
                        "opponent": {
                            "categories": [
                                {
                                    "name": "defensive",
                                    "displayName": "Defensive",
                                    "stats": [{"name": "interceptions", "displayValue": "15"}],
                                }
                            ]
                        },
                    }
                },
            )

        if "transactions" in url_str:
            return httpx.Response(
                200,
                json={
                    "transactions": [
                        {
                            "date": "2026-09-20T12:00:00Z",
                            "description": "Signed QB Matthew Stafford to a contract extension.",
                            "team": {"id": "14", "displayName": "Los Angeles Rams"},
                        }
                    ]
                },
            )

        if "bio" in url_str:
            return httpx.Response(
                200,
                json={
                    "bio": {
                        "birthPlace": {"city": "Tampa", "state": "FL"},
                        "college": {"name": "Georgia"},
                        "draft": {"round": 1, "selection": 1, "year": 2009},
                    }
                },
            )

        if "athletes" in url_str and "stats" in url_str:
            return httpx.Response(
                200,
                json={
                    "statistics": {
                        "categories": [
                            {
                                "name": "passing",
                                "stats": [{"name": "passingYards", "value": 4200}],
                            }
                        ]
                    }
                },
            )

        if "gamelog" in url_str:
            return httpx.Response(
                200,
                json={
                    "events": [
                        {
                            "id": "401872947",
                            "date": "2026-09-20",
                            "stats": ["24/35", "280", "2", "0"],
                        }
                    ]
                },
            )

        if "splits" in url_str:
            return httpx.Response(
                200,
                json={
                    "splits": {
                        "categories": [
                            {
                                "name": "home",
                                "stats": [{"name": "passingYards", "value": 2100}],
                            }
                        ]
                    }
                },
            )

        if "groups" in url_str:
            return httpx.Response(
                200,
                json={
                    "groups": [
                        {
                            "id": "1",
                            "name": "NFC West",
                            "teams": [{"id": "14"}],
                        }
                    ]
                },
            )

        if "draft" in url_str:
            return httpx.Response(
                200,
                json={
                    "draft": {
                        "year": 2026,
                        "rounds": [
                            {
                                "number": 1,
                                "picks": [{"overall": 1, "team": {"id": "14"}}],
                            }
                        ],
                    }
                },
            )

        if "scoreboard/header" in url_str:
            return httpx.Response(
                200,
                json={
                    "sports": [
                        {
                            "name": "football",
                            "leagues": [{"name": "NFL", "events": [{"id": "401872947"}]}],
                        }
                    ]
                },
            )

        if "odds" in url_str:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "provider": {"name": "DraftKings"},
                            "spread": -3.5,
                            "overUnder": 48.5,
                            "moneyline": -180,
                        }
                    ]
                },
            )

        if "plays" in url_str:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "1",
                            "text": "M.Stafford pass deep right for 25 yards, TOUCHDOWN.",
                            "clock": {"displayValue": "10:14"},
                            "scoringPlay": True,
                        }
                    ]
                },
            )

        if "situation" in url_str:
            return httpx.Response(
                200,
                json={
                    "down": 3,
                    "distance": 4,
                    "yardLine": 25,
                    "isRedZone": True,
                    "possessionText": "LAR",
                },
            )

        if "probabilities" in url_str:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "homeWinPercentage": 0.68,
                            "playId": "1",
                            "secondsLeft": 600,
                        }
                    ]
                },
            )

        if "predictor" in url_str:
            return httpx.Response(
                200,
                json={"homeTeam": {"gameProjection": 65.4, "teamChanceLoss": 34.6}},
            )

        if "calendar" in url_str:
            return httpx.Response(
                200,
                json={"eventDate": {"dates": ["2026-09-20", "2026-09-27"]}},
            )

        if "futures" in url_str:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "name": "Super Bowl LXI Champion",
                            "books": [{"team": {"id": "14"}, "value": "+1200"}],
                        }
                    ]
                },
            )

        if "powerindex" in url_str:
            return httpx.Response(
                200,
                json={"items": [{"team": {"id": "14"}, "rank": 4, "fpi": 8.5}]},
            )

        if "events" in url_str and "sports" in url_str and "leagues" not in url_str:
            return httpx.Response(
                200,
                json={"events": [{"id": "401872947", "name": "NYG @ LAR", "date": "2026-09-27"}]},
            )

        if "teams/14" in url_str:
            return httpx.Response(
                200,
                json={
                    "team": {
                        "id": "14",
                        "displayName": "Los Angeles Rams",
                        "abbreviation": "LAR",
                        "standingSummary": "1st in NFC West",
                        "record": {"items": [{"summary": "10-7"}]},
                        "venue": {"fullName": "SoFi Stadium"},
                        "nextEvent": [
                            {"id": "401872947", "name": "NYG @ LAR", "date": "2026-09-27T20:25:00Z"}
                        ],
                    }
                },
            )

        if "teams" in url_str:
            return httpx.Response(
                200,
                json={
                    "sports": [
                        {
                            "leagues": [
                                {
                                    "teams": [
                                        {
                                            "team": {
                                                "id": "14",
                                                "displayName": "Los Angeles Rams",
                                                "abbreviation": "LAR",
                                                "location": "Los Angeles",
                                                "nickname": "Rams",
                                                "color": "003594",
                                            }
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                },
            )

        return httpx.Response(200, json={"ok": True})

    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def mock_server_client(mock_transport: httpx.MockTransport):
    """Ensure espn_mcp.server.client and default_client use mock_transport during tests."""
    from espn_mcp import client as client_module
    from espn_mcp import server

    orig_server_client = getattr(server, "client", None)
    orig_default_custom = client_module.default_client._custom_client

    mock_http = httpx.AsyncClient(
        transport=mock_transport, base_url="https://site.web.api.espn.com"
    )
    test_client = client_module.ESPNClient(http_client=mock_http)

    server.client = test_client
    client_module.default_client._custom_client = mock_http

    yield

    server.client = orig_server_client
    client_module.default_client._custom_client = orig_default_custom
