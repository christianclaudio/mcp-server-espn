"""Asynchronous HTTP client for ESPN public APIs.

Includes connection pooling, retries, and path sanitization.
"""

import asyncio
import random
import urllib.parse
from typing import Any

import httpx

from espn_mcp import __version__
from espn_mcp.config import settings
from espn_mcp.errors import (
    ESPNConnectionError,
    ESPNNotFoundError,
    ESPNRateLimitError,
    ESPNValidationError,
    redact_secrets,
)

# Common sport/league aliases for seamless LLM agent invocation
SPORT_LEAGUE_MAP: dict[str, tuple[str, str]] = {
    "mlb": ("baseball", "mlb"),
    "baseball": ("baseball", "mlb"),
    "nba": ("basketball", "nba"),
    "wnba": ("basketball", "wnba"),
    "ncaab": ("basketball", "mens-college-basketball"),
    "cbb": ("basketball", "mens-college-basketball"),
    "mens-college-basketball": ("basketball", "mens-college-basketball"),
    "womens-college-basketball": ("basketball", "womens-college-basketball"),
    "wncaab": ("basketball", "womens-college-basketball"),
    "nfl": ("football", "nfl"),
    "ncaaf": ("football", "college-football"),
    "cfb": ("football", "college-football"),
    "college-football": ("football", "college-football"),
    "nhl": ("hockey", "nhl"),
    "hockey": ("hockey", "nhl"),
    "epl": ("soccer", "eng.1"),
    "premier-league": ("soccer", "eng.1"),
    "eng.1": ("soccer", "eng.1"),
    "mls": ("soccer", "usa.1"),
    "usa.1": ("soccer", "usa.1"),
    "ucl": ("soccer", "uefa.champions"),
    "champions-league": ("soccer", "uefa.champions"),
    "uefa.champions": ("soccer", "uefa.champions"),
    "laliga": ("soccer", "esp.1"),
    "esp.1": ("soccer", "esp.1"),
    "bundesliga": ("soccer", "ger.1"),
    "ger.1": ("soccer", "ger.1"),
    "seriea": ("soccer", "ita.1"),
    "ita.1": ("soccer", "ita.1"),
    "pga": ("golf", "pga"),
    "golf": ("golf", "pga"),
    "ufc": ("mma", "ufc"),
    "mma": ("mma", "ufc"),
}


KNOWN_SPORTS = {
    "baseball",
    "basketball",
    "football",
    "hockey",
    "soccer",
    "golf",
    "mma",
    "racing",
    "tennis",
}


def normalize_sport_league(sport: str, league: str) -> tuple[str, str]:
    """Resolve and validate sport and league parameters, accepting common aliases."""
    s_clean = sport.strip().lower() if sport else ""
    l_clean = league.strip().lower() if league else ""

    # Check league first in alias map
    if l_clean in SPORT_LEAGUE_MAP:
        return SPORT_LEAGUE_MAP[l_clean]
    # If both provided directly and sport is recognized
    if s_clean in KNOWN_SPORTS and l_clean:
        return s_clean, l_clean
    # Check sport in alias map if league is empty or generic
    if s_clean in SPORT_LEAGUE_MAP:
        return SPORT_LEAGUE_MAP[s_clean]

    raise ESPNValidationError(
        f"Unable to resolve sport/league for sport='{sport}', league='{league}'. "
        f"Supported leagues include: {', '.join(sorted(SPORT_LEAGUE_MAP.keys()))}"
    )


