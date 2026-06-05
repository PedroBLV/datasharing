"""Loading and validation of the YAML configuration.

The questions, capability areas, weights and scoring definitions live in
``questions.yaml`` and ``capabilities.yaml`` so the skills matrix can be
updated without changing application code. This module is the single
place that reads those files and turns them into plain Python objects the
rest of the app can rely on.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List

import yaml

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CAPABILITIES_PATH = os.path.join(APP_DIR, "capabilities.yaml")
QUESTIONS_PATH = os.path.join(APP_DIR, "questions.yaml")


@dataclass
class MaturityLevel:
    level: int
    label: str
    description: str


@dataclass
class Capability:
    id: str
    name: str
    weight: float
    description: str = ""


@dataclass
class Question:
    id: str
    capability_area: str
    text: str
    weight: float = 1.0
    response_type: str = "maturity_1_5"


@dataclass
class Config:
    capabilities: List[Capability]
    questions: List[Question]
    maturity_levels: Dict[int, MaturityLevel] = field(default_factory=dict)

    @property
    def capability_by_id(self) -> Dict[str, Capability]:
        return {c.id: c for c in self.capabilities}

    @property
    def question_by_id(self) -> Dict[str, Question]:
        return {q.id: q for q in self.questions}

    def questions_for(self, capability_id: str) -> List[Question]:
        return [q for q in self.questions if q.capability_area == capability_id]


def _load_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_capabilities(path: str = CAPABILITIES_PATH):
    data = _load_yaml(path)

    capabilities = [
        Capability(
            id=c["id"],
            name=c.get("name", c["id"]),
            weight=float(c.get("weight", 1.0)),
            description=c.get("description", ""),
        )
        for c in data.get("capabilities", [])
    ]

    maturity_levels: Dict[int, MaturityLevel] = {}
    for level, body in (data.get("maturity_levels") or {}).items():
        level_int = int(level)
        maturity_levels[level_int] = MaturityLevel(
            level=level_int,
            label=body.get("label", str(level_int)),
            description=body.get("description", ""),
        )

    return capabilities, maturity_levels


def load_questions(path: str = QUESTIONS_PATH) -> List[Question]:
    data = _load_yaml(path)
    default_type = data.get("default_response_type", "maturity_1_5")
    return [
        Question(
            id=q["id"],
            capability_area=q["capability_area"],
            text=q["text"],
            weight=float(q.get("weight", 1.0)),
            response_type=q.get("response_type", default_type),
        )
        for q in data.get("questions", [])
    ]


def load_config(
    capabilities_path: str = CAPABILITIES_PATH,
    questions_path: str = QUESTIONS_PATH,
) -> Config:
    """Load and validate the full configuration.

    Raises ``ConfigError`` if a question references a capability area that
    does not exist, so misconfiguration is surfaced loudly rather than
    silently dropping responses.
    """
    capabilities, maturity_levels = load_capabilities(capabilities_path)
    questions = load_questions(questions_path)

    capability_ids = {c.id for c in capabilities}
    seen_question_ids = set()
    for q in questions:
        if q.capability_area not in capability_ids:
            raise ConfigError(
                f"Question '{q.id}' references unknown capability "
                f"'{q.capability_area}'. Valid ids: {sorted(capability_ids)}"
            )
        if q.id in seen_question_ids:
            raise ConfigError(f"Duplicate question id '{q.id}' in questions.yaml")
        seen_question_ids.add(q.id)

    if not capabilities:
        raise ConfigError("No capabilities defined in capabilities.yaml")
    if not questions:
        raise ConfigError("No questions defined in questions.yaml")

    return Config(
        capabilities=capabilities,
        questions=questions,
        maturity_levels=maturity_levels,
    )


class ConfigError(Exception):
    """Raised when the YAML configuration is invalid or inconsistent."""
