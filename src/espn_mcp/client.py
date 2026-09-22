"""Asynchronous HTTP client for ESPN public APIs.

Includes connection pooling, retries, and path sanitization.
"""

import asyncio
import ipaddress
import random
import socket
import urllib.parse
from typing import Any
from urllib.parse import urlparse

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


def _validate_base_url(
    url: str,
    allowed_hosts_str: str | None = None,
    check_dns: bool = False,
) -> str:
    """Validate target base URL against SSRF, loopback, private IPs, and DNS rebinding."""
    if not url:
        return settings.BASE_URL.rstrip("/")

    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        raise ValueError("Only HTTPS is permitted for base URL.")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Invalid base URL: missing hostname.")

    if (
        hostname in {"localhost", "127.0.0.1", "::1"}
        or hostname.endswith(".local")
        or hostname.endswith(".internal")
    ):
        raise ValueError(f"Blocked internal/loopback hostname in base URL: {hostname}")

    allowed_raw = allowed_hosts_str if allowed_hosts_str is not None else settings.ALLOWED_HOSTS
    if allowed_raw.strip():
        allowed = {h.strip().lower() for h in allowed_raw.split(",") if h.strip()}
        if hostname.lower() not in allowed:
            raise ValueError(f"Hostname '{hostname}' is not in allowed hosts allowlist.")

    try:
        ip = ipaddress.ip_address(hostname)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or not ip.is_global
        ):
            raise ValueError(f"Blocked private/reserved IP address in base URL: {hostname}")
        return url.rstrip("/")
    except ValueError as e:
        if "Blocked" in str(e):
            raise

    if check_dns:
        _validate_hostname_dns(hostname)

    return url.rstrip("/")


def _validate_hostname_dns(hostname: str) -> None:
    """Validate resolved DNS IP addresses to defend against private IP binding and DNS rebinding."""
    # Allow canonical RFC 2606 reserved example domain for placeholder templates/tests
    if hostname == "example.com" or hostname.endswith(".example.com"):
        return

    try:
        ip = ipaddress.ip_address(hostname)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or not ip.is_global
        ):
            raise ValueError(f"Blocked private/reserved IP address: {hostname}")
        return
    except ValueError as e:
        if "Blocked" in str(e):
            raise

    try:
        resolved_addrs = socket.getaddrinfo(hostname, None)
        for _, _, _, _, sockaddr in resolved_addrs:
            resolved_ip = ipaddress.ip_address(sockaddr[0])
            if (
                resolved_ip.is_private
                or resolved_ip.is_loopback
                or resolved_ip.is_link_local
                or resolved_ip.is_multicast
                or resolved_ip.is_reserved
                or not resolved_ip.is_global
            ):
                msg = (
                    f"Blocked hostname '{hostname}' resolving to private/reserved IP: {sockaddr[0]}"
                )
                raise ValueError(msg)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve hostname in base URL: {hostname}") from exc


