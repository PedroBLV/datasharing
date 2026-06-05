"""Export helpers: clean CSV and AI-ready JSON.

Both exports carry the two dimensions (Knowledge and Delivery) so
downstream analysis can reason about who has shipped what, not just who
has heard of it.
"""

from __future__ import annotations

import io
import json
from typing import List

import pandas as pd

from config import Config
from scoring import build_person_profile, score_person


def people_scores_csv(
    people: pd.DataFrame, responses: pd.DataFrame, config: Config
) -> str:
    """Long-format CSV: one row per person/domain/dimension score."""
    rows = []
    for _, person in people.iterrows():
        person_resp = responses[responses["person_id"] == person["person_id"]]
        scored = score_person(person_resp, config)
        for ds in scored["domains"].values():
            for dim, val in ds.scores.items():
                rows.append(
                    {
                        "name": person["name"],
                        "role": person["role"],
                        "team": person["team"],
                        "location": person["location"],
                        "domain": ds.domain_name,
                        "dimension": dim,
                        "score": val,
                    }
                )
    buf = io.StringIO()
    pd.DataFrame(rows).to_csv(buf, index=False)
    return buf.getvalue()


def ai_ready_json(
    people: pd.DataFrame, responses: pd.DataFrame, config: Config
) -> str:
    """Prompt-ready JSON export for the whole team, two dimensions deep."""
    team: List[dict] = []
    for _, person in people.iterrows():
        person_resp = responses[responses["person_id"] == person["person_id"]]
        profile = build_person_profile(dict(person), person_resp, config)

        capabilities = {}
        for cs in profile["capability_scores"].values():
            capabilities[cs.capability_name] = {
                dim: round(score, 1) for dim, score in cs.scores.items()
            }

        team.append(
            {
                "person": profile["person"],
                "role": profile["role"],
                "team": profile["team"],
                "location": profile["location"],
                "overall": {k: round(v, 1) for k, v in profile["overall"].items()},
                "domain_scores": {
                    name: {d: round(v, 1) for d, v in scores.items()}
                    for name, scores in profile["domain_scores_by_name"].items()
                },
                "capabilities": capabilities,
                "strengths": profile["strengths"],
                "development_areas": profile["development_areas"],
                "delivered_capabilities": profile["delivered_capabilities"],
                "knowledge_delivery_gaps": profile["knowledge_delivery_gaps"],
                "summary": profile["summary"],
            }
        )

    payload = {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "scale": {
            str(lvl): ml.label for lvl, ml in sorted(config.maturity_levels.items())
        },
        "dimensions": [d.id for d in config.dimensions],
        "team_size": len(team),
        "people": team,
    }
    return json.dumps(payload, indent=2, default=str)
