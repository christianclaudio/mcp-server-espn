"""Asynchronous HTTP client for ESPN public APIs.

Includes connection pooling, retries, and path sanitization.
"""

import asyncio
import ipaddress
import random
import socket
import urllib.parse
from collections.abc import Awaitable
from datetime import datetime, timezone
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


def _extract_id_from_ref(obj: Any) -> str | None:
    """Extract numeric or alphanumeric ID from a dictionary or Core API $ref URI string."""
    if not isinstance(obj, dict):
        return None
    if "id" in obj and obj["id"] is not None:
        return str(obj["id"])
    ref = obj.get("$ref")
    if isinstance(ref, str):
        path_part = ref.split("?")[0].rstrip("/")
        last_seg = path_part.split("/")[-1]
        if last_seg:
            return last_seg
    return None


class SSRFSafeAsyncTransport(httpx.AsyncHTTPTransport):
    """Async HTTP transport enforcing DNS destination validation at request connection time."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Validate destination hostname via worker thread before dispatching HTTP request."""
        hostname = request.url.host
        if hostname:
            try:
                await asyncio.to_thread(_validate_hostname_dns, hostname)
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
        core_base_url: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        raw_url = (base_url or settings.BASE_URL).rstrip("/")
        self.base_url = _validate_base_url(raw_url, check_dns=False)
        raw_core_url = (core_base_url or settings.CORE_BASE_URL).rstrip("/")
        self.core_base_url = _validate_base_url(raw_core_url, check_dns=False)
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
        base_url: str | None = None,
    ) -> dict[str, Any]:
        """Execute request with exponential backoff and randomized jitter."""
        client = await self.get_client()
        target_base = (base_url or self.base_url).rstrip("/")
        url = f"{target_base}/{path.lstrip('/')}"
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

    async def _safe_enrich(self, coro: Awaitable[Any]) -> Any:
        """Execute an optional secondary enrichment coroutine, returning None on failure."""
        try:
            return await asyncio.wait_for(coro, timeout=self.timeout)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            return None

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
        if not raw.get("predictor") and s in ("football", "basketball"):
            pred_res = await self._safe_enrich(self.get_game_predictor(s, lg, event_id))
            if isinstance(pred_res, dict) and pred_res.get("predictor"):
                p = pred_res["predictor"]
                raw["predictor"] = p.get("predictor", p) if isinstance(p, dict) else p
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
        formatted = self._format_team_detail(raw, s, lg, team_id)
        if formatted.get("next_event") is None:
            sched = await self._safe_enrich(self.get_team_schedule(s, lg, team_id))
            if isinstance(sched, dict):
                for game in sched.get("games", []):
                    if game.get("status") in ("pre", "in") or (
                        game.get("status") != "post"
                        and not str(game.get("detail", "")).startswith("Final")
                    ):
                        formatted["next_event"] = {
                            "id": game.get("event_id"),
                            "name": game.get("matchup"),
                            "date": game.get("date"),
                        }
                        break
        return formatted

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

    async def get_athlete_bio(
        self,
        sport: str,
        league: str,
        athlete_id: str,
    ) -> dict[str, Any]:
        """Fetch athlete biographical data (birthplace, college, draft, metrics)."""
        s, lg = normalize_sport_league(sport, league)
        s_san = self.sanitize_path_param(s)
        lg_san = self.sanitize_path_param(lg)
        aid_san = self.sanitize_path_param(athlete_id)
        raw = await self.request(
            "GET", f"apis/common/v3/sports/{s_san}/{lg_san}/athletes/{aid_san}/bio"
        )
        return self._format_athlete_bio(raw, s, lg, athlete_id)

    async def get_athlete_stats(
        self,
        sport: str,
        league: str,
        athlete_id: str,
        season: int | None = None,
    ) -> dict[str, Any]:
        """Fetch athlete career and season statistical splits and totals."""
        s, lg = normalize_sport_league(sport, league)
        s_san = self.sanitize_path_param(s)
        lg_san = self.sanitize_path_param(lg)
        aid_san = self.sanitize_path_param(athlete_id)
        params = {"season": season} if season is not None else None
        raw = await self.request(
            "GET",
            f"apis/common/v3/sports/{s_san}/{lg_san}/athletes/{aid_san}/stats",
            params=params,
        )
        return self._format_athlete_stats(raw, s, lg, athlete_id, season)

    async def get_athlete_gamelog(
        self,
        sport: str,
        league: str,
        athlete_id: str,
        season: int | None = None,
    ) -> dict[str, Any]:
        """Fetch game-by-game performance log for an athlete."""
        s, lg = normalize_sport_league(sport, league)
        s_san = self.sanitize_path_param(s)
        lg_san = self.sanitize_path_param(lg)
        aid_san = self.sanitize_path_param(athlete_id)
        params = {"season": season} if season is not None else None
        raw = await self.request(
            "GET",
            f"apis/common/v3/sports/{s_san}/{lg_san}/athletes/{aid_san}/gamelog",
            params=params,
        )
        return self._format_athlete_gamelog(raw, s, lg, athlete_id, season)

    async def get_athlete_splits(
        self,
        sport: str,
        league: str,
        athlete_id: str,
        season: int | None = None,
    ) -> dict[str, Any]:
        """Fetch situational split statistics for an athlete."""
        s, lg = normalize_sport_league(sport, league)
        s_san = self.sanitize_path_param(s)
        lg_san = self.sanitize_path_param(lg)
        aid_san = self.sanitize_path_param(athlete_id)
        params = {"season": season} if season is not None else None
        raw = await self.request(
            "GET",
            f"apis/common/v3/sports/{s_san}/{lg_san}/athletes/{aid_san}/splits",
            params=params,
        )
        return self._format_athlete_splits(raw, s, lg, athlete_id, season)

    async def get_leaders_by_athlete(
        self,
        sport: str,
        league: str,
        limit: int = 10,
        category: str | None = None,
        sort: str | None = None,
    ) -> dict[str, Any]:
        """Fetch statistical leaderboards by athlete across a league."""
        s, lg = normalize_sport_league(sport, league)
        s_san = self.sanitize_path_param(s)
        lg_san = self.sanitize_path_param(lg)
        params: dict[str, Any] = {"limit": limit}
        if category:
            params["category"] = category
        if sort:
            params["sort"] = sort
        raw = await self.request(
            "GET",
            f"apis/common/v3/sports/{s_san}/{lg_san}/statistics/byathlete",
            params=params,
        )
        return self._format_leaders_by_athlete(raw, s, lg, limit=limit)

    async def get_leaders_by_team(
        self,
        sport: str,
        league: str,
        limit: int = 10,
        category: str | None = None,
        sort: str | None = None,
    ) -> dict[str, Any]:
        """Fetch statistical leaderboards by team across a league."""
        s, lg = normalize_sport_league(sport, league)
        s_san = self.sanitize_path_param(s)
        lg_san = self.sanitize_path_param(lg)
        params: dict[str, Any] = {"limit": limit}
        if category:
            params["category"] = category
        if sort:
            params["sort"] = sort
        raw = await self.request(
            "GET",
            f"apis/common/v3/sports/{s_san}/{lg_san}/statistics/byteam",
            params=params,
        )
        return self._format_leaders_by_team(raw, s, lg, limit=limit)

    async def get_league_groups(
        self,
        sport: str,
        league: str,
    ) -> dict[str, Any]:
        """Fetch league division, conference, and group hierarchy."""
        s, lg = normalize_sport_league(sport, league)
        s_san = self.sanitize_path_param(s)
        lg_san = self.sanitize_path_param(lg)
        raw = await self.request("GET", f"apis/site/v2/sports/{s_san}/{lg_san}/groups")
        return self._format_league_groups(raw, s, lg)

    async def get_league_events(
        self,
        sport: str,
        league: str,
        dates: str | None = None,
    ) -> dict[str, Any]:
        """Fetch scheduled events across a league."""
        s, lg = normalize_sport_league(sport, league)
        s_san = self.sanitize_path_param(s)
        lg_san = self.sanitize_path_param(lg)
        params = {"dates": dates} if dates is not None else None
        raw = await self.request(
            "GET", f"apis/site/v2/sports/{s_san}/{lg_san}/events", params=params
        )
        return self._format_league_events(raw, s, lg, dates)

    async def get_league_draft(
        self,
        sport: str,
        league: str,
        season: int | None = None,
    ) -> dict[str, Any]:
        """Fetch league draft rounds, selections, and picks."""
        s, lg = normalize_sport_league(sport, league)
        s_san = self.sanitize_path_param(s)
        lg_san = self.sanitize_path_param(lg)
        params = {"season": season} if season is not None else None
        raw = await self.request(
            "GET", f"apis/site/v2/sports/{s_san}/{lg_san}/draft", params=params
        )
        return self._format_league_draft(raw, s, lg, season)

    async def get_scoreboard_header(
        self,
        sport: str = "football",
        league: str = "nfl",
    ) -> dict[str, Any]:
        """Fetch live ticker scoreboard header data."""
        s, lg = normalize_sport_league(sport, league)
        params = {"sport": s, "league": lg}
        raw = await self.request("GET", "apis/v2/scoreboard/header", params=params)
        return self._format_scoreboard_header(raw, s, lg)

    async def get_event_odds(
        self,
        sport: str,
        league: str,
        event_id: str,
        competition_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch provider sports betting odds, spreads, and moneylines."""
        s, lg = normalize_sport_league(sport, league)
        s_san = self.sanitize_path_param(s)
        lg_san = self.sanitize_path_param(lg)
        eid_san = self.sanitize_path_param(event_id)
        cid = competition_id or event_id
        cid_san = self.sanitize_path_param(cid)
        raw = await self.request(
            "GET",
            f"v2/sports/{s_san}/leagues/{lg_san}/events/{eid_san}/competitions/{cid_san}/odds",
            base_url=self.core_base_url,
        )
        return self._format_event_odds(raw, s, lg, event_id, cid)

    async def get_play_by_play(
        self,
        sport: str,
        league: str,
        event_id: str,
        competition_id: str | None = None,
        limit: int = 50,
        page: int = 1,
    ) -> dict[str, Any]:
        """Fetch granular game play-by-play sequence with downs, clocks, and yardage."""
        s, lg = normalize_sport_league(sport, league)
        s_san = self.sanitize_path_param(s)
        lg_san = self.sanitize_path_param(lg)
        eid_san = self.sanitize_path_param(event_id)
        cid = competition_id or event_id
        cid_san = self.sanitize_path_param(cid)
        params = {"limit": limit, "page": page}
        raw = await self.request(
            "GET",
            f"v2/sports/{s_san}/leagues/{lg_san}/events/{eid_san}/competitions/{cid_san}/plays",
            params=params,
            base_url=self.core_base_url,
        )
        return self._format_play_by_play(raw, s, lg, event_id, cid)

    async def get_game_situation(
        self,
        sport: str,
        league: str,
        event_id: str,
        competition_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch real-time in-game situation (down, distance, yardline, possession, red zone)."""
        s, lg = normalize_sport_league(sport, league)
        s_san = self.sanitize_path_param(s)
        lg_san = self.sanitize_path_param(lg)
        eid_san = self.sanitize_path_param(event_id)
        cid = competition_id or event_id
        cid_san = self.sanitize_path_param(cid)
        raw = await self.request(
            "GET",
            f"v2/sports/{s_san}/leagues/{lg_san}/events/{eid_san}/competitions/{cid_san}/situation",
            base_url=self.core_base_url,
        )
        return self._format_game_situation(raw, s, lg, event_id, cid)

    async def get_win_probabilities(
        self,
        sport: str,
        league: str,
        event_id: str,
        competition_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Fetch high-density win probability timeline curve samples."""
        s, lg = normalize_sport_league(sport, league)
        s_san = self.sanitize_path_param(s)
        lg_san = self.sanitize_path_param(lg)
        eid_san = self.sanitize_path_param(event_id)
        cid = competition_id or event_id
        cid_san = self.sanitize_path_param(cid)
        params = {"limit": limit}
        raw = await self.request(
            "GET",
            f"v2/sports/{s_san}/leagues/{lg_san}/events/{eid_san}/competitions/{cid_san}/probabilities",
            params=params,
            base_url=self.core_base_url,
        )
        return self._format_win_probabilities(raw, s, lg, event_id, cid)

    async def get_game_predictor(
        self,
        sport: str,
        league: str,
        event_id: str,
        competition_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch ESPN predictive matchup model win percentages and projected margins."""
        s, lg = normalize_sport_league(sport, league)
        s_san = self.sanitize_path_param(s)
        lg_san = self.sanitize_path_param(lg)
        eid_san = self.sanitize_path_param(event_id)
        cid = competition_id or event_id
        cid_san = self.sanitize_path_param(cid)
        raw = await self.request(
            "GET",
            f"v2/sports/{s_san}/leagues/{lg_san}/events/{eid_san}/competitions/{cid_san}/predictor",
            base_url=self.core_base_url,
        )
        return self._format_game_predictor(raw, s, lg, event_id, cid)

    async def get_calendar(
        self,
        sport: str,
        league: str,
        dates: str | None = None,
    ) -> dict[str, Any]:
        """Fetch league schedule calendar and active competition dates."""
        s, lg = normalize_sport_league(sport, league)
        s_san = self.sanitize_path_param(s)
        lg_san = self.sanitize_path_param(lg)
        if dates:
            raw = await self.request(
                "GET",
                f"v2/sports/{s_san}/leagues/{lg_san}/calendar/ondays",
                params={"dates": dates},
                base_url=self.core_base_url,
            )
        else:
            raw = await self.request(
                "GET",
                f"v2/sports/{s_san}/leagues/{lg_san}/calendar",
                base_url=self.core_base_url,
            )
            # If the index returns unresolved core $ref links, resolve calendar/ondays
            if isinstance(raw, dict) and "items" in raw:
                items = raw.get("items", [])
                if isinstance(items, list) and any(
                    isinstance(it, dict) and "$ref" in it for it in items
                ):
                    raw = await self.request(
                        "GET",
                        f"v2/sports/{s_san}/leagues/{lg_san}/calendar/ondays",
                        base_url=self.core_base_url,
                    )
        return self._format_calendar(raw, s, lg, dates)

    async def get_futures(
        self,
        sport: str,
        league: str,
        season: int | None = None,
    ) -> dict[str, Any]:
        """Fetch season futures betting markets (championship, conference, win totals)."""
        s, lg = normalize_sport_league(sport, league)
        s_san = self.sanitize_path_param(s)
        lg_san = self.sanitize_path_param(lg)
        resolved_season = season or datetime.now(timezone.utc).year
        raw = await self.request(
            "GET",
            f"v2/sports/{s_san}/leagues/{lg_san}/seasons/{resolved_season}/futures",
            base_url=self.core_base_url,
        )
        return self._format_futures(raw, s, lg, resolved_season)

    async def get_power_index(
        self,
        sport: str,
        league: str,
        season: int | None = None,
    ) -> dict[str, Any]:
        """Fetch league-wide team power index (FPI / BPI) ratings and efficiency metrics."""
        s, lg = normalize_sport_league(sport, league)
        s_san = self.sanitize_path_param(s)
        lg_san = self.sanitize_path_param(lg)
        resolved_season = season or datetime.now(timezone.utc).year
        raw, teams_res = await asyncio.gather(
            self.request(
                "GET",
                f"v2/sports/{s_san}/leagues/{lg_san}/seasons/{resolved_season}/powerindex",
                base_url=self.core_base_url,
            ),
            self._safe_enrich(self.list_teams(s, lg)),
        )
        team_map: dict[str, dict[str, Any]] = {}
        if isinstance(teams_res, dict):
            for t in teams_res.get("teams", []):
                if isinstance(t, dict) and t.get("id"):
                    team_map[str(t["id"])] = {
                        "name": t.get("name") or t.get("displayName"),
                        "abbreviation": t.get("abbreviation"),
                    }
        return self._format_power_index(raw, s, lg, resolved_season, team_map=team_map)

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
        if not predictor and winprob and isinstance(winprob, list):
            last_wp = winprob[-1] if isinstance(winprob[-1], dict) else {}
            h_raw = last_wp.get("homeWinPercentage")
            if isinstance(h_raw, (int, float)) and not isinstance(h_raw, bool):
                t_raw = last_wp.get("tiePercentage")
                t_val: float | None = None
                if isinstance(t_raw, (int, float)) and not isinstance(t_raw, bool):
                    t_val = float(t_raw)
                is_fractional = 0.0 <= h_raw <= 1.0 and (t_val is None or 0.0 <= t_val <= 1.0)
                h_pct = round(h_raw * 100.0, 1) if is_fractional else round(float(h_raw), 1)
                t_pct: float | None = None
                if t_val is not None:
                    t_pct = round(t_val * 100.0, 1) if is_fractional else round(t_val, 1)
                away_calc = 100.0 - h_pct - (t_pct or 0.0)
                a_pct = max(0.0, round(away_calc, 1))

                pred_dict: dict[str, Any] = {
                    "source": "winprobability",
                    "homeTeam": {"winPercentage": h_pct},
                    "awayTeam": {"winPercentage": a_pct},
                }
                if t_pct is not None:
                    pred_dict["tiePercentage"] = t_pct
                predictor = pred_dict
        seasonseries = raw.get("seasonseries", [])
        last_five = raw.get("lastFiveGames", [])
        injuries = raw.get("injuries", [])

        # Clean betting lines
        betting_lines = []
        for pick in pickcenter:
            if not isinstance(pick, dict):
                continue
            p_info = pick.get("provider", {})
            provider_name = p_info.get("name") if isinstance(p_info, dict) else str(p_info)
            away_raw = pick.get("awayTeamOdds")
            away_odds: dict[str, Any] = away_raw if isinstance(away_raw, dict) else {}
            home_raw = pick.get("homeTeamOdds")
            home_odds: dict[str, Any] = home_raw if isinstance(home_raw, dict) else {}
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
            t_team_raw = t.get("team")
            t_team = t_team_raw if isinstance(t_team_raw, dict) else {}
            stats_list = [
                {"name": s.get("name"), "display_value": s.get("displayValue")}
                for s in t.get("statistics", [])
            ]
            team_stats.append(
                {
                    "team_id": t_team.get("id"),
                    "team_name": t_team.get("displayName"),
                    "abbreviation": t_team.get("abbreviation"),
                    "statistics": stats_list,
                }
            )

        # Clean category leaders
        leaders_out = []
        for ldr in raw.get("leaders", []):
            if not isinstance(ldr, dict):
                continue
            # Case A: nested by team: {"team": {...}, "leaders": [category, ...]}
            if "team" in ldr and isinstance(ldr.get("leaders"), list):
                ldr_team_raw = ldr.get("team")
                ldr_team = ldr_team_raw if isinstance(ldr_team_raw, dict) else {}
                team_id = ldr_team.get("id")
                team_name = ldr_team.get("displayName") or ldr_team.get("abbreviation")
                for cat in ldr["leaders"]:
                    if not isinstance(cat, dict):
                        continue
                    cat_name = cat.get("name") or cat.get("displayName")
                    cat_display = cat.get("displayName") or cat_name
                    cat_leaders = []
                    for l_entry in cat.get("leaders", []):
                        if not isinstance(l_entry, dict):
                            continue
                        ath_raw = l_entry.get("athlete")
                        ath: dict[str, Any] = ath_raw if isinstance(ath_raw, dict) else {}
                        cat_leaders.append(
                            {
                                "display_value": l_entry.get("displayValue"),
                                "value": l_entry.get("value"),
                                "athlete_id": ath.get("id"),
                                "name": ath.get("displayName") or ath.get("fullName"),
                                "team_id": team_id,
                                "team_name": team_name,
                            }
                        )
                    leaders_out.append(
                        {
                            "category": cat_name,
                            "display_name": cat_display,
                            "team_id": team_id,
                            "team_name": team_name,
                            "leaders": cat_leaders,
                        }
                    )
            else:
                # Case B: flat category list
                cat_name = ldr.get("name") or ldr.get("displayName")
                cat_display = ldr.get("displayName") or cat_name
                cat_leaders = []
                for l_entry in ldr.get("leaders", []):
                    if not isinstance(l_entry, dict):
                        continue
                    ath_raw = l_entry.get("athlete")
                    ath = ath_raw if isinstance(ath_raw, dict) else {}
                    t_raw = l_entry.get("team")
                    t_info: dict[str, Any] = t_raw if isinstance(t_raw, dict) else {}
                    cat_leaders.append(
                        {
                            "display_value": l_entry.get("displayValue"),
                            "value": l_entry.get("value"),
                            "athlete_id": ath.get("id"),
                            "name": ath.get("displayName") or ath.get("fullName"),
                            "team_id": t_info.get("id"),
                            "team_name": t_info.get("displayName"),
                        }
                    )
                leaders_out.append(
                    {
                        "category": cat_name,
                        "display_name": cat_display,
                        "leaders": cat_leaders,
                    }
                )

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
        team_pick_map: dict[str, dict[str, Any]] = {}
        for pick in pickcenter:
            if not isinstance(pick, dict):
                continue
            for side in ("awayTeamOdds", "homeTeamOdds"):
                odds = pick.get(side, {})
                if isinstance(odds, dict) and odds.get("teamId"):
                    tid = str(odds["teamId"])
                    if tid not in team_pick_map:
                        team_pick_map[tid] = {
                            "favorite": odds.get("favorite"),
                            "underdog": odds.get("underdog"),
                            "moneyline": odds.get("moneyLine"),
                            "spread": pick.get("spread"),
                            "details": pick.get("details"),
                        }
        team_record_map: dict[str, str] = {}
        for comp in header.get("competitions", []):
            if not isinstance(comp, dict):
                continue
            for competitor in comp.get("competitors", []):
                if not isinstance(competitor, dict):
                    continue
                c_id = str(competitor.get("id") or "")
                c_team = competitor.get("team")
                if isinstance(c_team, dict) and c_team.get("id"):
                    c_id = str(c_team["id"])
                for r in competitor.get("records", []):
                    if isinstance(r, dict):
                        rec_summary = r.get("summary") or r.get("displayValue")
                        if rec_summary and c_id:
                            team_record_map[c_id] = rec_summary
                            break
        if isinstance(ats_raw, list):
            for ats_item in ats_raw:
                if isinstance(ats_item, dict):
                    ats_team_raw = ats_item.get("team")
                    ats_team = ats_team_raw if isinstance(ats_team_raw, dict) else {}
                    tid = str(ats_team.get("id")) if ats_team.get("id") else ""
                    # Record resolution
                    record_val = ats_item.get("record")
                    if (
                        not record_val
                        and isinstance(ats_item.get("records"), list)
                        and ats_item["records"]
                    ):
                        r0 = ats_item["records"][0]
                        record_val = (
                            (r0.get("summary") or r0.get("displayValue"))
                            if isinstance(r0, dict)
                            else None
                        )
                    if not record_val and tid in team_record_map:
                        record_val = team_record_map[tid]
                    pick_data = team_pick_map.get(tid, {})
                    line_val = (
                        ats_item.get("line")
                        or pick_data.get("details")
                        or (
                            str(pick_data.get("spread"))
                            if pick_data.get("spread") is not None
                            else None
                        )
                    )
                    fav_val = (
                        ats_item.get("favorite")
                        if ats_item.get("favorite") is not None
                        else pick_data.get("favorite")
                    )
                    dog_val = (
                        ats_item.get("underdog")
                        if ats_item.get("underdog") is not None
                        else pick_data.get("underdog")
                    )
                    ats_out.append(
                        {
                            "team_id": ats_team.get("id"),
                            "team_name": ats_team.get("displayName")
                            or ats_team.get("abbreviation"),
                            "favorite": fav_val,
                            "underdog": dog_val,
                            "line": line_val,
                            "record": record_val,
                            "overall_record": team_record_map.get(tid),
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

    @staticmethod
    def _format_depth_slot(idx: int, ath_slot: Any) -> dict[str, Any] | None:
        if not isinstance(ath_slot, dict):
            return None
        ath_sub = ath_slot.get("athlete") if isinstance(ath_slot.get("athlete"), dict) else {}
        ath_info = ath_sub or ath_slot
        rank_val = (
            ath_slot.get("rank")
            or (ath_info.get("rank") if isinstance(ath_info, dict) else None)
            or (idx + 1)
        )
        slot_val = (
            ath_slot.get("slot")
            or (ath_info.get("slot") if isinstance(ath_info, dict) else None)
            or str(idx + 1)
        )
        raw_jersey = (
            ath_info.get("displayJersey")
            or ath_info.get("jersey")
            or ath_info.get("jerseyNumber")
            or ath_info.get("number")
            or ath_slot.get("displayJersey")
            or ath_slot.get("jersey")
            or ath_slot.get("jerseyNumber")
            or ath_slot.get("number")
        )
        jersey_val = str(raw_jersey) if raw_jersey is not None else None
        return {
            "slot": str(slot_val),
            "rank": rank_val,
            "athlete_id": ath_info.get("id") or ath_slot.get("id"),
            "name": (
                ath_info.get("displayName")
                or ath_info.get("fullName")
                or ath_slot.get("displayName")
                or ath_slot.get("name")
            ),
            "jersey": jersey_val,
        }

    def _format_depth_chart(
        self, raw: dict[str, Any], sport: str, league: str, team_id: str
    ) -> dict[str, Any]:
        depthchart_raw = raw.get("depthchart")
        if depthchart_raw is None:
            depthchart_raw = raw.get("items")
        if depthchart_raw is None:
            depthchart_raw = raw.get("depthCharts")
        if depthchart_raw is None:
            depthchart_raw = raw.get("positions")
        if depthchart_raw is None:
            depthchart_raw = {}
        positions_out = []

        if isinstance(depthchart_raw, list):
            for formation in depthchart_raw:
                formation_name = formation.get("name", "")
                for pos_key, pos_val in formation.get("positions", {}).items():
                    slot_athletes = []
                    if isinstance(pos_val, dict):
                        for idx, ath_slot in enumerate(pos_val.get("athletes", [])):
                            slot_obj = self._format_depth_slot(idx, ath_slot)
                            if slot_obj is not None:
                                slot_athletes.append(slot_obj)
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
                    for idx, ath_slot in enumerate(pos_val.get("athletes", [])):
                        slot_obj = self._format_depth_slot(idx, ath_slot)
                        if slot_obj is not None:
                            slot_athletes.append(slot_obj)
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
        """Format team detailed metadata, records, franchise venue, and scheduled events."""
        t = raw.get("team", raw)
        next_event = t.get("nextEvent", [{}])
        first_next: dict[str, Any] = {}
        for ev in next_event if isinstance(next_event, list) else [next_event]:
            if not isinstance(ev, dict) or not ev.get("id"):
                continue
            comps = ev.get("competitions", [])
            comp = comps[0] if isinstance(comps, list) and comps else {}
            status_info = (
                comp.get("status", {}).get("type", {})
                if isinstance(comp.get("status"), dict)
                else {}
            )
            is_completed = status_info.get("completed", False) or status_info.get("state") == "post"
            if not is_completed:
                first_next = ev
                break

        record = t.get("record", {})
        record_items = record.get("items", []) if isinstance(record, dict) else []
        overall_record = (
            record_items[0].get("summary")
            if record_items
            else (record.get("overall") if isinstance(record, dict) else None)
        )
        venue_obj = t.get("venue") or raw.get("venue") or t.get("franchise", {}).get("venue", {})
        venue_name: str | None = None
        if isinstance(venue_obj, str):
            venue_name = venue_obj
        elif isinstance(venue_obj, dict):
            raw_vname = venue_obj.get("fullName") or venue_obj.get("name")
            venue_name = str(raw_vname) if raw_vname is not None else None

        return {
            "sport": sport,
            "league": league,
            "team_id": team_id,
            "name": t.get("displayName") or t.get("name"),
            "abbreviation": t.get("abbreviation"),
            "standing_summary": t.get("standingSummary"),
            "record": overall_record,
            "venue": venue_name,
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
        """Format offensive and defensive statistical categories for a team."""
        results = raw.get("results", raw) if isinstance(raw, dict) else {}
        if not isinstance(results, dict):
            results = {}
        team_stats_raw: Any = results.get("stats") or results.get("categories")
        if not team_stats_raw:
            splits_raw = results.get("splits")
            if isinstance(splits_raw, list):
                team_stats_raw = [
                    sp
                    for sp in splits_raw
                    if isinstance(sp, dict)
                    and str(sp.get("name", "")).lower() not in ("opponent", "opponents", "opp")
                ]
            elif isinstance(splits_raw, dict):
                team_stats_raw = splits_raw.get("team") or []
            else:
                team_stats_raw = results

        opponent_stats_raw = (
            results.get("opponent")
            or (raw.get("opponent") if isinstance(raw, dict) else {})
            or results.get("opponentStats")
            or (raw.get("opponentStats") if isinstance(raw, dict) else {})
            or {}
        )
        if not opponent_stats_raw and isinstance(results, dict):
            splits = (
                results.get("splits") or (raw.get("splits") if isinstance(raw, dict) else []) or []
            )
            if isinstance(splits, list):
                for sp in splits:
                    if isinstance(sp, dict) and str(sp.get("name", "")).lower() in (
                        "opponent",
                        "opponents",
                        "opp",
                    ):
                        opponent_stats_raw = sp
                        break
            elif isinstance(splits, dict):
                opponent_stats_raw = splits.get("opponent") or splits.get("opponents") or {}

        def _extract_categories(container: Any) -> list[dict[str, Any]]:
            categories_out = []
            if isinstance(container, list):
                cats = container
            elif isinstance(container, dict):
                if (
                    "stats" in container
                    and isinstance(container["stats"], list)
                    and any(
                        isinstance(x, dict) and ("value" in x or "displayValue" in x)
                        for x in container["stats"]
                    )
                ):
                    cats = [container]
                else:
                    cats = (
                        container.get("categories")
                        or container.get("stats")
                        or container.get("statistics")
                        or container.get("splits")
                        or []
                    )
                    if isinstance(cats, dict):
                        cats = [cats]
                    elif not isinstance(cats, list):
                        cats = []
            else:
                cats = []
            for cat in cats:
                if isinstance(cat, dict):
                    s_raw = cat.get("stats") or cat.get("statistics") or cat.get("items") or []
                    if isinstance(s_raw, list) and s_raw:
                        stats_list = [
                            {
                                "name": s.get("name"),
                                "display_value": s.get("displayValue") or s.get("value"),
                            }
                            for s in s_raw
                            if isinstance(s, dict)
                        ]
                    elif "value" in cat or "displayValue" in cat:
                        stats_list = [
                            {
                                "name": cat.get("name"),
                                "display_value": cat.get("displayValue") or cat.get("value"),
                            }
                        ]
                    else:
                        stats_list = []
                    categories_out.append(
                        {
                            "name": cat.get("name"),
                            "display_name": cat.get("displayName") or cat.get("name"),
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

    def _format_athlete_bio(
        self, raw: dict[str, Any], sport: str, league: str, athlete_id: str
    ) -> dict[str, Any]:
        """Format athlete biographical background and career profile."""
        return {
            "sport": sport,
            "league": league,
            "athlete_id": athlete_id,
            "bio": raw.get("bio", raw),
        }

    def _format_athlete_stats(
        self, raw: dict[str, Any], sport: str, league: str, athlete_id: str, season: int | None
    ) -> dict[str, Any]:
        """Format seasonal and career statistical categories for an athlete."""
        return {
            "sport": sport,
            "league": league,
            "athlete_id": athlete_id,
            "season": season,
            "statistics": raw.get("statistics") or raw.get("categories") or raw,
        }

    def _format_athlete_gamelog(
        self, raw: dict[str, Any], sport: str, league: str, athlete_id: str, season: int | None
    ) -> dict[str, Any]:
        """Format game-by-game log events and performance metrics for an athlete."""
        games = (
            raw.get("events") or raw.get("gameLog") or raw.get("entries") or raw.get("items") or []
        )
        count_val = 0
        if isinstance(games, list):
            count_val = len(games)
        elif isinstance(games, dict):
            if "events" in games and isinstance(games["events"], (list, dict)):
                count_val = len(games["events"])
            elif "entries" in games and isinstance(games["entries"], (list, dict)):
                count_val = len(games["entries"])
            elif "items" in games and isinstance(games["items"], (list, dict)):
                count_val = len(games["items"])
            else:
                count_val = len(games)
        return {
            "sport": sport,
            "league": league,
            "athlete_id": athlete_id,
            "season": season,
            "count": count_val,
            "games": games,
        }

    def _format_athlete_splits(
        self, raw: dict[str, Any], sport: str, league: str, athlete_id: str, season: int | None
    ) -> dict[str, Any]:
        """Format situational and venue statistical splits for an athlete."""
        split_categories = raw.get("splitCategories") or raw.get("categories") or []
        labels = raw.get("labels") or raw.get("names") or []
        categories_out = []
        if isinstance(split_categories, list):
            for sc_idx, sc in enumerate(split_categories):
                if not isinstance(sc, dict):
                    continue
                sc_name = sc.get("displayName") or sc.get("name")
                cat_labels = (
                    sc.get("labels")
                    or sc.get("names")
                    or (
                        labels[sc_idx]
                        if isinstance(labels, list)
                        and sc_idx < len(labels)
                        and isinstance(labels[sc_idx], list)
                        else None
                    )
                    or (
                        labels
                        if isinstance(labels, list) and (not labels or isinstance(labels[0], str))
                        else []
                    )
                )
                splits_list = []
                for sp in sc.get("splits", []):
                    if not isinstance(sp, dict):
                        continue
                    stats_raw = sp.get("stats", [])
                    row_labels = sp.get("labels") or sp.get("names") or cat_labels
                    stat_map: dict[str, Any] = {}
                    if isinstance(stats_raw, list) and isinstance(row_labels, list):
                        for i, val in enumerate(stats_raw):
                            lbl = row_labels[i] if i < len(row_labels) else f"stat_{i}"
                            stat_map[str(lbl)] = val
                    splits_list.append(
                        {
                            "name": sp.get("displayName") or sp.get("name"),
                            "abbreviation": sp.get("abbreviation"),
                            "stats": stat_map if stat_map else stats_raw,
                        }
                    )
                if splits_list:
                    categories_out.append(
                        {
                            "category": sc_name,
                            "splits": splits_list,
                        }
                    )
        return {
            "sport": sport,
            "league": league,
            "athlete_id": athlete_id,
            "season": season,
            "labels": labels,
            "splits": categories_out
            if categories_out
            else (raw.get("splits") or raw.get("categories") or raw),
        }

    def _format_leaders_by_athlete(
        self, raw: dict[str, Any], sport: str, league: str, limit: int = 10
    ) -> dict[str, Any]:
        """Format individual athlete statistical leaderboards across a league."""
        cats_raw = raw.get("categories") or []
        trimmed_cats = []
        if isinstance(cats_raw, list):
            for cat in cats_raw:
                if not isinstance(cat, dict):
                    continue
                raw_leaders = cat.get("leaders", [])
                valid_leaders = (
                    [item for item in raw_leaders if isinstance(item, dict)]
                    if isinstance(raw_leaders, list)
                    else []
                )
                leaders_list = []
                for idx, item in enumerate(valid_leaders[:limit]):
                    ath_ref = item.get("athlete", {})
                    team_ref = item.get("team", {})
                    ath_id = _extract_id_from_ref(ath_ref)
                    team_id = _extract_id_from_ref(team_ref)
                    leaders_list.append(
                        {
                            "rank": idx + 1,
                            "display_value": item.get("displayValue"),
                            "value": item.get("value"),
                            "athlete_id": ath_id,
                            "athlete_name": (
                                ath_ref.get("displayName") or ath_ref.get("fullName")
                                if isinstance(ath_ref, dict)
                                else None
                            ),
                            "team_id": team_id,
                        }
                    )
                if leaders_list:
                    trimmed_cats.append(
                        {
                            "name": cat.get("name"),
                            "display_name": cat.get("displayName"),
                            "abbreviation": cat.get("abbreviation"),
                            "leaders": leaders_list,
                        }
                    )
        fallback = raw.get("athletes") or raw.get("statistics") or raw.get("items") or []
        trimmed_fallback = fallback[:limit] if isinstance(fallback, list) else fallback
        final_cats = trimmed_cats if trimmed_cats else trimmed_fallback
        res_ath: dict[str, Any] = {
            "sport": sport,
            "league": league,
            "count": len(trimmed_cats)
            if trimmed_cats
            else (len(trimmed_fallback) if isinstance(trimmed_fallback, list) else 1),
            "categories": final_cats,
        }
        if not trimmed_cats:
            res_ath["leaders"] = trimmed_fallback
        return res_ath

    def _format_leaders_by_team(
        self, raw: dict[str, Any], sport: str, league: str, limit: int = 10
    ) -> dict[str, Any]:
        """Format team-level statistical leaderboards across a league."""
        cats_raw = raw.get("categories") or []
        trimmed_cats = []
        if isinstance(cats_raw, list):
            for cat in cats_raw:
                if not isinstance(cat, dict):
                    continue
                leaders_list = []
                raw_leaders = cat.get("leaders", [])
                valid_leaders = (
                    [item for item in raw_leaders if isinstance(item, dict)]
                    if isinstance(raw_leaders, list)
                    else []
                )
                for idx, item in enumerate(valid_leaders[:limit]):
                    team_ref = item.get("team", {})
                    team_id = _extract_id_from_ref(team_ref)
                    leaders_list.append(
                        {
                            "rank": idx + 1,
                            "display_value": item.get("displayValue"),
                            "value": item.get("value"),
                            "team_id": team_id,
                            "team_name": (
                                team_ref.get("displayName") if isinstance(team_ref, dict) else None
                            ),
                        }
                    )
                if leaders_list:
                    trimmed_cats.append(
                        {
                            "name": cat.get("name"),
                            "display_name": cat.get("displayName"),
                            "abbreviation": cat.get("abbreviation"),
                            "leaders": leaders_list,
                        }
                    )
        fallback = raw.get("teams") or raw.get("statistics") or raw.get("items") or []
        trimmed_fallback = fallback[:limit] if isinstance(fallback, list) else fallback
        final_cats = trimmed_cats if trimmed_cats else trimmed_fallback
        res_team: dict[str, Any] = {
            "sport": sport,
            "league": league,
            "count": len(trimmed_cats)
            if trimmed_cats
            else (len(trimmed_fallback) if isinstance(trimmed_fallback, list) else 1),
            "categories": final_cats,
        }
        if not trimmed_cats:
            res_team["leaders"] = trimmed_fallback
        return res_team

    def _format_league_groups(self, raw: dict[str, Any], sport: str, league: str) -> dict[str, Any]:
        """Format league division, conference, and structural group hierarchies."""
        groups = raw.get("groups") or []
        return {
            "sport": sport,
            "league": league,
            "count": len(groups) if isinstance(groups, list) else 1,
            "groups": groups,
        }

    def _format_league_events(
        self, raw: dict[str, Any], sport: str, league: str, dates: str | None
    ) -> dict[str, Any]:
        """Format scheduled league competition events."""
        events = raw.get("events") or []
        return {
            "sport": sport,
            "league": league,
            "dates": dates,
            "count": len(events) if isinstance(events, list) else 1,
            "events": events,
        }

    def _format_league_draft(
        self, raw: dict[str, Any], sport: str, league: str, season: int | None
    ) -> dict[str, Any]:
        """Format league draft selections, rounds, and player picks."""
        draft = raw.get("draft") or raw.get("picks") or raw.get("rounds") or raw
        return {
            "sport": sport,
            "league": league,
            "season": season,
            "draft": draft,
        }

    def _format_scoreboard_header(
        self, raw: dict[str, Any], sport: str, league: str
    ) -> dict[str, Any]:
        """Format live ticker scoreboard header data across games."""
        sports_data = raw.get("sports") or raw.get("leagues") or raw
        return {
            "sport": sport,
            "league": league,
            "sports": sports_data,
        }

    def _format_event_odds(
        self, raw: dict[str, Any], sport: str, league: str, event_id: str, competition_id: str
    ) -> dict[str, Any]:
        """Format event odds and sportsbook betting lines."""
        odds = raw.get("items") if isinstance(raw.get("items"), list) else raw
        return {
            "sport": sport,
            "league": league,
            "event_id": event_id,
            "competition_id": competition_id,
            "odds": odds,
        }

    def _format_play_by_play(
        self, raw: dict[str, Any], sport: str, league: str, event_id: str, competition_id: str
    ) -> dict[str, Any]:
        """Format chronological play-by-play drive and scoring actions."""
        plays = raw.get("items") or []
        return {
            "sport": sport,
            "league": league,
            "event_id": event_id,
            "competition_id": competition_id,
            "count": len(plays) if isinstance(plays, list) else 1,
            "plays": plays,
        }

    def _format_game_situation(
        self, raw: dict[str, Any], sport: str, league: str, event_id: str, competition_id: str
    ) -> dict[str, Any]:
        """Format real-time in-game possession, down, distance, and field position."""
        return {
            "sport": sport,
            "league": league,
            "event_id": event_id,
            "competition_id": competition_id,
            "situation": raw,
        }

    def _format_win_probabilities(
        self, raw: dict[str, Any], sport: str, league: str, event_id: str, competition_id: str
    ) -> dict[str, Any]:
        """Format live and historical win probability curves across game progression."""
        probs = raw.get("items") or []
        return {
            "sport": sport,
            "league": league,
            "event_id": event_id,
            "competition_id": competition_id,
            "count": len(probs) if isinstance(probs, list) else 1,
            "probabilities": probs,
        }

    def _format_game_predictor(
        self, raw: dict[str, Any], sport: str, league: str, event_id: str, competition_id: str
    ) -> dict[str, Any]:
        """Format pre-game and in-game matchup predictor and projected chance of winning."""
        return {
            "sport": sport,
            "league": league,
            "event_id": event_id,
            "competition_id": competition_id,
            "predictor": raw,
        }

    def _format_calendar(
        self, raw: dict[str, Any] | list[Any], sport: str, league: str, dates: str | None
    ) -> dict[str, Any]:
        """Format league calendar schedule dates and active competition windows."""
        raw_dict = raw if isinstance(raw, dict) else {}
        cal = raw_dict.get("eventDate") or raw_dict.get("sections") or raw
        active_dates = []
        if isinstance(raw_dict.get("eventDate"), dict):
            active_dates = raw_dict["eventDate"].get("dates", [])
        elif isinstance(raw_dict.get("dates"), list):
            active_dates = raw_dict["dates"]
        elif isinstance(raw_dict.get("items"), list) and all(
            isinstance(x, str) for x in raw_dict["items"]
        ):
            active_dates = raw_dict["items"]

        start_date = raw_dict.get("startDate") or (
            raw_dict.get("eventDate", {}).get("startDate")
            if isinstance(raw_dict.get("eventDate"), dict)
            else None
        )
        end_date = raw_dict.get("endDate") or (
            raw_dict.get("eventDate", {}).get("endDate")
            if isinstance(raw_dict.get("eventDate"), dict)
            else None
        )
        sections = raw_dict.get("sections") if isinstance(raw_dict.get("sections"), list) else []

        def _clean_calendar_item(it: Any) -> Any:
            if isinstance(it, dict) and "$ref" in it:
                ref_url = it["$ref"]
                segment = str(ref_url).split("?")[0].rstrip("/").split("/")[-1]
                return {"type": segment, "ref": ref_url}
            return it

        calendar_out: Any = cal
        if isinstance(cal, dict) and "items" in cal:
            clean_items = [_clean_calendar_item(it) for it in cal.get("items", [])]
            calendar_out = {**cal, "items": clean_items}
        elif isinstance(cal, list):
            calendar_out = [_clean_calendar_item(it) for it in cal]

        return {
            "sport": sport,
            "league": league,
            "dates": dates,
            "start_date": start_date,
            "end_date": end_date,
            "active_dates": active_dates,
            "sections": sections,
            "calendar": calendar_out,
        }

    def _format_futures(
        self, raw: dict[str, Any], sport: str, league: str, season: int
    ) -> dict[str, Any]:
        """Format championship and season outright futures betting markets."""
        futures = raw.get("items") if isinstance(raw.get("items"), list) else raw
        return {
            "sport": sport,
            "league": league,
            "season": season,
            "futures": futures,
        }

    def _format_power_index(
        self,
        raw: dict[str, Any],
        sport: str,
        league: str,
        season: int,
        team_map: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Format team power index (FPI / BPI) ratings and efficiency metrics."""
        raw_items_val = raw.get("items") if isinstance(raw, dict) else None
        raw_items: list[Any] = (
            raw_items_val
            if isinstance(raw_items_val, list)
            else ([raw] if (isinstance(raw, dict) and raw) else [])
        )
        formatted_items = []
        for it in raw_items:
            if not isinstance(it, dict):
                continue
            item_copy = dict(it)
            team_raw = it.get("team")
            if isinstance(team_raw, dict):
                team_id = _extract_id_from_ref(team_raw)
                team_info = {
                    "id": team_id,
                    "name": team_raw.get("displayName") or team_raw.get("name"),
                    "abbreviation": team_raw.get("abbreviation"),
                }
                if team_map and team_id and team_id in team_map:
                    t_meta = team_map[team_id]
                    team_info["name"] = team_info["name"] or t_meta.get("name")
                    team_info["abbreviation"] = team_info["abbreviation"] or t_meta.get(
                        "abbreviation"
                    )
                item_copy["team"] = team_info
            formatted_items.append(item_copy)
        if isinstance(raw_items_val, list):
            p_out: Any = formatted_items
        elif formatted_items:
            p_out = formatted_items
        else:
            p_out = raw
        return {
            "sport": sport,
            "league": league,
            "season": season,
            "power_index": p_out,
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
