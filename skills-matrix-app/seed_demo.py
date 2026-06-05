"""Seed the database with sample assessments and run a smoke test.

Useful for trying out the dashboards without manually filling in the form,
and as a lightweight check that the config, database, scoring and export
modules all hang together. Run with:  python seed_demo.py
"""

from __future__ import annotations

import os
import random

from config import load_config
from database import Database
from export import ai_ready_json, people_scores_csv
from scoring import build_person_profile, team_averages, team_matrix

SAMPLE_PEOPLE = [
    ("Ada Lovelace", "AI Specialist", "London", "AI Practice"),
    ("Alan Turing", "ML Engineer", "Manchester", "AI Practice"),
    ("Grace Hopper", "Delivery Lead", "New York", "Consulting"),
    ("Katherine Johnson", "Data Engineer", "Houston", "Data"),
]


def _random_responses(config, skew: int):
    """Generate plausible responses, skewed by a per-person bias."""
    responses = {}
    for q in config.questions:
        base = random.randint(1, 5)
        score = max(1, min(5, base + random.choice([-1, 0, 0, skew])))
        responses[q.id] = {
            "score": score,
            "capability_area": q.capability_area,
            "comment": None,
        }
    return responses


def main() -> None:
    random.seed(42)
    config = load_config()

    # Use a throwaway database so we don't pollute real data.
    db_path = os.path.join(os.path.dirname(__file__), "data", "demo.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db = Database(db_path)

    for i, (name, role, location, team) in enumerate(SAMPLE_PEOPLE):
        db.save_assessment(
            name=name,
            role=role,
            location=location,
            team=team,
            responses=_random_responses(config, skew=1 if i % 2 == 0 else 0),
        )

    people = db.get_people()
    responses = db.get_responses()
    assert len(people) == len(SAMPLE_PEOPLE), "people count mismatch"

    # Individual profile.
    first = people.iloc[0].to_dict()
    profile = build_person_profile(
        first, db.get_responses(first["person_id"]), config
    )
    assert 1 <= profile["overall_score"] <= 5, "overall out of range"
    assert profile["scores"], "no capability scores produced"
    assert profile["strengths"], "no strengths produced"

    # Team aggregation.
    matrix = team_matrix(people, responses, config)
    averages = team_averages(matrix, config)
    assert not averages.empty, "team averages empty"

    # Exports.
    csv_text = people_scores_csv(people, responses, config)
    json_text = ai_ready_json(people, responses, config)
    assert "name" in csv_text.splitlines()[0]
    assert '"people"' in json_text

    print("Smoke test passed.")
    print(f"  People:            {len(people)}")
    print(f"  Capability areas:  {len(config.capabilities)}")
    print(f"  Questions:         {len(config.questions)}")
    print(f"  Example overall:   {profile['overall_score']} ({first['name']})")
    print(f"  Top team area:     {averages.index[0]} ({averages.iloc[0]})")
    print("  Summary:")
    print("    " + profile["summary"])

    os.remove(db_path)


if __name__ == "__main__":
    main()
