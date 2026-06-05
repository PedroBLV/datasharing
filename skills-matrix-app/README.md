# Skills Matrix Assessment App

A lightweight internal Streamlit application that replaces the previous
Spark Think skills matrix quiz. People complete a structured capability
assessment; the app stores their responses, calculates weighted scores,
and produces individual and team-level insights — plus clean exports for
later AI analysis.

The guiding principle is that **the questions and scoring model live
outside the code**. To change the matrix you edit YAML, not Python.

## Features

- **Assessment** — complete a capability assessment grouped by area, on a
  1–5 maturity scale that rewards practical and delivery experience.
- **Individual view** — overall score, capability radar chart, strengths,
  development areas, confidence gaps and suggested next steps.
- **Team view** — capability heatmap, average score by area, gaps across
  the team, who is strong in each capability, with role/team/location
  filters.
- **Admin** — export CSV / AI-ready JSON, import previous results, edit
  the YAML question set in-app, and manage people.

## Quick start

```bash
cd skills-matrix-app
pip install -r requirements.txt
streamlit run app.py
```

To try it with sample data and run a quick logic check (no Streamlit
needed):

```bash
python seed_demo.py
```

## Project structure

```
skills-matrix-app/
  app.py            Streamlit UI (Assessment / Individual / Team / Admin)
  config.py         Loads & validates the YAML configuration
  database.py       SQLite persistence (people + responses)
  scoring.py        Per-person and team scoring logic
  export.py         CSV and AI-ready JSON exports
  seed_demo.py      Sample data + smoke test
  questions.yaml    The questions (editable without code changes)
  capabilities.yaml Capability areas, weights and maturity levels
  requirements.txt
  data/             SQLite database lives here (git-ignored)
  exports/          Generated exports (git-ignored)
```

## Configuration

Everything about the matrix is in two YAML files:

- **`capabilities.yaml`** — the capability areas, their relative weights
  (used to roll capability scores up into an overall score) and the
  shared 1–5 maturity-level definitions.
- **`questions.yaml`** — the questions. Each references a `capability_area`
  from `capabilities.yaml` and has its own `weight` within that area.

Add, remove or re-weight questions and capability areas by editing these
files (or via the in-app **Admin → Update the question set** editor). The
config is validated on load: a question pointing at an unknown capability,
or a duplicate question id, raises a clear error rather than silently
dropping data.

> Note: changing a question's `id` orphans historical responses keyed on
> the old id, so keep ids stable and only retire them by removing the
> question.

## Scoring model

| Metric | How it is computed |
| --- | --- |
| Question score | The raw 1–5 answer. |
| `raw_score` (per area) | Mean of question scores in the area. |
| `weighted_score` (per area) | Question-weight-weighted mean in the area. |
| `overall_score` | Capability-weight-weighted mean across areas. |
| `maturity_level` | Weighted score rounded to the nearest 1–5 band. |
| Strengths / development | Highest / lowest scoring capability areas. |
| Confidence gaps | Areas with a wide spread of question scores. |

Maturity bands:

1. **Awareness** — I understand the concept.
2. **Practitioner** — I have experimented with it.
3. **Builder** — I have built a working prototype.
4. **Delivery-capable** — I have delivered it in a client/project context.
5. **Production/leadership-capable** — I have productionised it or led others.

## AI-ready export

The Admin tab exports a prompt-ready JSON structure for downstream AI
analysis (summaries, training paths, staffing), shaped like:

```json
{
  "team_size": 4,
  "team_averages": { "RAG & Knowledge Systems": 3.4 },
  "people": [
    {
      "person": "Name",
      "role": "AI Specialist",
      "scores": { "RAG & Knowledge Systems": 4.2, "Agentic AI": 3.5 },
      "strengths": ["RAG & Knowledge Systems", "Delivery & Consulting"],
      "development_areas": ["Agentic AI", "Cloud & Platform Engineering"],
      "summary": "Strong delivery experience in RAG…"
    }
  ]
}
```

## Data & privacy

Responses are stored locally in `data/responses.db` (SQLite), which is
git-ignored. Each submission is a new record, so the same person retaking
the assessment builds a history rather than overwriting earlier answers.
Exports and the database are excluded from version control by default.
