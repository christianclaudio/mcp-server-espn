"""Asynchronous HTTP client for ESPN public APIs.

Includes connection pooling, retries, and path sanitization.
"""

import asyncio
import ipaddress
import random
import socket
import urllib.parse
from collections import Counter
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


def _is_terminal_event(
    state: str | None,
    detail: str | None,
    completed: bool = False,
) -> bool:
    """Determine whether an event is completed, cancelled, postponed, or terminal."""
    if completed:
        return True
    stat = str(state or "").lower()
    det = str(detail or "").lower()
    if stat in ("post", "canceled", "cancelled", "postponed", "suspended"):
        return True
    if any(k in det for k in ("cancel", "postpone")):
        return True
    is_upcoming_or_live = stat in ("pre", "in") or any(
        term in stat for term in ("sched", "live", "progress")
    )
    is_final_detail = (
        det in ("final", "f")
        or det.startswith("final/")
        or det.startswith("final -")
        or det.startswith("final:")
    )
    if is_final_detail and not is_upcoming_or_live:
        return True
    return False


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


_NFL_LEADER_CATEGORY_MAP: dict[str, tuple[str, str]] = {
    "passing": ("offense", "passing.passingYards:desc"),
    "passingyards": ("offense", "passing.passingYards:desc"),
    "passingtouchdowns": ("offense", "passing.passingTouchdowns:desc"),
    "rushing": ("offense", "rushing.rushingYards:desc"),
    "rushingyards": ("offense", "rushing.rushingYards:desc"),
    "rushingtouchdowns": ("offense", "rushing.rushingTouchdowns:desc"),
    "receiving": ("offense", "receiving.receivingYards:desc"),
    "receivingyards": ("offense", "receiving.receivingYards:desc"),
    "receivingtouchdowns": ("offense", "receiving.receivingTouchdowns:desc"),
    "defensive": ("defense", "defensive.totalTackles:desc"),
    "tackles": ("defense", "defensive.totalTackles:desc"),
    "sacks": ("defense", "defensive.sacks:desc"),
    "interceptions": ("defense", "interceptions.interceptions:desc"),
    "kicking": ("specialTeams", "kicking.fieldGoalsMade:desc"),
    "punting": ("specialTeams", "punting.punts:desc"),
}


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
        ats_raw = raw.get("againstTheSpread")
        if isinstance(ats_raw, list):
            header = raw.get("header")
            header_dict = header if isinstance(header, dict) else {}
            season_info = header_dict.get("season")
            season_dict = season_info if isinstance(season_info, dict) else {}
            raw_yr = season_dict.get("year")
            raw_type = season_dict.get("type")
            season_yr: int | None = None
            season_type: int | None = None
            if raw_yr is not None and raw_type is not None:
                try:
                    season_yr = int(raw_yr)
                    season_type = int(raw_type)
                except (ValueError, TypeError):
                    season_yr = None
                    season_type = None

            if season_yr is not None and season_type is not None:
                targets: list[tuple[dict[str, Any], str]] = []
                for ats_item in ats_raw:
                    if isinstance(ats_item, dict) and not ats_item.get("record"):
                        t_obj = ats_item.get("team")
                        t_id = (
                            str(t_obj.get("id"))
                            if isinstance(t_obj, dict) and t_obj.get("id")
                            else ""
                        )
                        if t_id:
                            targets.append((ats_item, t_id))
                if targets:
                    results = await asyncio.gather(
                        *(
                            self._enrich_ats_record(s_san, lg_san, season_yr, season_type, t_id)
                            for _, t_id in targets
                        ),
                        return_exceptions=True,
                    )
                    for (item, _), rec_res in zip(targets, results, strict=True):
                        if isinstance(rec_res, str) and rec_res:
                            item["record"] = rec_res
        return self._format_game_summary(raw, s, lg, event_id)

    async def _enrich_ats_record(
        self, sport: str, league: str, season: int, season_type: int, team_id: str
    ) -> str | None:
        """Fetch team ATS spread record from core odds-records if available."""
        raw = await self._safe_enrich(
            self.request(
                "GET",
                f"v2/sports/{sport}/leagues/{league}/seasons/{season}/types/{season_type}/teams/{team_id}/odds-records",
                base_url=self.core_base_url,
            )
        )
        if isinstance(raw, dict):
            items = raw.get("items")
            if isinstance(items, list):
                for it in items:
                    if isinstance(it, dict) and it.get("type") in ("spreadOverall", "atsOverall"):
                        stats_raw = it.get("stats")
                        if not isinstance(stats_raw, list):
                            continue
                        stats_dict = {
                            str(s.get("displayName") or s.get("type") or s.get("abbreviation")): (
                                s.get("displayValue") or s.get("value")
                            )
                            for s in stats_raw
                            if isinstance(s, dict)
                        }
                        raw_w = stats_dict.get("Wins") or stats_dict.get("W")
                        raw_l = stats_dict.get("Losses") or stats_dict.get("L")
                        if raw_w is None and raw_l is None:
                            continue
                        w = raw_w if raw_w is not None else "0"
                        l_val = raw_l if raw_l is not None else "0"
                        raw_p = (
                            stats_dict.get("Pushes")
                            or stats_dict.get("Ties")
                            or stats_dict.get("T")
                        )
                        p = raw_p if raw_p is not None else "0"
                        try:
                            w_int = int(float(str(w)))
                            l_int = int(float(str(l_val)))
                            p_int = int(float(str(p)))
                            return f"{w_int}-{l_int}-{p_int}"
                        except (ValueError, TypeError):
                            return f"{w}-{l_val}-{p}"
        return None

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
        position_map: dict[str, str] = {}
        box_raw = raw.get("boxscore") if isinstance(raw, dict) else {}
        box_dict = box_raw if isinstance(box_raw, dict) else {}
        box_players_raw = box_dict.get("players")
        box_players: list[Any] = box_players_raw if isinstance(box_players_raw, list) else []
        team_ids: list[str] = []
        for group in box_players:
            if not isinstance(group, dict):
                continue
            t_info = group.get("team")
            tid = str(t_info.get("id") or "") if isinstance(t_info, dict) else ""
            if not tid or tid in team_ids:
                continue
            needs_pos = False
            stats_raw = group.get("statistics")
            if isinstance(stats_raw, list):
                for stat_cat in stats_raw:
                    if not isinstance(stat_cat, dict):
                        continue
                    aths_raw = stat_cat.get("athletes")
                    if isinstance(aths_raw, list):
                        for a_entry in aths_raw:
                            if isinstance(a_entry, dict):
                                a_info = a_entry.get("athlete")
                                if isinstance(a_info, dict):
                                    a_pos = a_info.get("position")
                                    has_pos = (
                                        bool(a_pos.get("abbreviation"))
                                        if isinstance(a_pos, dict)
                                        else bool(a_pos)
                                    )
                                    if not has_pos:
                                        needs_pos = True
                                        break
                    if needs_pos:
                        break
            if needs_pos:
                team_ids.append(tid)

        if team_ids:
            roster_coros = [
                self._safe_enrich(
                    self.request(
                        "GET",
                        f"apis/site/v2/sports/{s_san}/{lg_san}/teams/{self.sanitize_path_param(tid)}/roster",
                    )
                )
                for tid in team_ids
            ]
            rosters = await asyncio.gather(*roster_coros)
            for r_data in rosters:
                if isinstance(r_data, dict):
                    ath_groups = r_data.get("athletes")
                    if isinstance(ath_groups, list):
                        for grp in ath_groups:
                            if isinstance(grp, dict):
                                items = grp.get("items")
                                if isinstance(items, list):
                                    for ath in items:
                                        if isinstance(ath, dict):
                                            aid = str(ath.get("id") or "")
                                            p_info = ath.get("position")
                                            if isinstance(p_info, dict):
                                                pos_abbr = (
                                                    str(p_info["abbreviation"])
                                                    if p_info.get("abbreviation")
                                                    else None
                                                )
                                            elif p_info:
                                                pos_abbr = str(p_info)
                                            else:
                                                pos_abbr = None
                                            if aid and pos_abbr:
                                                position_map[aid] = pos_abbr

        return self._format_player_stats(raw, s, lg, event_id, position_map=position_map)

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
        raw, roster_data = await asyncio.gather(
            self.request("GET", f"apis/site/v2/sports/{s_san}/{lg_san}/teams/{t_san}/depthcharts"),
            self._safe_enrich(
                self.request("GET", f"apis/site/v2/sports/{s_san}/{lg_san}/teams/{t_san}/roster")
            ),
        )
        jersey_map: dict[str, str] = {}
        if isinstance(roster_data, dict):
            athletes = roster_data.get("athletes")
            if isinstance(athletes, list):
                for group in athletes:
                    if isinstance(group, dict):
                        items = group.get("items")
                        if isinstance(items, list):
                            for ath in items:
                                if isinstance(ath, dict):
                                    aid = str(ath.get("id") or "")
                                    j = ath.get("jersey") or ath.get("displayJersey")
                                    if aid and j is not None:
                                        jersey_map[aid] = str(j)
        return self._format_depth_chart(raw, s, lg, team_id, jersey_map=jersey_map)

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
        t_raw = raw.get("team", raw) if isinstance(raw, dict) else {}
        t_obj = t_raw if isinstance(t_raw, dict) else {}
        raw_venue = t_obj.get("venue") or (raw.get("venue") if isinstance(raw, dict) else None)
        needs_venue = formatted.get("venue") is None or raw_venue is None
        needs_next_event = formatted.get("next_event") is None

        if needs_venue or needs_next_event:
            sched = await self._safe_enrich(self.get_team_schedule(s, lg, team_id))
            if isinstance(sched, dict):
                home_v = sched.get("home_venue")
                if home_v and needs_venue:
                    formatted["venue"] = home_v
                if needs_next_event:
                    for game in sched.get("games", []):
                        g_stat = str(game.get("status") or "").lower()
                        is_upcoming_or_live = g_stat in ("pre", "in") or any(
                            term in g_stat for term in ("sched", "live", "progress")
                        )
                        is_terminal = _is_terminal_event(
                            game.get("status"),
                            game.get("detail"),
                            completed=bool(game.get("completed")),
                        )
                        if not is_terminal and is_upcoming_or_live:
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
            cat_clean = category.lower().replace("_", "").replace("-", "")
            if s == "football" and lg == "nfl" and cat_clean in _NFL_LEADER_CATEGORY_MAP:
                mapped_cat, mapped_sort = _NFL_LEADER_CATEGORY_MAP[cat_clean]
                params["category"] = mapped_cat
                if not sort:
                    params["sort"] = mapped_sort
            else:
                params["category"] = category
        if sort and "sort" not in params:
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
            cat_clean = category.lower().replace("_", "").replace("-", "")
            if s == "football" and lg == "nfl" and cat_clean in _NFL_LEADER_CATEGORY_MAP:
                mapped_cat, mapped_sort = _NFL_LEADER_CATEGORY_MAP[cat_clean]
                params["category"] = mapped_cat
                if not sort:
                    params["sort"] = mapped_sort
            else:
                params["category"] = category
        if sort and "sort" not in params:
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
        """Format detailed game summary including boxscore, predictor, lines, and plays."""
        header_raw = raw.get("header")
        header = header_raw if isinstance(header_raw, dict) else {}
        boxscore_raw = raw.get("boxscore")
        boxscore = boxscore_raw if isinstance(boxscore_raw, dict) else {}
        pickcenter_raw = raw.get("pickcenter")
        pickcenter = pickcenter_raw if isinstance(pickcenter_raw, list) else []
        predictor_raw = raw.get("predictor")
        predictor = predictor_raw if isinstance(predictor_raw, dict) else {}
        winprob_raw = raw.get("winprobability")
        winprob = winprob_raw if isinstance(winprob_raw, list) else []
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
                comp_records = competitor.get("record") or competitor.get("records") or []
                if isinstance(comp_records, str) and comp_records and c_id:
                    team_record_map[c_id] = comp_records
                else:
                    if isinstance(comp_records, dict):
                        comp_records = [comp_records]
                    if isinstance(comp_records, list):
                        for r in comp_records:
                            if isinstance(r, dict):
                                rec_summary = r.get("summary") or r.get("displayValue")
                                if rec_summary and c_id:
                                    team_record_map[c_id] = str(rec_summary)
                                    break
                            elif isinstance(r, str) and r and c_id:
                                team_record_map[c_id] = r
                                break
        if isinstance(ats_raw, list):
            for ats_item in ats_raw:
                if isinstance(ats_item, dict):
                    ats_team_raw = ats_item.get("team")
                    ats_team = ats_team_raw if isinstance(ats_team_raw, dict) else {}
                    tid = str(ats_team.get("id")) if ats_team.get("id") else ""
                    # Record resolution
                    raw_rec = ats_item.get("record")
                    record_val: str | None = None
                    if isinstance(raw_rec, list) and raw_rec:
                        r0 = raw_rec[0]
                        if isinstance(r0, dict):
                            rec_s = r0.get("summary") or r0.get("displayValue")
                            record_val = str(rec_s) if rec_s is not None else None
                        elif r0 is not None:
                            record_val = str(r0)
                    elif isinstance(raw_rec, dict):
                        rec_s = raw_rec.get("summary") or raw_rec.get("displayValue")
                        record_val = str(rec_s) if rec_s is not None else None
                    elif raw_rec is not None:
                        record_val = str(raw_rec)

                    if (
                        not record_val
                        and isinstance(ats_item.get("records"), list)
                        and ats_item["records"]
                    ):
                        r0 = ats_item["records"][0]
                        if isinstance(r0, dict):
                            rec_s = r0.get("summary") or r0.get("displayValue")
                            record_val = str(rec_s) if rec_s is not None else None
                        elif r0 is not None:
                            record_val = str(r0)
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
        self,
        raw: dict[str, Any],
        sport: str,
        league: str,
        event_id: str,
        position_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        boxscore_raw = raw.get("boxscore") if isinstance(raw, dict) else {}
        boxscore = boxscore_raw if isinstance(boxscore_raw, dict) else {}
        player_groups_raw = boxscore.get("players")
        player_groups: list[Any] = player_groups_raw if isinstance(player_groups_raw, list) else []

        teams_player_stats = []
        for group in player_groups:
            if not isinstance(group, dict):
                continue
            team_info_raw = group.get("team")
            team_info: dict[str, Any] = team_info_raw if isinstance(team_info_raw, dict) else {}
            categories_out = []
            stats_list_raw = group.get("statistics")
            stats_list = stats_list_raw if isinstance(stats_list_raw, list) else []
            for stat_cat in stats_list:
                if not isinstance(stat_cat, dict):
                    continue
                cat_type = stat_cat.get("type") or stat_cat.get("name") or "general"
                labels = stat_cat.get("labels", [])
                athletes_out = []
                ath_list_raw = stat_cat.get("athletes")
                ath_list = ath_list_raw if isinstance(ath_list_raw, list) else []
                for ath in ath_list:
                    if not isinstance(ath, dict):
                        continue
                    ath_info = ath.get("athlete", {})
                    stats_values = ath.get("stats", [])
                    stats_map = (
                        dict(zip(labels, stats_values, strict=False))
                        if isinstance(labels, list)
                        and isinstance(stats_values, list)
                        and len(labels) == len(stats_values)
                        else {}
                    )
                    aid = str(ath_info.get("id") or "") if isinstance(ath_info, dict) else ""
                    pos_val = (
                        ath_info.get("position", {}).get("abbreviation")
                        if isinstance(ath_info, dict) and isinstance(ath_info.get("position"), dict)
                        else (ath_info.get("position") if isinstance(ath_info, dict) else None)
                    )
                    if not pos_val and position_map and aid and aid in position_map:
                        pos_val = position_map[aid]

                    athletes_out.append(
                        {
                            "athlete_id": aid or None,
                            "name": ath_info.get("displayName")
                            if isinstance(ath_info, dict)
                            else None,
                            "jersey": ath_info.get("jersey")
                            if isinstance(ath_info, dict)
                            else None,
                            "position": pos_val,
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
    def _format_depth_slot(
        idx: int, ath_slot: Any, jersey_map: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        """Format a single depth chart slot entry with rank, slot, and jersey resolution."""
        if not isinstance(ath_slot, dict):
            return None
        ath_sub = ath_slot.get("athlete") if isinstance(ath_slot.get("athlete"), dict) else {}
        ath_info = ath_sub or ath_slot
        aid = ath_info.get("id") or ath_slot.get("id")
        aid_str = str(aid) if aid is not None else ""
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
            (jersey_map.get(aid_str) if (jersey_map and aid_str) else None)
            or ath_info.get("displayJersey")
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
            "athlete_id": aid,
            "name": (
                ath_info.get("displayName")
                or ath_info.get("fullName")
                or ath_slot.get("displayName")
                or ath_slot.get("name")
            ),
            "jersey": jersey_val,
        }

    def _format_depth_chart(
        self,
        raw: dict[str, Any],
        sport: str,
        league: str,
        team_id: str,
        jersey_map: dict[str, str] | None = None,
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
                            slot_obj = self._format_depth_slot(idx, ath_slot, jersey_map=jersey_map)
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
                        slot_obj = self._format_depth_slot(idx, ath_slot, jersey_map=jersey_map)
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
        """Format team schedule events, matchups, and game completion statuses."""
        events_out = []
        home_venues: list[str] = []
        for ev in raw.get("events") or []:
            if not isinstance(ev, dict):
                continue
            comps = ev.get("competitions")
            if not isinstance(comps, list) or not comps or not isinstance(comps[0], dict):
                continue
            comp = comps[0]
            opponent = None
            is_home = False
            comps_list = comp.get("competitors")
            if isinstance(comps_list, list):
                for c in comps_list:
                    if isinstance(c, dict):
                        if str(c.get("id")) == str(team_id):
                            if c.get("homeAway") == "home":
                                is_home = True
                        else:
                            c_team = c.get("team")
                            if isinstance(c_team, dict):
                                opponent = c_team.get("displayName")
            v_obj = comp.get("venue")
            v_name = (
                (v_obj.get("fullName") or v_obj.get("name"))
                if isinstance(v_obj, dict)
                else (str(v_obj) if isinstance(v_obj, str) else None)
            )
            if is_home and v_name:
                home_venues.append(str(v_name))
            status_val = comp.get("status")
            status = status_val if isinstance(status_val, dict) else {}
            type_val = status.get("type")
            type_dict = type_val if isinstance(type_val, dict) else {}
            events_out.append(
                {
                    "event_id": ev.get("id"),
                    "date": ev.get("date"),
                    "matchup": ev.get("name"),
                    "opponent": opponent,
                    "venue": v_name,
                    "status": type_dict.get("state") or status.get("state"),
                    "detail": type_dict.get("detail") or status.get("detail"),
                    "completed": type_dict.get("completed", status.get("completed", False)),
                }
            )
        team_raw = raw.get("team")
        team_dict: dict[str, Any] = team_raw if isinstance(team_raw, dict) else {}
        season_raw = raw.get("season")
        season_dict: dict[str, Any] = season_raw if isinstance(season_raw, dict) else {}
        home_venue = Counter(home_venues).most_common(1)[0][0] if home_venues else None
        return {
            "sport": sport,
            "league": league,
            "team_id": team_id,
            "team_name": team_dict.get("displayName"),
            "season": season_dict.get("year"),
            "home_venue": home_venue,
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
        t_raw = raw.get("team", raw)
        t: dict[str, Any] = t_raw if isinstance(t_raw, dict) else {}
        next_event = t.get("nextEvent")
        events_list = (
            next_event
            if isinstance(next_event, list)
            else ([next_event] if isinstance(next_event, dict) else [])
        )
        first_next: dict[str, Any] = {}
        for ev in events_list:
            if not isinstance(ev, dict) or not ev.get("id"):
                continue
            if not (ev.get("name") and ev.get("date")):
                continue
            comps = ev.get("competitions")
            comp = (
                comps[0] if isinstance(comps, list) and comps and isinstance(comps[0], dict) else {}
            )
            status_val = comp.get("status")
            status_dict = status_val if isinstance(status_val, dict) else {}
            type_val = status_dict.get("type")
            status_info = type_val if isinstance(type_val, dict) else {}

            ev_status = ev.get("status")
            ev_status_dict = ev_status if isinstance(ev_status, dict) else {}
            ev_type_val = ev_status_dict.get("type")
            ev_type_dict = ev_type_val if isinstance(ev_type_val, dict) else {}

            c_stat = status_info.get("state") or status_dict.get("state")
            e_stat = (
                ev_type_dict.get("state")
                or ev_status_dict.get("state")
                or (ev_status if isinstance(ev_status, str) else None)
            )

            c_det = status_info.get("detail") or status_dict.get("detail")
            e_det = ev_type_dict.get("detail") or ev_status_dict.get("detail")

            c_completed = bool(status_info.get("completed") or status_dict.get("completed"))
            e_completed = bool(ev_type_dict.get("completed") or ev_status_dict.get("completed"))

            if _is_terminal_event(c_stat, c_det, completed=c_completed) or _is_terminal_event(
                e_stat, e_det, completed=e_completed
            ):
                continue

            first_next = ev
            break

        record = t.get("record", {})
        record_items = record.get("items", []) if isinstance(record, dict) else []
        overall_record = (
            record_items[0].get("summary")
            if record_items
            else (record.get("overall") if isinstance(record, dict) else None)
        )
        franchise = t.get("franchise")
        franchise_venue = franchise.get("venue") if isinstance(franchise, dict) else None
        venue_obj = t.get("venue") or raw.get("venue") or franchise_venue or {}
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

    @staticmethod
    def _map_labels_unique(labels: list[Any], values: list[Any]) -> dict[str, Any]:
        """Map values to labels while preserving duplicate labels with sequential suffixes."""
        reserved = {str(labels[j] if j < len(labels) else f"stat_{j}") for j in range(len(values))}
        out: dict[str, Any] = {}
        for idx, val in enumerate(values):
            raw_lbl = str(labels[idx] if idx < len(labels) else f"stat_{idx}")
            lbl_key = raw_lbl
            suffix = 1
            while lbl_key in out or (lbl_key in reserved and lbl_key != raw_lbl):
                lbl_key = f"{raw_lbl}_{suffix}"
                suffix += 1
            out[lbl_key] = val
        return out

    def _format_athlete_splits(
        self, raw: dict[str, Any], sport: str, league: str, athlete_id: str, season: int | None
    ) -> dict[str, Any]:
        """Format situational and venue statistical splits for an athlete."""
        split_categories = raw.get("splitCategories") or raw.get("categories") or []
        labels = raw.get("labels") or raw.get("names") or []
        stat_categories = raw.get("categories")
        has_subcats = (
            isinstance(stat_categories, list)
            and len(stat_categories) > 0
            and all(
                isinstance(c, dict)
                and c.get("count") is not None
                and not isinstance(c.get("count"), bool)
                and str(c.get("count")).isascii()
                and str(c.get("count")).isdigit()
                and int(str(c.get("count"))) >= 0
                for c in stat_categories
            )
            and sum(int(str(c.get("count"))) for c in stat_categories) > 0
        )
        total_cat_count = (
            sum(int(str(c.get("count"))) for c in stat_categories)
            if has_subcats and isinstance(stat_categories, list)
            else 0
        )
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
                        if (
                            has_subcats
                            and isinstance(stat_categories, list)
                            and len(stats_raw) == total_cat_count
                        ):
                            cat_stats: dict[str, dict[str, Any]] = {}
                            curr_offset = 0
                            for cat_spec in stat_categories:
                                c_name = (
                                    cat_spec.get("name") or cat_spec.get("displayName") or "general"
                                )
                                c_count = int(str(cat_spec.get("count", 0)))
                                sub_labels = row_labels[curr_offset : curr_offset + c_count]
                                sub_vals = stats_raw[curr_offset : curr_offset + c_count]
                                cat_stats[str(c_name)] = self._map_labels_unique(
                                    sub_labels, sub_vals
                                )
                                curr_offset += c_count
                            stat_map = cat_stats
                        else:
                            stat_map = self._map_labels_unique(row_labels, stats_raw)
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
        if not trimmed_cats and isinstance(fallback, list):
            trimmed_athletes = []
            dict_fallback = [ath for ath in fallback if isinstance(ath, dict)]
            for ath_item in dict_fallback[:limit]:
                clean_ath = {k: v for k, v in ath_item.items() if k not in ("links", "logos")}
                ath_info = ath_item.get("athlete")
                if isinstance(ath_info, dict):
                    clean_ath["athlete"] = {
                        "id": _extract_id_from_ref(ath_info),
                        "displayName": ath_info.get("displayName") or ath_info.get("fullName"),
                        "jersey": ath_info.get("jersey"),
                        "position": (
                            ath_info.get("position", {}).get("abbreviation")
                            if isinstance(ath_info.get("position"), dict)
                            else ath_info.get("position")
                        ),
                        "teamId": ath_info.get("teamId")
                        or _extract_id_from_ref(
                            ath_info.get("teams", [{}])[0]
                            if isinstance(ath_info.get("teams"), list) and ath_info.get("teams")
                            else {}
                        ),
                        "teamName": ath_info.get("teamName"),
                    }
                if "categories" in ath_item and isinstance(ath_item["categories"], list):
                    clean_cats = []
                    for c in ath_item["categories"]:
                        if isinstance(c, dict):
                            clean_cats.append(
                                {
                                    "name": c.get("name") or c.get("displayName"),
                                    "displayName": c.get("displayName"),
                                    "values": c.get("values", []),
                                    "ranks": c.get("ranks", []),
                                }
                            )
                    clean_ath["categories"] = clean_cats
                trimmed_athletes.append(clean_ath)
            if trimmed_athletes:
                trimmed_cats = trimmed_athletes

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
        res_ath["leaders"] = trimmed_cats if trimmed_cats else trimmed_fallback
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
        if not trimmed_cats and isinstance(fallback, list):
            trimmed_teams = []
            dict_fallback = [team for team in fallback if isinstance(team, dict)]
            for team_item in dict_fallback[:limit]:
                clean_t = {k: v for k, v in team_item.items() if k not in ("links", "logos")}
                team_info = team_item.get("team")
                if isinstance(team_info, dict):
                    clean_t["team"] = {
                        "id": _extract_id_from_ref(team_info),
                        "displayName": team_info.get("displayName") or team_info.get("name"),
                        "abbreviation": team_info.get("abbreviation"),
                    }
                if "categories" in team_item and isinstance(team_item["categories"], list):
                    clean_cats = []
                    for c in team_item["categories"]:
                        if isinstance(c, dict):
                            clean_cats.append(
                                {
                                    "name": c.get("name") or c.get("displayName"),
                                    "displayName": c.get("displayName"),
                                    "values": c.get("values", []),
                                    "ranks": c.get("ranks", []),
                                }
                            )
                    clean_t["categories"] = clean_cats
                trimmed_teams.append(clean_t)
            if trimmed_teams:
                trimmed_cats = trimmed_teams

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
        res_team["leaders"] = trimmed_cats if trimmed_cats else trimmed_fallback
        return res_team

    def _format_league_groups(self, raw: dict[str, Any], sport: str, league: str) -> dict[str, Any]:
        """Format league division, conference, and structural group hierarchies."""
        raw_groups = raw.get("groups") if isinstance(raw, dict) else []
        groups = [] if raw_groups is None else raw_groups
        if not isinstance(groups, list):
            return {
                "sport": sport,
                "league": league,
                "count": 1,
                "groups": groups,
            }

        def _clean_group_node(node: Any) -> Any:
            if not isinstance(node, dict):
                return node
            clean = {k: v for k, v in node.items() if k not in ("logos", "links", "$ref")}
            if "team" in clean and isinstance(clean["team"], dict):
                clean["team"] = {
                    k: v for k, v in clean["team"].items() if k not in ("logos", "links", "$ref")
                }
            if "teams" in node and isinstance(node["teams"], list):
                clean_teams = []
                for tm in node["teams"]:
                    if isinstance(tm, dict):
                        c_tm = {k: v for k, v in tm.items() if k not in ("logos", "links", "$ref")}
                        if "team" in c_tm and isinstance(c_tm["team"], dict):
                            c_tm["team"] = {
                                k: v
                                for k, v in c_tm["team"].items()
                                if k not in ("logos", "links", "$ref")
                            }
                        clean_teams.append(c_tm)
                    else:
                        clean_teams.append(tm)
                clean["teams"] = clean_teams
            if "children" in node and isinstance(node["children"], list):
                clean["children"] = [_clean_group_node(c) for c in node["children"]]
            return clean

        cleaned_groups = [_clean_group_node(g) for g in groups]
        return {
            "sport": sport,
            "league": league,
            "count": len(cleaned_groups),
            "groups": cleaned_groups,
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
        if not isinstance(raw, dict):
            return {
                "sport": sport,
                "league": league,
                "season": season,
                "draft": raw,
            }

        def _clean_picks(picks_list: list[Any]) -> list[Any]:
            clean_picks = []
            for pk in picks_list:
                if not isinstance(pk, dict):
                    continue
                clean_pk = {
                    k: v
                    for k, v in pk.items()
                    if k not in ("broadcasts", "guid", "headshot", "links", "uid", "$ref")
                }
                if "team" in clean_pk and isinstance(clean_pk["team"], dict):
                    clean_pk["team"] = {
                        k: v
                        for k, v in clean_pk["team"].items()
                        if k not in ("logos", "links", "$ref")
                    }
                ath = pk.get("athlete")
                if isinstance(ath, dict):
                    raw_attrs = ath.get("attributes")
                    team_raw = ath.get("team")
                    clean_team = (
                        {k: v for k, v in team_raw.items() if k not in ("logos", "links", "$ref")}
                        if isinstance(team_raw, dict)
                        else team_raw
                    )
                    clean_ath = {
                        "id": ath.get("id"),
                        "displayName": ath.get("displayName"),
                        "position": (
                            ath.get("position", {}).get("name")
                            if isinstance(ath.get("position"), dict)
                            else ath.get("position")
                        ),
                        "team": clean_team,
                        "attributes": [
                            {
                                "name": a.get("name"),
                                "value": (
                                    a.get("displayValue")
                                    if a.get("displayValue") is not None
                                    else a.get("value")
                                ),
                            }
                            for a in (raw_attrs if isinstance(raw_attrs, list) else [])
                            if isinstance(a, dict)
                        ],
                    }
                    clean_pk["athlete"] = clean_ath
                clean_picks.append(clean_pk)
            return clean_picks

        draft_content = raw.get("draft") or raw.get("picks") or raw.get("rounds") or raw
        if isinstance(draft_content, list):
            return {
                "sport": sport,
                "league": league,
                "season": season,
                "draft": _clean_picks(draft_content),
            }
        if not isinstance(draft_content, dict):
            return {
                "sport": sport,
                "league": league,
                "season": season,
                "draft": draft_content,
            }
        clean_draft = {
            k: v for k, v in draft_content.items() if k not in ("broadcasts", "links", "$ref")
        }
        picks = draft_content.get("picks")
        if isinstance(picks, list):
            clean_draft["picks"] = _clean_picks(picks)
        rounds = draft_content.get("rounds")
        if isinstance(rounds, list):
            clean_rounds = []
            for r in rounds:
                if not isinstance(r, dict):
                    clean_rounds.append(r)
                    continue
                clean_r = {k: v for k, v in r.items() if k not in ("links", "$ref")}
                if "picks" in r and isinstance(r["picks"], list):
                    clean_r["picks"] = _clean_picks(r["picks"])
                clean_rounds.append(clean_r)
            clean_draft["rounds"] = clean_rounds
        return {
            "sport": sport,
            "league": league,
            "season": season,
            "draft": clean_draft,
        }

    def _format_scoreboard_header(
        self, raw: dict[str, Any], sport: str, league: str
    ) -> dict[str, Any]:
        """Format live ticker scoreboard header data across games."""
        sports_data = raw.get("sports") if isinstance(raw, dict) else None
        if not isinstance(sports_data, list):
            fallback = raw.get("leagues") or raw if isinstance(raw, dict) else raw
            return {
                "sport": sport,
                "league": league,
                "sports": fallback,
            }
        cleaned_sports = []
        for sp in sports_data:
            if not isinstance(sp, dict):
                continue
            c_sp: dict[str, Any] = {
                "id": sp.get("id"),
                "name": sp.get("name"),
                "slug": sp.get("slug"),
            }
            leagues = sp.get("leagues")
            if isinstance(leagues, list):
                c_leagues = []
                for lg in leagues:
                    if not isinstance(lg, dict):
                        continue
                    c_lg: dict[str, Any] = {
                        "id": lg.get("id"),
                        "name": lg.get("name"),
                        "abbreviation": lg.get("abbreviation"),
                        "slug": lg.get("slug"),
                    }
                    if "events" in lg and isinstance(lg["events"], list):
                        c_events = []
                        for ev in lg["events"]:
                            if not isinstance(ev, dict):
                                continue
                            c_ev: dict[str, Any] = {
                                "id": ev.get("id"),
                                "name": ev.get("name"),
                                "shortName": ev.get("shortName"),
                                "date": ev.get("date"),
                                "summary": ev.get("summary"),
                                "period": ev.get("period"),
                                "clock": ev.get("clock"),
                                "status": ev.get("status"),
                            }
                            comps = ev.get("competitors")
                            if isinstance(comps, list):
                                c_comps = []
                                for c in comps:
                                    if not isinstance(c, dict):
                                        continue
                                    c_entry: dict[str, Any] = {
                                        "id": c.get("id"),
                                        "homeAway": c.get("homeAway"),
                                        "score": c.get("score"),
                                        "winner": c.get("winner"),
                                    }
                                    t = c.get("team")
                                    if isinstance(t, dict):
                                        c_entry["team"] = {
                                            "id": t.get("id"),
                                            "name": t.get("name"),
                                            "displayName": t.get("displayName"),
                                            "abbreviation": t.get("abbreviation"),
                                        }
                                    c_comps.append(c_entry)
                                c_ev["competitors"] = c_comps
                            c_events.append(c_ev)
                        c_lg["events"] = c_events
                    c_leagues.append(c_lg)
                c_sp["leagues"] = c_leagues
            cleaned_sports.append(c_sp)
        return {
            "sport": sport,
            "league": league,
            "sports": cleaned_sports,
        }

    def _format_event_odds(
        self, raw: dict[str, Any], sport: str, league: str, event_id: str, competition_id: str
    ) -> dict[str, Any]:
        """Format event odds and sportsbook betting lines."""
        raw_items = raw.get("items") if isinstance(raw, dict) else None
        odds_list: list[Any] = (
            raw_items
            if isinstance(raw_items, list)
            else ([raw] if isinstance(raw, dict) and raw else [])
        )
        formatted_odds = []
        for it in odds_list:
            if not isinstance(it, dict):
                continue
            clean_odd = {k: v for k, v in it.items() if k not in ("links", "$ref", "propBets")}
            odd_id = _extract_id_from_ref(it)
            if odd_id:
                clean_odd["id"] = odd_id
            prov = it.get("provider")
            if isinstance(prov, dict):
                prov_id = _extract_id_from_ref(prov)
                clean_prov = {k: v for k, v in prov.items() if k not in ("links", "$ref")}
                if prov_id:
                    clean_prov["id"] = prov_id
                clean_odd["provider"] = clean_prov
            for side_key in ("homeTeamOdds", "awayTeamOdds"):
                side = it.get(side_key)
                if isinstance(side, dict):
                    clean_side = {k: v for k, v in side.items() if k != "links"}
                    team_raw = side.get("team")
                    if isinstance(team_raw, dict):
                        tid = _extract_id_from_ref(team_raw)
                        if tid:
                            clean_side["team_id"] = tid
                            clean_side["team"] = {"id": tid}
                    clean_odd[side_key] = clean_side
            formatted_odds.append(clean_odd)

        if isinstance(raw_items, list):
            out_odds: Any = formatted_odds
        elif formatted_odds:
            out_odds = formatted_odds
        else:
            out_odds = raw
        return {
            "sport": sport,
            "league": league,
            "event_id": event_id,
            "competition_id": competition_id,
            "odds": out_odds,
        }

    def _format_play_by_play(
        self, raw: dict[str, Any], sport: str, league: str, event_id: str, competition_id: str
    ) -> dict[str, Any]:
        """Format chronological play-by-play drive and scoring actions."""
        raw_plays = raw.get("items") if isinstance(raw, dict) else None
        plays: list[Any] = (
            raw_plays
            if isinstance(raw_plays, list)
            else ([raw] if isinstance(raw, dict) and raw else [])
        )
        formatted_plays = []
        for pl in plays:
            if not isinstance(pl, dict):
                continue
            clean_pl = {k: v for k, v in pl.items() if k not in ("links", "$ref")}
            team_raw = pl.get("team")
            if isinstance(team_raw, dict):
                tid = _extract_id_from_ref(team_raw)
                if tid:
                    clean_pl["team_id"] = tid
                    clean_pl["team"] = {"id": tid}
            period_raw = pl.get("period")
            if isinstance(period_raw, dict):
                p_num = period_raw.get("number")
                if p_num is None:
                    extracted = _extract_id_from_ref(period_raw)
                    if extracted is not None:
                        p_num = int(extracted) if extracted.isdigit() else extracted
                elif isinstance(p_num, str) and p_num.isdigit():
                    p_num = int(p_num)
                clean_pl["period"] = {"number": p_num} if p_num is not None else period_raw
            if "participants" in pl and isinstance(pl["participants"], list):
                clean_parts = []
                for part in pl["participants"]:
                    if isinstance(part, dict):
                        c_part = {
                            k: v
                            for k, v in part.items()
                            if k not in ("links", "$ref", "statistics", "playStatistics")
                        }
                        ath_raw = part.get("athlete")
                        if isinstance(ath_raw, dict):
                            aid = _extract_id_from_ref(ath_raw)
                            if aid:
                                c_part["athlete_id"] = aid
                                c_part["athlete"] = {"id": aid}
                        pos_raw = part.get("position")
                        if isinstance(pos_raw, dict):
                            pos_id = _extract_id_from_ref(pos_raw)
                            if pos_id:
                                c_part["position_id"] = pos_id
                        clean_parts.append(c_part)
                clean_pl["participants"] = clean_parts
            if "teamParticipants" in pl and isinstance(pl["teamParticipants"], list):
                clean_tparts = []
                for tp in pl["teamParticipants"]:
                    if isinstance(tp, dict):
                        c_tp = {
                            k: v
                            for k, v in tp.items()
                            if k not in ("links", "$ref", "statistics", "playStatistics")
                        }
                        t_raw = tp.get("team")
                        if isinstance(t_raw, dict):
                            t_id = _extract_id_from_ref(t_raw)
                            if t_id:
                                c_tp["team_id"] = t_id
                                c_tp["team"] = {"id": t_id}
                        clean_tparts.append(c_tp)
                clean_pl["teamParticipants"] = clean_tparts
            formatted_plays.append(clean_pl)

        if isinstance(raw_plays, list):
            out_plays: Any = formatted_plays
        elif formatted_plays:
            out_plays = formatted_plays
        else:
            out_plays = []

        return {
            "sport": sport,
            "league": league,
            "event_id": event_id,
            "competition_id": competition_id,
            "count": len(out_plays) if isinstance(out_plays, list) else 1,
            "plays": out_plays,
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
        if not isinstance(raw, dict):
            return {
                "sport": sport,
                "league": league,
                "event_id": event_id,
                "competition_id": competition_id,
                "predictor": raw,
            }
        clean_pred = {k: v for k, v in raw.items() if k not in ("links", "$ref")}
        for side_key in ("homeTeam", "awayTeam"):
            side = raw.get(side_key)
            if isinstance(side, dict):
                clean_side = {k: v for k, v in side.items() if k != "links"}
                team_raw = side.get("team")
                if isinstance(team_raw, dict):
                    tid = _extract_id_from_ref(team_raw)
                    if tid:
                        clean_side["team_id"] = tid
                        clean_side["team"] = {"id": tid}
                clean_pred[side_key] = clean_side
        return {
            "sport": sport,
            "league": league,
            "event_id": event_id,
            "competition_id": competition_id,
            "predictor": clean_pred,
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
        sections_raw = raw_dict.get("sections")
        sections: list[Any] = sections_raw if isinstance(sections_raw, list) else []
        formatted_sections = []
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            sec_copy = dict(sec)
            st_raw = sec.get("seasonType")
            if isinstance(st_raw, dict) and "$ref" in st_raw:
                st_id = _extract_id_from_ref(st_raw)
                sec_copy["seasonType"] = {
                    "id": st_id,
                    "ref": st_raw["$ref"],
                }
            formatted_sections.append(sec_copy)

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
            "sections": formatted_sections if formatted_sections else sections,
            "calendar": calendar_out,
        }

    def _format_futures(
        self, raw: dict[str, Any], sport: str, league: str, season: int
    ) -> dict[str, Any]:
        """Format championship and season outright futures betting markets."""
        raw_items_val = raw.get("items") if isinstance(raw, dict) else None
        raw_items: list[Any] = (
            raw_items_val
            if isinstance(raw_items_val, list)
            else ([raw] if (isinstance(raw, dict) and raw) else [])
        )
        formatted_futures = []
        for it in raw_items:
            if not isinstance(it, dict):
                continue
            it_copy = dict(it)
            f_list = it.get("futures")
            if isinstance(f_list, list):
                clean_f_list = []
                for prov in f_list:
                    if not isinstance(prov, dict):
                        continue
                    prov_copy = dict(prov)
                    books = prov.get("books")
                    if isinstance(books, list):
                        clean_books = []
                        for b in books:
                            if not isinstance(b, dict):
                                continue
                            b_copy = dict(b)
                            ath_raw = b.get("athlete")
                            if isinstance(ath_raw, dict) and "$ref" in ath_raw:
                                ath_id = _extract_id_from_ref(ath_raw)
                                b_copy["athlete"] = {
                                    "id": ath_id,
                                    "ref": ath_raw["$ref"],
                                }
                                if ath_id:
                                    b_copy["athlete_id"] = ath_id
                            team_raw = b.get("team")
                            if isinstance(team_raw, dict) and "$ref" in team_raw:
                                team_id = _extract_id_from_ref(team_raw)
                                b_copy["team"] = {
                                    "id": team_id,
                                    "ref": team_raw["$ref"],
                                }
                                if team_id:
                                    b_copy["team_id"] = team_id
                            clean_books.append(b_copy)
                        prov_copy["books"] = clean_books
                    clean_f_list.append(prov_copy)
                it_copy["futures"] = clean_f_list
            formatted_futures.append(it_copy)

        if isinstance(raw_items_val, list):
            f_out: Any = formatted_futures
        elif formatted_futures:
            f_out = formatted_futures
        else:
            f_out = raw

        return {
            "sport": sport,
            "league": league,
            "season": season,
            "futures": f_out,
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
            item_copy = {k: v for k, v in it.items() if k not in ("links", "logos", "$ref")}
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
