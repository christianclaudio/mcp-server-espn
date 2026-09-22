"""ESPN domain sub-servers and re-exports for backward compatibility."""

from espn_mcp.tools.games import (
    game_analysis_prompt,
    games_server,
    get_game_summary,
    get_rankings,
    get_scoreboard,
    get_standings,
    get_team_schedule,
)
from espn_mcp.tools.news import (
    get_capabilities,
    get_news,
    get_supported_leagues,
    news_server,
)
from espn_mcp.tools.teams import (
    get_athlete_overview,
    get_player_stats,
    get_team_depth_chart,
    get_team_roster,
    team_evaluation_prompt,
    teams_server,
)

__all__ = [
    "game_analysis_prompt",
    "games_server",
    "get_athlete_overview",
    "get_capabilities",
    "get_game_summary",
    "get_news",
    "get_player_stats",
    "get_rankings",
    "get_scoreboard",
    "get_standings",
    "get_supported_leagues",
    "get_team_depth_chart",
    "get_team_roster",
    "get_team_schedule",
    "news_server",
    "team_evaluation_prompt",
    "teams_server",
]
