"""Seed the database with sample assessments and run a smoke test.

Lets you try the dashboards without filling in the form by hand, and acts
as a lightweight check that config, database, scoring and export all hang
together for the two-dimensional (Knowledge/Delivery) model.

Run with:  python seed_demo.py
"""

from __future__ import annotations

import os
import random

from config import load_config
from database import Database
from export import ai_ready_json, people_scores_csv
from scoring import build_person_profile, score_person, team_averages, team_matrix

SAMPLE_PEOPLE = [
    # (name, role, location, team, knowledge_bias, delivery_bias)
    ("Ada Lovelace", "AI Specialist", "London", "AI Practice", 1, 1),
    ("Alan Turing", "ML Engineer", "Manchester", "AI Practice", 1, 0),
    ("Grace Hopper", "Delivery Lead", "New York", "Consulting", 0, 1),
    ("Katherine Johnson", "Data Engineer", "Houston", "Data", 0, 0),
]


def _clamp(v):
    return max(0, min(5, v))


def _random_responses(config, k_bias: int, d_bias: int):
    """Generate plausible responses where knowledge tends to run a little
    ahead of delivery — the realistic 'know more than I've shipped' shape."""
    responses = []
    for cap in config.capabilities:
        base = random.randint(1, 4)
        knowledge = _clamp(base + k_bias + random.choice([0, 1]))
        # Delivery usually trails knowledge.
        delivery = _clamp(min(knowledge, base + d_bias) - random.choice([0, 1]))
        for dim_id, score in (("knowledge", knowledge), ("delivery", delivery)):
            responses.append(
                {
                    "capability_id": cap.id,
                    "domain": cap.domain,
                    "dimension": dim_id,
                    "score": score,
                }
            )
    return responses


def main() -> None:
    random.seed(42)
    config = load_config()

    db_path = os.path.join(os.path.dirname(__file__), "data", "demo.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db = Database(db_path)

    for name, role, location, team, k_bias, d_bias in SAMPLE_PEOPLE:
        db.save_assessment(
            name=name,
            role=role,
            location=location,
            team=team,
            responses=_random_responses(config, k_bias, d_bias),
        )

    people = db.get_people()
    responses = db.get_responses()
    assert len(people) == len(SAMPLE_PEOPLE), "people count mismatch"

    first = people.iloc[0].to_dict()
    profile = build_person_profile(first, db.get_responses(first["person_id"]), config)
    assert profile["overall"], "no overall scores produced"
    assert "knowledge" in profile["overall"] and "delivery" in profile["overall"]
    assert profile["domain_scores"], "no domain scores produced"

    # Team aggregation per dimension.
    for dim in config.dimension_ids:
        matrix = team_matrix(people, responses, config, dim)
        averages = team_averages(matrix, config)
        assert not averages.empty, f"team averages empty for {dim}"

    csv_text = people_scores_csv(people, responses, config)
    json_text = ai_ready_json(people, responses, config)
    assert "dimension" in csv_text.splitlines()[0]
    assert '"people"' in json_text and '"knowledge_delivery_gaps"' in json_text

    print("Smoke test passed.")
    print(f"  People:       {len(people)}")
    print(f"  Domains:      {len(config.domains)}")
    print(f"  Capabilities: {len(config.capabilities)}")
    print(f"  Dimensions:   {', '.join(config.dimension_ids)}")
    print(f"  {first['name']} overall: {profile['overall']}")
    print(f"  K/D gaps:     {len(profile['knowledge_delivery_gaps'])} capability(ies)")
    print("  Summary:")
    print("    " + profile["summary"])

    os.remove(db_path)


if __name__ == "__main__":
    main()