class ESPNClient:
    """Hardened async client for querying ESPN public REST endpoints."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = (base_url or settings.BASE_URL).rstrip("/")
        self.timeout = timeout if timeout is not None else settings.TIMEOUT_SECONDS
        self.max_retries = max_retries if max_retries is not None else settings.MAX_RETRIES
        self._custom_client = http_client
        self._client: httpx.AsyncClient | None = None

    async def get_client(self) -> httpx.AsyncClient:
        """Retrieve or initialize persistent AsyncClient pool."""
        if self._custom_client is not None:
            return self._custom_client
        if self._client is None or self._client.is_closed:
            headers = {
                "Accept": "application/json",
                "User-Agent": f"mcp-server-espn/{__version__}",
            }
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=httpx.Timeout(self.timeout),
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
            )
        return self._client

    async def close(self) -> None:
        """Close underlying connection pool."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def sanitize_path_param(self, segment: str) -> str:
        """Encode path parameters to prevent directory traversal and injection."""
        return urllib.parse.quote(str(segment), safe="").replace("..", "%2E%2E")

    async def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute request with exponential backoff and randomized jitter."""
        client = await self.get_client()
        url = f"{self.base_url}/{path.lstrip('/')}"
        last_exception: Exception | None = None

        # Filter out None values from params
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}

        for attempt in range(self.max_retries + 1):
            try:
                response = await client.request(
                    method=method,
                    url=url,
                    params=clean_params,
                    json=json_data,
                )

                if response.status_code == 404:
                    raise ESPNNotFoundError(f"HTTP 404: ESPN resource at '{path}' not found.")

                if response.status_code == 429:
                    if attempt < self.max_retries:
                        backoff = (2**attempt) + random.uniform(0.1, 0.5)
                        await asyncio.sleep(backoff)
                        continue
                    raise ESPNRateLimitError("HTTP 429: Rate limit exceeded after retries.")

                if response.status_code >= 500:
                    if attempt < self.max_retries:
                        backoff = (2**attempt) + random.uniform(0.1, 0.5)
                        await asyncio.sleep(backoff)
                        continue
                    raise ESPNConnectionError(
                        f"HTTP {response.status_code}: Upstream server error."
                    )

                response.raise_for_status()
                if not response.content:
                    return {}
                return response.json()  # type: ignore[no-any-return]

            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                last_exception = exc
                if attempt < self.max_retries:
                    await asyncio.sleep((2**attempt) + random.uniform(0.1, 0.5))
                    continue

        raise ESPNConnectionError(f"Request failed: {redact_secrets(str(last_exception))}")

    # =========================================================================
    # High-level ESPN domain methods
    # =========================================================================

    async def get_scoreboard(
        self,
        sport: str,
        league: str,
        dates: str | None = None,
        week: int | None = None,
        season_type: int | None = None,
        group: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Fetch live scoreboard, game states, broadcasts, and probables."""
        s, lg = normalize_sport_league(sport, league)
        s_san = self.sanitize_path_param(s)
        lg_san = self.sanitize_path_param(lg)
        params: dict[str, Any] = {"limit": limit}
        if dates:
            params["dates"] = dates.replace("-", "")
        if week is not None:
            params["week"] = week
        if season_type is not None:
            params["seasontype"] = season_type
        if group:
            params["groups"] = group

        raw = await self.request(
            "GET", f"apis/site/v2/sports/{s_san}/{lg_san}/scoreboard", params=params
        )
        return self._format_scoreboard(raw, s, lg)

    async def get_game_summary(
        self,
        sport: str,
        league: str,
        event_id: str,
    ) -> dict[str, Any]:
        """Fetch full game summary, betting lines (pickcenter), predictor, and series."""
        s, lg = normalize_sport_league(sport, league)
        s_san = self.sanitize_path_param(s)
        lg_san = self.sanitize_path_param(lg)
        raw = await self.request(
            "GET", f"apis/site/v2/sports/{s_san}/{lg_san}/summary", params={"event": event_id}
        )
        return self._format_game_summary(raw, s, lg, event_id)

    async def get_player_stats(
        self,
        sport: str,
        league: str,
        event_id: str,
    ) -> dict[str, Any]:
        """Extract individual player boxscores and performance metrics for a game."""
        s, lg = normalize_sport_league(sport, league)
        s_san = self.sanitize_path_param(s)
        lg_san = self.sanitize_path_param(lg)
        raw = await self.request(
            "GET", f"apis/site/v2/sports/{s_san}/{lg_san}/summary", params={"event": event_id}
        )
        return self._format_player_stats(raw, s, lg, event_id)

    async def get_standings(
        self,
        sport: str,
        league: str,
        season: int | None = None,
    ) -> dict[str, Any]:
        """Fetch division, conference, and overall standings."""
        s, lg = normalize_sport_league(sport, league)
        s_san = self.sanitize_path_param(s)
        lg_san = self.sanitize_path_param(lg)
        params: dict[str, Any] = {}
        if season is not None:
            params["season"] = season
        raw = await self.request("GET", f"apis/v2/sports/{s_san}/{lg_san}/standings", params=params)
        return self._format_standings(raw, s, lg)

    async def get_news(
        self,
        sport: str,
        league: str,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Fetch latest headlines, breaking news, and injuries."""
        s, lg = normalize_sport_league(sport, league)
        s_san = self.sanitize_path_param(s)
        lg_san = self.sanitize_path_param(lg)
        raw = await self.request(
            "GET", f"apis/site/v2/sports/{s_san}/{lg_san}/news", params={"limit": limit}
        )
        return self._format_news(raw, s, lg)

    async def get_rankings(
        self,
        sport: str,
        league: str,
    ) -> dict[str, Any]:
        """Fetch Top 25 national polls (AP Poll, Coaches Poll, CFP)."""
        s, lg = normalize_sport_league(sport, league)
        s_san = self.sanitize_path_param(s)
        lg_san = self.sanitize_path_param(lg)
        raw = await self.request("GET", f"apis/site/v2/sports/{s_san}/{lg_san}/rankings")
        return self._format_rankings(raw, s, lg)

    async def get_team_roster(
        self,
        sport: str,
        league: str,
        team_id: str,
    ) -> dict[str, Any]:
        """Fetch active team roster and injury designations by position."""
        s, lg = normalize_sport_league(sport, league)
        s_san = self.sanitize_path_param(s)
        lg_san = self.sanitize_path_param(lg)
        t_san = self.sanitize_path_param(team_id)
        raw = await self.request(
            "GET", f"apis/site/v2/sports/{s_san}/{lg_san}/teams/{t_san}/roster"
        )
        return self._format_team_roster(raw, s, lg, team_id)

    async def get_team_depth_chart(
        self,
        sport: str,
        league: str,
        team_id: str,
    ) -> dict[str, Any]:
        """Fetch team depth chart (starters and backup positional hierarchy)."""
        s, lg = normalize_sport_league(sport, league)
        s_san = self.sanitize_path_param(s)
        lg_san = self.sanitize_path_param(lg)
        t_san = self.sanitize_path_param(team_id)
        raw = await self.request(
            "GET", f"apis/site/v2/sports/{s_san}/{lg_san}/teams/{t_san}/depthcharts"
        )
        return self._format_depth_chart(raw, s, lg, team_id)

    async def get_team_schedule(
        self,
        sport: str,
        league: str,
        team_id: str,
        season: int | None = None,
    ) -> dict[str, Any]:
        """Fetch full team season schedule and past game results."""
        s, lg = normalize_sport_league(sport, league)
        s_san = self.sanitize_path_param(s)
        lg_san = self.sanitize_path_param(lg)
        t_san = self.sanitize_path_param(team_id)
        params: dict[str, Any] = {}
        if season is not None:
            params["season"] = season
        raw = await self.request(
            "GET", f"apis/site/v2/sports/{s_san}/{lg_san}/teams/{t_san}/schedule", params=params
        )
        return self._format_team_schedule(raw, s, lg, team_id)

    async def get_athlete_overview(
        self,
        sport: str,
        league: str,
        athlete_id: str,
    ) -> dict[str, Any]:
        """Fetch athlete biographical profile, season splits, and game log."""
        s, lg = normalize_sport_league(sport, league)
        s_san = self.sanitize_path_param(s)
        lg_san = self.sanitize_path_param(lg)
        a_san = self.sanitize_path_param(athlete_id)
        raw = await self.request(
            "GET", f"apis/common/v3/sports/{s_san}/{lg_san}/athletes/{a_san}/overview"
        )
        return self._format_athlete_overview(raw, s, lg, athlete_id)

    # =========================================================================
    # Data Formatters & Cleaners
    # =========================================================================

    def _format_scoreboard(self, raw: dict[str, Any], sport: str, league: str) -> dict[str, Any]:
        events = []
        for ev in raw.get("events", []):
            ev_id = ev.get("id")
            name = ev.get("name", "")
            date_str = ev.get("date", "")
            status_obj = ev.get("status", {}).get("type", {})
            state = status_obj.get("state", "").lower()
            detail = status_obj.get("shortDetail") or status_obj.get("detail", "")
            clock = ev.get("status", {}).get("displayClock", "")
            period = ev.get("status", {}).get("period", 0)

            competitions = ev.get("competitions", [])
            comp = competitions[0] if competitions else {}
            broadcasts = [
                b.get("names", [])
                for b in comp.get("broadcasts", [])
                if isinstance(b.get("names"), list)
            ]
            flat_broadcasts = [item for sub in broadcasts for item in sub]

            competitors = comp.get("competitors", [])
            home, away = None, None
            for c in competitors:
                if c.get("homeAway") == "home":
                    home = c
                elif c.get("homeAway") == "away":
                    away = c

            def format_competitor(c: dict[str, Any] | None) -> dict[str, Any]:
                if not c:
                    return {}
                t = c.get("team", {})
                rec = (c.get("records") or [{}])[0].get("summary", "")
                probables = (c.get("probables") or [{}])[0].get("athlete", {}).get("displayName")
                return {
                    "id": t.get("id"),
                    "name": t.get("displayName"),
                    "abbreviation": t.get("abbreviation"),
                    "score": c.get("score", "0"),
                    "record": rec,
                    "probable_starter": probables,
                    "winner": c.get("winner", False),
                }

            events.append(
                {
                    "event_id": ev_id,
                    "matchup": name,
                    "date": date_str,
                    "state": state,
                    "status_detail": detail,
                    "period": period,
                    "clock": clock,
                    "broadcasts": flat_broadcasts,
                    "home_team": format_competitor(home),
                    "away_team": format_competitor(away),
                }
            )

        return {
            "sport": sport,
            "league": league,
            "count": len(events),
            "events": events,
        }

    def _format_game_summary(
        self, raw: dict[str, Any], sport: str, league: str, event_id: str
    ) -> dict[str, Any]:
        header = raw.get("header", {})
        boxscore = raw.get("boxscore", {})
        pickcenter = raw.get("pickcenter", [])
        predictor = raw.get("predictor", {})
        winprob = raw.get("winprobability", [])
        seasonseries = raw.get("seasonseries", [])
        last_five = raw.get("lastFiveGames", [])
        injuries = raw.get("injuries", [])

        # Clean betting lines
        betting_lines = []
        for pick in pickcenter:
            p_info = pick.get("provider", {})
            provider_name = p_info.get("name") if isinstance(p_info, dict) else str(p_info)
            betting_lines.append(
                {
                    "provider": provider_name,
                    "details": pick.get("details"),
                    "over_under": pick.get("overUnder"),
                    "spread": pick.get("spread"),
                    "away_moneyline": pick.get("awayTeamOdds", {}).get("moneyLine"),
                    "home_moneyline": pick.get("homeTeamOdds", {}).get("moneyLine"),
                }
            )

        # Clean team stats
        team_stats = []
        for t in boxscore.get("teams", []):
            team_info = t.get("team", {})
            stats_list = [
                {"name": s.get("name"), "display_value": s.get("displayValue")}
                for s in t.get("statistics", [])
            ]
            team_stats.append(
                {
                    "team_id": team_info.get("id"),
                    "team_name": team_info.get("displayName"),
                    "abbreviation": team_info.get("abbreviation"),
                    "statistics": stats_list,
                }
            )

        return {
            "sport": sport,
            "league": league,
            "event_id": event_id,
            "game_info": raw.get("gameInfo", {}),
            "header": {
                "season": header.get("season", {}),
                "week": header.get("week"),
                "competitions": header.get("competitions", []),
            },
            "betting_lines": betting_lines,
            "predictor": predictor,
            "live_win_probability_samples": winprob[-5:] if winprob else [],
            "season_series": seasonseries,
            "last_five_games": last_five,
            "team_statistics": team_stats,
            "injuries": injuries,
        }

    def _format_player_stats(
        self, raw: dict[str, Any], sport: str, league: str, event_id: str
    ) -> dict[str, Any]:
        boxscore = raw.get("boxscore", {})
        player_groups = boxscore.get("players", [])

        teams_player_stats = []
        for group in player_groups:
            team_info = group.get("team", {})
            categories_out = []
            for stat_cat in group.get("statistics", []):
                cat_type = stat_cat.get("type") or stat_cat.get("name") or "general"
                labels = stat_cat.get("labels", [])
                athletes_out = []
                for ath in stat_cat.get("athletes", []):
                    ath_info = ath.get("athlete", {})
                    stats_values = ath.get("stats", [])
                    stats_map = (
                        dict(zip(labels, stats_values, strict=False))
                        if len(labels) == len(stats_values)
                        else {}
                    )

                    athletes_out.append(
                        {
                            "athlete_id": ath_info.get("id"),
                            "name": ath_info.get("displayName"),
                            "jersey": ath_info.get("jersey"),
                            "position": (
                                ath_info.get("position", {}).get("abbreviation")
                                if isinstance(ath_info.get("position"), dict)
                                else ath_info.get("position")
                            ),
                            "stats": stats_map or stats_values,
                        }
                    )
                categories_out.append(
                    {
                        "category": cat_type,
                        "labels": labels,
                        "athletes": athletes_out,
                    }
                )
            teams_player_stats.append(
                {
                    "team_id": team_info.get("id"),
                    "team_name": team_info.get("displayName"),
                    "abbreviation": team_info.get("abbreviation"),
                    "categories": categories_out,
                }
            )

        return {
            "sport": sport,
            "league": league,
            "event_id": event_id,
            "player_boxscores": teams_player_stats,
        }

    def _format_standings(self, raw: dict[str, Any], sport: str, league: str) -> dict[str, Any]:
        # Handle hierarchical groups/divisions/entries
        entries_out = []

        def extract_entries(node: Any) -> None:
            if isinstance(node, dict):
                if "standings" in node and isinstance(node["standings"], dict):
                    extract_entries(node["standings"].get("entries", []))
                elif "entries" in node and isinstance(node["entries"], list):
                    for item in node["entries"]:
                        team = item.get("team", {})
                        stats = {
                            s.get("name"): s.get("displayValue", s.get("value"))
                            for s in item.get("stats", [])
                        }
                        entries_out.append(
                            {
                                "team_id": team.get("id"),
                                "name": team.get("displayName"),
                                "abbreviation": team.get("abbreviation"),
                                "stats": stats,
                            }
                        )
                for v in node.values():
                    extract_entries(v)
            elif isinstance(node, list):
                for item in node:
                    extract_entries(item)

        extract_entries(raw)
        return {
            "sport": sport,
            "league": league,
            "count": len(entries_out),
            "standings": entries_out,
        }

    def _format_news(self, raw: dict[str, Any], sport: str, league: str) -> dict[str, Any]:
        articles_out = []
        for art in raw.get("articles", []):
            articles_out.append(
                {
                    "headline": art.get("headline"),
                    "description": art.get("description"),
                    "published": art.get("published"),
                    "type": art.get("type"),
                    "byline": art.get("byline"),
                    "link": (art.get("links", {}).get("web", {})).get("href")
                    if isinstance(art.get("links"), dict)
                    else None,
                }
            )
        return {
            "sport": sport,
            "league": league,
            "count": len(articles_out),
            "articles": articles_out,
        }

    def _format_rankings(self, raw: dict[str, Any], sport: str, league: str) -> dict[str, Any]:
        polls_out = []
        for poll in raw.get("rankings", []):
            ranks_out = []
            for r in poll.get("ranks", []):
                t = r.get("team", {})
                ranks_out.append(
                    {
                        "current": r.get("current"),
                        "previous": r.get("previous"),
                        "points": r.get("points"),
                        "first_place_votes": r.get("firstPlaceVotes", 0),
                        "record": r.get("recordSummary"),
                        "team": {
                            "id": t.get("id"),
                            "name": t.get("displayName") or t.get("name"),
                            "abbreviation": t.get("abbreviation"),
                        },
                    }
                )
            polls_out.append(
                {
                    "name": poll.get("name"),
                    "type": poll.get("type"),
                    "headline": poll.get("headline"),
                    "ranks": ranks_out,
                }
            )
        return {
            "sport": sport,
            "league": league,
            "polls": polls_out,
        }

    def _format_team_roster(
        self, raw: dict[str, Any], sport: str, league: str, team_id: str
    ) -> dict[str, Any]:
        athletes_out = []
        for pos_group in raw.get("athletes", []):
            pos_name = pos_group.get("position", "")
            for ath in pos_group.get("items", []):
                injuries = [inj.get("status") for inj in ath.get("injuries", [])]
                athletes_out.append(
                    {
                        "id": ath.get("id"),
                        "name": ath.get("displayName") or ath.get("fullName"),
                        "jersey": ath.get("jersey"),
                        "position_group": pos_name,
                        "position": ath.get("position", {}).get("abbreviation")
                        if isinstance(ath.get("position"), dict)
                        else ath.get("position"),
                        "experience": ath.get("experience", {}).get("years"),
                        "injuries": injuries,
                    }
                )
        return {
            "sport": sport,
            "league": league,
            "team_id": team_id,
            "team_name": raw.get("team", {}).get("displayName"),
            "season": raw.get("season", {}).get("year"),
            "count": len(athletes_out),
            "athletes": athletes_out,
        }

    def _format_depth_chart(
        self, raw: dict[str, Any], sport: str, league: str, team_id: str
    ) -> dict[str, Any]:
        depthchart_raw = raw.get("depthchart", {})
        positions_out = []

        if isinstance(depthchart_raw, list):
            for formation in depthchart_raw:
                formation_name = formation.get("name", "")
                for pos_key, pos_val in formation.get("positions", {}).items():
                    slot_athletes = []
                    if isinstance(pos_val, dict):
                        for ath_slot in pos_val.get("athletes", []):
                            ath_info = ath_slot.get("athlete", {})
                            slot_athletes.append(
                                {
                                    "slot": ath_slot.get("slot"),
                                    "rank": ath_slot.get("rank"),
                                    "athlete_id": ath_info.get("id"),
                                    "name": ath_info.get("displayName"),
                                    "jersey": ath_info.get("jersey"),
                                }
                            )
                    positions_out.append(
                        {
                            "formation": formation_name,
                            "position": pos_key.upper(),
                            "depth": slot_athletes,
                        }
                    )
        elif isinstance(depthchart_raw, dict):
            for pos_key, pos_val in depthchart_raw.items():
                slot_athletes = []
                if isinstance(pos_val, dict):
                    for ath_slot in pos_val.get("athletes", []):
                        ath_info = ath_slot.get("athlete", {})
                        slot_athletes.append(
                            {
                                "slot": ath_slot.get("slot"),
                                "rank": ath_slot.get("rank"),
                                "athlete_id": ath_info.get("id"),
                                "name": ath_info.get("displayName"),
                                "jersey": ath_info.get("jersey"),
                            }
                        )
                positions_out.append({"position": pos_key, "depth": slot_athletes})

        return {
            "sport": sport,
            "league": league,
            "team_id": team_id,
            "positions": positions_out,
        }

    def _format_team_schedule(
        self, raw: dict[str, Any], sport: str, league: str, team_id: str
    ) -> dict[str, Any]:
        events_out = []
        for ev in raw.get("events", []):
            comp = (ev.get("competitions") or [{}])[0]
            opponent = None
            for c in comp.get("competitors", []):
                if str(c.get("id")) != str(team_id):
                    opponent = c.get("team", {}).get("displayName")
            status = comp.get("status", {}).get("type", {})
            events_out.append(
                {
                    "event_id": ev.get("id"),
                    "date": ev.get("date"),
                    "matchup": ev.get("name"),
                    "opponent": opponent,
                    "status": status.get("state"),
                    "detail": status.get("detail"),
                }
            )
        return {
            "sport": sport,
            "league": league,
            "team_id": team_id,
            "team_name": raw.get("team", {}).get("displayName"),
            "season": raw.get("season", {}).get("year"),
            "count": len(events_out),
            "games": events_out,
        }

    def _format_athlete_overview(
        self, raw: dict[str, Any], sport: str, league: str, athlete_id: str
    ) -> dict[str, Any]:
        return {
            "sport": sport,
            "league": league,
            "athlete_id": athlete_id,
            "statistics": raw.get("statistics", {}),
            "next_game": raw.get("nextGame", {}),
            "game_log": raw.get("gameLog", {}),
            "rotowire_notes": raw.get("rotowire", []),
            "awards": raw.get("awards", []),
        }


# Backwards compatibility alias
TemplateClient = ESPNClient