class SSRFSafeAsyncTransport(httpx.AsyncHTTPTransport):
    """Async HTTP transport enforcing DNS destination validation at request connection time."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        hostname = request.url.host
        if hostname:
            try:
                _validate_hostname_dns(hostname)
            except ValueError as exc:
                raise ESPNConnectionError(
                    f"SSRF validation blocked request to {hostname}: {exc}"
                ) from exc
        return await super().handle_async_request(request)


class ESPNClient:
    """Hardened async client for querying ESPN public REST endpoints."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        raw_url = (base_url or settings.BASE_URL).rstrip("/")
        self.base_url = _validate_base_url(raw_url, check_dns=False)
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
            transport = SSRFSafeAsyncTransport(
                verify=True,
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
            )
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=httpx.Timeout(self.timeout),
                transport=transport,
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

                if 400 <= response.status_code < 500:
                    raise ESPNValidationError(
                        f"HTTP {response.status_code}: ESPN rejected the request for '{path}'."
                    )

                response.raise_for_status()
                if not response.content:
                    return {}
                return response.json()  # type: ignore[no-any-return]

            except httpx.RequestError as exc:
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

    async def search(
        self,
        query: str,
        type: str = "player",
        limit: int = 5,
    ) -> dict[str, Any]:
        """Search ESPN's entity index by name for athletes, teams, or leagues."""
        params: dict[str, Any] = {
            "query": query,
            "type": type,
            "limit": limit,
        }
        raw = await self.request("GET", "apis/common/v3/search", params=params)
        return self._format_search(raw, query, type)

    async def list_teams(
        self,
        sport: str,
        league: str,
    ) -> dict[str, Any]:
        """Fetch all teams in a given sport and league."""
        s, lg = normalize_sport_league(sport, league)
        s_san = self.sanitize_path_param(s)
        lg_san = self.sanitize_path_param(lg)
        raw = await self.request("GET", f"apis/site/v2/sports/{s_san}/{lg_san}/teams")
        return self._format_teams_list(raw, s, lg)

    async def get_team(
        self,
        sport: str,
        league: str,
        team_id: str,
    ) -> dict[str, Any]:
        """Fetch detailed information for a single team."""
        s, lg = normalize_sport_league(sport, league)
        s_san = self.sanitize_path_param(s)
        lg_san = self.sanitize_path_param(lg)
        t_san = self.sanitize_path_param(team_id)
        raw = await self.request("GET", f"apis/site/v2/sports/{s_san}/{lg_san}/teams/{t_san}")
        return self._format_team_detail(raw, s, lg, team_id)

    async def get_team_statistics(
        self,
        sport: str,
        league: str,
        team_id: str,
    ) -> dict[str, Any]:
        """Fetch team and opponent season statistics."""
        s, lg = normalize_sport_league(sport, league)
        s_san = self.sanitize_path_param(s)
        lg_san = self.sanitize_path_param(lg)
        t_san = self.sanitize_path_param(team_id)
        raw = await self.request(
            "GET", f"apis/site/v2/sports/{s_san}/{lg_san}/teams/{t_san}/statistics"
        )
        return self._format_team_statistics(raw, s, lg, team_id)

    async def get_transactions(
        self,
        sport: str,
        league: str,
        limit: int = 25,
    ) -> dict[str, Any]:
        """Fetch recent league player transactions."""
        s, lg = normalize_sport_league(sport, league)
        s_san = self.sanitize_path_param(s)
        lg_san = self.sanitize_path_param(lg)
        params: dict[str, Any] = {"limit": limit}
        raw = await self.request(
            "GET", f"apis/site/v2/sports/{s_san}/{lg_san}/transactions", params=params
        )
        return self._format_transactions(raw, s, lg)

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
                """Format raw competitor payload into normalized team dictionary."""
                if not c:
                    return {}
                raw_team = c.get("team")
                t: dict[str, Any] = raw_team if isinstance(raw_team, dict) else {}
                recs = c.get("records")
                rec_entry = recs[0] if isinstance(recs, list) and recs else {}
                rec = rec_entry.get("summary", "") if isinstance(rec_entry, dict) else ""
                probs = c.get("probables")
                prob_entry = probs[0] if isinstance(probs, list) and probs else {}
                athlete_info = prob_entry.get("athlete") if isinstance(prob_entry, dict) else {}
                probables = (
                    athlete_info.get("displayName") if isinstance(athlete_info, dict) else None
                )
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
            away_odds = (
                pick.get("awayTeamOdds") if isinstance(pick.get("awayTeamOdds"), dict) else {}
            )
            home_odds = (
                pick.get("homeTeamOdds") if isinstance(pick.get("homeTeamOdds"), dict) else {}
            )
            betting_lines.append(
                {
                    "provider": provider_name,
                    "details": pick.get("details"),
                    "over_under": pick.get("overUnder"),
                    "spread": pick.get("spread"),
                    "away_moneyline": away_odds.get("moneyLine"),
                    "home_moneyline": home_odds.get("moneyLine"),
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

        # Clean category leaders
        leaders_out = []
        for ldr in raw.get("leaders", []):
            cat_name = ldr.get("name") or ldr.get("displayName")
            cat_leaders = []
            for l_entry in ldr.get("leaders", []):
                ath = l_entry.get("athlete", {})
                cat_leaders.append(
                    {
                        "display_value": l_entry.get("displayValue"),
                        "athlete_id": ath.get("id"),
                        "name": ath.get("displayName") or ath.get("fullName"),
                        "team_id": l_entry.get("team", {}).get("id"),
                    }
                )
            leaders_out.append({"category": cat_name, "leaders": cat_leaders})

        # Scoring plays
        scoring_plays_out = []
        for sp in raw.get("scoringPlays", []):
            period_val = (
                sp.get("period", {}).get("number")
                if isinstance(sp.get("period"), dict)
                else sp.get("period")
            )
            clock_val = (
                sp.get("clock", {}).get("displayValue")
                if isinstance(sp.get("clock"), dict)
                else sp.get("clock")
            )
            type_val = (
                sp.get("type", {}).get("text")
                if isinstance(sp.get("type"), dict)
                else sp.get("type")
            )
            scoring_plays_out.append(
                {
                    "period": period_val,
                    "clock": clock_val,
                    "type": type_val,
                    "text": sp.get("text"),
                    "away_score": sp.get("awayScore"),
                    "home_score": sp.get("homeScore"),
                    "team_id": sp.get("team", {}).get("id")
                    if isinstance(sp.get("team"), dict)
                    else None,
                }
            )

        # Drives summary
        drives_raw = raw.get("drives", {})
        drives_out: dict[str, Any] = {}
        if isinstance(drives_raw, dict):
            current_drive = drives_raw.get("current")
            previous_drives = drives_raw.get("previous", [])
            start_period = (
                current_drive.get("start", {}).get("period", {}).get("number")
                if isinstance(current_drive, dict) and isinstance(current_drive.get("start"), dict)
                else None
            )
            drives_out = {
                "current": (
                    {
                        "description": current_drive.get("description"),
                        "plays": current_drive.get("plays"),
                        "yards": current_drive.get("yards"),
                        "start_period": start_period,
                    }
                    if isinstance(current_drive, dict)
                    else None
                ),
                "count": len(previous_drives) if isinstance(previous_drives, list) else 0,
            }

        # Against The Spread (ATS)
        ats_raw = raw.get("againstTheSpread", [])
        ats_out = []
        if isinstance(ats_raw, list):
            for ats_item in ats_raw:
                if isinstance(ats_item, dict):
                    team_info = ats_item.get("team", {})
                    ats_out.append(
                        {
                            "team_id": team_info.get("id"),
                            "team_name": team_info.get("displayName"),
                            "favorite": ats_item.get("favorite"),
                            "underdog": ats_item.get("underdog"),
                            "line": ats_item.get("line"),
                            "record": ats_item.get("record"),
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
            "leaders": leaders_out,
            "scoring_plays": scoring_plays_out,
            "drives": drives_out,
            "against_the_spread": ats_out,
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

        coach_raw = raw.get("coach", [])
        coach_out: list[dict[str, Any]] = []
        if isinstance(coach_raw, list):
            for c in coach_raw:
                if isinstance(c, dict):
                    first = c.get("firstName", "")
                    last = c.get("lastName", "")
                    full = f"{first} {last}".strip() or c.get("displayName")
                    coach_out.append(
                        {
                            "id": c.get("id"),
                            "name": full,
                            "experience": c.get("experience"),
                        }
                    )
        elif isinstance(coach_raw, dict):
            first = coach_raw.get("firstName", "")
            last = coach_raw.get("lastName", "")
            full = f"{first} {last}".strip() or coach_raw.get("displayName")
            coach_out.append(
                {
                    "id": coach_raw.get("id"),
                    "name": full,
                    "experience": coach_raw.get("experience"),
                }
            )

        return {
            "sport": sport,
            "league": league,
            "team_id": team_id,
            "team_name": raw.get("team", {}).get("displayName"),
            "season": raw.get("season", {}).get("year"),
            "coach": coach_out,
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
                            ath_info = ath_slot.get("athlete") or ath_slot
                            slot_athletes.append(
                                {
                                    "slot": ath_slot.get("slot"),
                                    "rank": ath_slot.get("rank"),
                                    "athlete_id": ath_info.get("id") or ath_slot.get("id"),
                                    "name": ath_info.get("displayName")
                                    or ath_info.get("fullName")
                                    or ath_slot.get("displayName")
                                    or ath_slot.get("name"),
                                    "jersey": ath_info.get("jersey") or ath_slot.get("jersey"),
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
                        ath_info = ath_slot.get("athlete") or ath_slot
                        slot_athletes.append(
                            {
                                "slot": ath_slot.get("slot"),
                                "rank": ath_slot.get("rank"),
                                "athlete_id": ath_info.get("id") or ath_slot.get("id"),
                                "name": ath_info.get("displayName")
                                or ath_info.get("fullName")
                                or ath_slot.get("displayName")
                                or ath_slot.get("name"),
                                "jersey": ath_info.get("jersey") or ath_slot.get("jersey"),
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

    def _format_search(self, raw: dict[str, Any], query: str, type_filter: str) -> dict[str, Any]:
        items_out = []
        raw_items = raw.get("items") or raw.get("results") or []
        for item in raw_items:
            if isinstance(item, dict):
                link_obj = item.get("link")
                if isinstance(link_obj, dict):
                    web_link = link_obj.get("web")
                elif isinstance(link_obj, str):
                    web_link = link_obj
                else:
                    web_link = None
                items_out.append(
                    {
                        "id": item.get("id"),
                        "name": item.get("displayName") or item.get("name"),
                        "type": item.get("type"),
                        "description": item.get("description"),
                        "league": item.get("league"),
                        "sport": item.get("sport"),
                        "link": web_link,
                    }
                )
        return {
            "query": query,
            "type": type_filter,
            "count": len(items_out),
            "items": items_out,
        }

    def _format_teams_list(self, raw: dict[str, Any], sport: str, league: str) -> dict[str, Any]:
        teams_raw = raw.get("teams", [])
        if not teams_raw and raw.get("sports"):
            leagues = raw["sports"][0].get("leagues", [])
            if leagues:
                teams_raw = leagues[0].get("teams", [])

        teams_out = []
        for entry in teams_raw:
            t = entry.get("team", entry) if isinstance(entry, dict) else {}
            if isinstance(t, dict) and t.get("id"):
                teams_out.append(
                    {
                        "id": t.get("id"),
                        "name": t.get("displayName") or t.get("name"),
                        "abbreviation": t.get("abbreviation"),
                        "location": t.get("location"),
                        "nickname": t.get("nickname"),
                        "color": t.get("color"),
                    }
                )
        return {
            "sport": sport,
            "league": league,
            "count": len(teams_out),
            "teams": teams_out,
        }

    def _format_team_detail(
        self, raw: dict[str, Any], sport: str, league: str, team_id: str
    ) -> dict[str, Any]:
        t = raw.get("team", raw)
        next_event = t.get("nextEvent", [{}])
        first_next = (
            next_event[0]
            if isinstance(next_event, list) and next_event
            else (next_event if isinstance(next_event, dict) else {})
        )
        record = t.get("record", {})
        record_items = record.get("items", []) if isinstance(record, dict) else []
        overall_record = (
            record_items[0].get("summary")
            if record_items
            else (record.get("overall") if isinstance(record, dict) else None)
        )

        return {
            "sport": sport,
            "league": league,
            "team_id": team_id,
            "name": t.get("displayName") or t.get("name"),
            "abbreviation": t.get("abbreviation"),
            "standing_summary": t.get("standingSummary"),
            "record": overall_record,
            "venue": (
                t.get("venue", {}).get("fullName") if isinstance(t.get("venue"), dict) else None
            ),
            "next_event": (
                {
                    "id": first_next.get("id"),
                    "name": first_next.get("name"),
                    "date": first_next.get("date"),
                }
                if first_next.get("id")
                else None
            ),
        }

    def _format_team_statistics(
        self, raw: dict[str, Any], sport: str, league: str, team_id: str
    ) -> dict[str, Any]:
        results = raw.get("results", raw)
        team_stats_raw = results.get("stats", {}) if isinstance(results, dict) else {}
        opponent_stats_raw = results.get("opponent", {}) if isinstance(results, dict) else {}

        def _extract_categories(container: Any) -> list[dict[str, Any]]:
            categories_out = []
            cats = container.get("categories", []) if isinstance(container, dict) else []
            for cat in cats:
                if isinstance(cat, dict):
                    stats_list = [
                        {"name": s.get("name"), "display_value": s.get("displayValue")}
                        for s in cat.get("stats", [])
                        if isinstance(s, dict)
                    ]
                    categories_out.append(
                        {
                            "name": cat.get("name"),
                            "display_name": cat.get("displayName"),
                            "stats": stats_list,
                        }
                    )
            return categories_out

        return {
            "sport": sport,
            "league": league,
            "team_id": team_id,
            "team_stats": _extract_categories(team_stats_raw),
            "opponent_stats": _extract_categories(opponent_stats_raw),
        }

    def _format_transactions(self, raw: dict[str, Any], sport: str, league: str) -> dict[str, Any]:
        tx_raw = raw.get("transactions") or raw.get("items") or []
        tx_out = []
        for item in tx_raw:
            if isinstance(item, dict):
                team_info = item.get("team", {}) if isinstance(item.get("team"), dict) else {}
                tx_out.append(
                    {
                        "date": item.get("date"),
                        "description": item.get("description"),
                        "team_id": team_info.get("id"),
                        "team_name": team_info.get("displayName") or team_info.get("name"),
                    }
                )
        return {
            "sport": sport,
            "league": league,
            "count": len(tx_out),
            "transactions": tx_out,
        }


# Backwards compatibility alias
TemplateClient = ESPNClient

# Default shared client instance
default_client = ESPNClient()


def get_client() -> ESPNClient:
    """Return active client, checking server.client for test monkeypatching."""
    try:
        import espn_mcp.server as srv

        if hasattr(srv, "client") and srv.client is not None:
            return srv.client
    except (ImportError, AttributeError):
        pass
    return default_client
