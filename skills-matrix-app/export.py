"""Export helpers: clean CSV and AI-ready JSON.

The JSON export follows the structure described in the delivery plan so
that downstream AI analysis (summaries, training paths, staffing) can
consume it directly.
"""

from __future__ import annotations

import io
import json
from typing import List

import pandas as pd

from config import Config
from scoring import build_person_profile, team_averages, team_matrix


def people_scores_csv(
    people: pd.DataFrame, responses: pd.DataFrame, config: Config
) -> str:
    """A wide CSV: one row per person, one column per capability area."""
    matrix = team_matrix(people, responses, config)
    buf = io.StringIO()
    matrix.to_csv(buf, index=False)
    return buf.getvalue()


def ai_ready_json(
    people: pd.DataFrame, responses: pd.DataFrame, config: Config
) -> str:
    """Produce the prompt-ready JSON export for the whole team."""
    team: List[dict] = []
    for _, person in people.iterrows():
        person_resp = responses[responses["person_id"] == person["person_id"]]
        profile = build_person_profile(dict(person), person_resp, config)
        team.append(
            {
                "person": profile["person"],
                "role": profile["role"],
                "team": profile["team"],
                "location": profile["location"],
                "overall_score": profile["overall_score"],
                "scores": {k: round(v, 1) for k, v in profile["scores"].items()},
                "strengths": profile["strengths"],
                "development_areas": profile["development_areas"],
                "confidence_gaps": profile["confidence_gaps"],
                "summary": profile["summary"],
            }
        )

    matrix = team_matrix(people, responses, config)
    averages = team_averages(matrix, config)

    payload = {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "team_size": len(team),
        "team_averages": {k: round(float(v), 1) for k, v in averages.items()},
        "people": team,
    }
    return json.dumps(payload, indent=2, default=str)
