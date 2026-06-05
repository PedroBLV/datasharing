"""Scoring logic for the skills matrix.

All scoring is derived from raw question responses plus the weights in the
YAML configuration, so the model can be re-tuned without touching stored
data. The headline numbers are:

* ``raw_score``      - simple mean of question scores in a capability area.
* ``weighted_score`` - question-weight-weighted mean within a capability.
* ``overall``        - capability-weight-weighted mean across capabilities.
* ``maturity_level`` - the weighted score rounded to the nearest maturity
                       band and mapped to its label.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd

from config import Config

# How many capability areas to surface as strengths / development areas.
TOP_N = 3
# A confidence gap is an area whose questions disagree strongly: the
# person is clearly experienced on some and not others within one area.
CONFIDENCE_GAP_SPREAD = 2.0


@dataclass
class CapabilityScore:
    capability_id: str
    capability_name: str
    raw_score: float
    weighted_score: float
    maturity_level: int
    maturity_label: str
    num_questions: int
    spread: float  # max-min of question scores within the area


def maturity_label(level: int, config: Config) -> str:
    ml = config.maturity_levels.get(level)
    return ml.label if ml else str(level)


def score_person(
    responses: pd.DataFrame, config: Config
) -> Dict[str, CapabilityScore]:
    """Compute per-capability scores for a single person's responses.

    ``responses`` must have columns ``question_id`` and ``score``. Only
    capability areas the person actually answered are returned.
    """
    question_by_id = config.question_by_id
    capability_by_id = config.capability_by_id

    # Group raw scores by capability area.
    by_capability: Dict[str, List[tuple]] = {}
    for _, row in responses.iterrows():
        q = question_by_id.get(row["question_id"])
        if q is None:
            # Question was removed from config; ignore stale response.
            continue
        by_capability.setdefault(q.capability_area, []).append(
            (float(row["score"]), q.weight)
        )

    results: Dict[str, CapabilityScore] = {}
    for cap_id, scored in by_capability.items():
        cap = capability_by_id.get(cap_id)
        if cap is None:
            continue
        scores = [s for s, _ in scored]
        weights = [w for _, w in scored]
        raw = sum(scores) / len(scores)
        weight_total = sum(weights) or 1.0
        weighted = sum(s * w for s, w in scored) / weight_total
        level = max(1, min(5, round(weighted)))
        results[cap_id] = CapabilityScore(
            capability_id=cap_id,
            capability_name=cap.name,
            raw_score=round(raw, 2),
            weighted_score=round(weighted, 2),
            maturity_level=level,
            maturity_label=maturity_label(level, config),
            num_questions=len(scores),
            spread=round(max(scores) - min(scores), 2),
        )
    return results


def overall_score(
    capability_scores: Dict[str, CapabilityScore], config: Config
) -> float:
    """Capability-weight-weighted mean across all answered capabilities."""
    if not capability_scores:
        return 0.0
    capability_by_id = config.capability_by_id
    total_w = 0.0
    total = 0.0
    for cap_id, cs in capability_scores.items():
        w = capability_by_id[cap_id].weight if cap_id in capability_by_id else 1.0
        total += cs.weighted_score * w
        total_w += w
    return round(total / (total_w or 1.0), 2)


def strengths_and_gaps(
    capability_scores: Dict[str, CapabilityScore], top_n: int = TOP_N
):
    """Return (strengths, development_areas, confidence_gaps).

    Strengths are the highest-scoring capability areas, development areas
    the lowest. Confidence gaps are areas with a wide spread of question
    scores - real experience in part of the area but not all of it.
    """
    ordered = sorted(
        capability_scores.values(), key=lambda c: c.weighted_score, reverse=True
    )
    strengths = [c.capability_name for c in ordered[:top_n]]
    development = [c.capability_name for c in ordered[-top_n:][::-1]]
    confidence_gaps = [
        c.capability_name
        for c in capability_scores.values()
        if c.spread >= CONFIDENCE_GAP_SPREAD
    ]
    return strengths, development, confidence_gaps


def build_person_profile(
    person: dict, responses: pd.DataFrame, config: Config
) -> dict:
    """Assemble the full individual profile, including AI-ready summary."""
    cap_scores = score_person(responses, config)
    overall = overall_score(cap_scores, config)
    strengths, development, gaps = strengths_and_gaps(cap_scores)

    scores_by_name = {
        cs.capability_name: cs.weighted_score for cs in cap_scores.values()
    }

    summary = _build_summary(person, strengths, development, gaps, overall)

    return {
        "person": person.get("name"),
        "role": person.get("role"),
        "team": person.get("team"),
        "location": person.get("location"),
        "overall_score": overall,
        "scores": scores_by_name,
        "capability_scores": cap_scores,
        "strengths": strengths,
        "development_areas": development,
        "confidence_gaps": gaps,
        "next_steps": _suggested_next_steps(cap_scores),
        "summary": summary,
    }


def _build_summary(
    person: dict,
    strengths: List[str],
    development: List[str],
    gaps: List[str],
    overall: float,
) -> str:
    name = person.get("name", "This person")
    role = person.get("role")
    parts = [
        f"{name}{f' ({role})' if role else ''} has an overall capability "
        f"score of {overall:.1f} out of 5."
    ]
    if strengths:
        parts.append("Strongest in " + ", ".join(strengths) + ".")
    if development:
        parts.append("Development needed around " + ", ".join(development) + ".")
    if gaps:
        parts.append(
            "Uneven experience (confidence gaps) in " + ", ".join(gaps) + "."
        )
    return " ".join(parts)


def _suggested_next_steps(
    capability_scores: Dict[str, CapabilityScore]
) -> List[str]:
    """Heuristic next steps based on the lowest-scoring areas."""
    steps = []
    lowest = sorted(
        capability_scores.values(), key=lambda c: c.weighted_score
    )[:TOP_N]
    for cs in lowest:
        if cs.weighted_score < 2:
            steps.append(
                f"Build foundational, hands-on experience in {cs.capability_name}."
            )
        elif cs.weighted_score < 3:
            steps.append(
                f"Move from experimentation to a working prototype in "
                f"{cs.capability_name}."
            )
        elif cs.weighted_score < 4:
            steps.append(
                f"Take {cs.capability_name} into a real client/project delivery."
            )
        else:
            steps.append(
                f"Lead others or productionise work in {cs.capability_name}."
            )
    return steps


# --- Team-level aggregation ----------------------------------------------


def team_matrix(
    people: pd.DataFrame, responses: pd.DataFrame, config: Config
) -> pd.DataFrame:
    """Return a person x capability matrix of weighted scores.

    Rows are people (indexed by name, with person_id retained), columns
    are capability names. Used for the team heatmap.
    """
    rows = []
    for _, person in people.iterrows():
        person_resp = responses[responses["person_id"] == person["person_id"]]
        cap_scores = score_person(person_resp, config)
        row = {
            "person_id": person["person_id"],
            "name": person["name"],
            "role": person["role"],
            "team": person["team"],
            "location": person["location"],
        }
        for cs in cap_scores.values():
            row[cs.capability_name] = cs.weighted_score
        rows.append(row)
    return pd.DataFrame(rows)


def team_averages(matrix: pd.DataFrame, config: Config) -> pd.Series:
    """Mean score per capability area across the team."""
    cap_names = [c.name for c in config.capabilities]
    present = [c for c in cap_names if c in matrix.columns]
    if not present:
        return pd.Series(dtype=float)
    return matrix[present].mean().round(2).sort_values(ascending=False)


def people_strong_in(
    matrix: pd.DataFrame, config: Config, threshold: float = 4.0
) -> Dict[str, List[str]]:
    """Map each capability to the people scoring at/above ``threshold``."""
    result: Dict[str, List[str]] = {}
    for cap in config.capabilities:
        if cap.name not in matrix.columns:
            continue
        strong = matrix[matrix[cap.name] >= threshold]["name"].tolist()
        result[cap.name] = strong
    return result


def team_gaps(
    averages: pd.Series, threshold: float = 3.0
) -> List[str]:
    """Capability areas where the team average is below ``threshold``."""
    return [name for name, score in averages.items() if score < threshold]
