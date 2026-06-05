"""Skills Matrix Assessment — Streamlit application.

A lightweight internal app to capture a structured capability assessment,
score it, and produce individual and team-level insights. Questions,
capability areas and weights all live in YAML config so the matrix can be
updated without changing this code.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import io

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import yaml

import export as export_mod
from config import APP_DIR, ConfigError, load_config
from database import Database
from scoring import (
    build_person_profile,
    people_strong_in,
    team_averages,
    team_gaps,
    team_matrix,
)

st.set_page_config(
    page_title="Skills Matrix Assessment",
    page_icon="🧭",
    layout="wide",
)


# --- Cached resources -----------------------------------------------------


@st.cache_resource
def get_db() -> Database:
    return Database()


def get_config():
    """Load config fresh each run so edits in the Admin tab take effect."""
    return load_config()


# --- Assessment view ------------------------------------------------------


def render_assessment(config) -> None:
    st.header("Complete your skills assessment")
    st.caption(
        "Rate your experience on each capability. The scale rewards "
        "practical and delivery experience, not just theory."
    )

    with st.expander("What do the scores mean?", expanded=False):
        for level in sorted(config.maturity_levels):
            ml = config.maturity_levels[level]
            st.markdown(f"**{level} — {ml.label}**: {ml.description}")

    with st.form("assessment_form", clear_on_submit=False):
        st.subheader("About you")
        col1, col2 = st.columns(2)
        with col1:
            name = st.text_input("Name *")
            role = st.text_input("Role", placeholder="e.g. AI Specialist")
        with col2:
            team = st.text_input("Team", placeholder="e.g. AI Practice")
            location = st.text_input("Location", placeholder="e.g. London")

        st.divider()
        scale_labels = {
            level: f"{level} — {config.maturity_levels[level].label}"
            for level in sorted(config.maturity_levels)
        }

        responses: dict = {}
        for cap in config.capabilities:
            questions = config.questions_for(cap.id)
            if not questions:
                continue
            st.subheader(cap.name)
            if cap.description:
                st.caption(cap.description)
            for q in questions:
                score = st.select_slider(
                    q.text,
                    options=sorted(scale_labels),
                    value=1,
                    format_func=lambda v: scale_labels[v],
                    key=f"score_{q.id}",
                )
                comment = st.text_input(
                    "Optional comment",
                    key=f"comment_{q.id}",
                    placeholder="Context, recent examples, caveats…",
                    label_visibility="collapsed",
                )
                responses[q.id] = {
                    "score": score,
                    "capability_area": cap.id,
                    "comment": comment.strip() or None,
                }

        submitted = st.form_submit_button("Submit assessment", type="primary")

    if submitted:
        if not name.strip():
            st.error("Please enter your name before submitting.")
            return
        db = get_db()
        person_id = db.save_assessment(
            name=name.strip(),
            role=role.strip(),
            location=location.strip(),
            team=team.strip(),
            responses=responses,
        )
        st.success(
            f"Thanks {name.strip()} — your assessment has been saved. "
            "Open the Individual view to see your profile."
        )
        st.session_state["last_person_id"] = person_id


# --- Individual view ------------------------------------------------------


def render_individual(config) -> None:
    st.header("Individual capability profile")
    db = get_db()
    people = db.get_people()
    if people.empty:
        st.info("No assessments yet. Complete one in the Assessment tab.")
        return

    people = people.copy()
    people["label"] = (
        people["name"]
        + "  ·  "
        + people["date_completed"].str.slice(0, 10).fillna("")
    )
    default_idx = 0
    last = st.session_state.get("last_person_id")
    if last is not None and last in set(people["person_id"]):
        default_idx = int(people.index[people["person_id"] == last][0])

    choice = st.selectbox(
        "Select a person",
        options=list(people.index),
        index=default_idx,
        format_func=lambda i: people.loc[i, "label"],
    )
    person = people.loc[choice].to_dict()
    responses = db.get_responses(person["person_id"])
    if responses.empty:
        st.warning("This person has no recorded responses.")
        return

    profile = build_person_profile(person, responses, config)

    top = st.columns([1, 2])
    with top[0]:
        st.metric("Overall score", f"{profile['overall_score']:.1f} / 5")
        st.markdown(f"**Role:** {person.get('role') or '—'}")
        st.markdown(f"**Team:** {person.get('team') or '—'}")
        st.markdown(f"**Location:** {person.get('location') or '—'}")
    with top[1]:
        st.markdown("##### Summary")
        st.write(profile["summary"])

    # Radar chart of capability scores.
    scores = profile["scores"]
    if scores:
        cats = list(scores.keys())
        vals = list(scores.values())
        radar = go.Figure()
        radar.add_trace(
            go.Scatterpolar(
                r=vals + [vals[0]],
                theta=cats + [cats[0]],
                fill="toself",
                name=person["name"],
            )
        )
        radar.update_layout(
            polar=dict(radialaxis=dict(visible=True, range=[0, 5])),
            showlegend=False,
            margin=dict(l=40, r=40, t=40, b=40),
            height=480,
        )
        st.plotly_chart(radar, use_container_width=True)

    cols = st.columns(3)
    with cols[0]:
        st.markdown("##### 💪 Strengths")
        for s in profile["strengths"]:
            st.markdown(f"- {s}")
    with cols[1]:
        st.markdown("##### 📈 Development areas")
        for d in profile["development_areas"]:
            st.markdown(f"- {d}")
    with cols[2]:
        st.markdown("##### ⚠️ Confidence gaps")
        if profile["confidence_gaps"]:
            for g in profile["confidence_gaps"]:
                st.markdown(f"- {g}")
        else:
            st.caption("None — scores are consistent within each area.")

    st.markdown("##### Suggested next steps")
    for step in profile["next_steps"]:
        st.markdown(f"- {step}")

    with st.expander("Detailed scores by capability"):
        detail = pd.DataFrame(
            [
                {
                    "Capability": cs.capability_name,
                    "Raw score": cs.raw_score,
                    "Weighted score": cs.weighted_score,
                    "Maturity": f"{cs.maturity_level} — {cs.maturity_label}",
                    "Questions": cs.num_questions,
                }
                for cs in profile["capability_scores"].values()
            ]
        )
        st.dataframe(detail, use_container_width=True, hide_index=True)


# --- Team view ------------------------------------------------------------


def render_team(config) -> None:
    st.header("Team capability view")
    db = get_db()
    people = db.get_people()
    responses = db.get_responses()
    if people.empty:
        st.info("No assessments yet. Complete one in the Assessment tab.")
        return

    matrix = team_matrix(people, responses, config)

    # Filters.
    with st.expander("Filters", expanded=False):
        fcols = st.columns(3)
        with fcols[0]:
            roles = _multiselect_filter("Role", matrix["role"])
        with fcols[1]:
            teams = _multiselect_filter("Team", matrix["team"])
        with fcols[2]:
            locations = _multiselect_filter("Location", matrix["location"])
    if roles:
        matrix = matrix[matrix["role"].isin(roles)]
    if teams:
        matrix = matrix[matrix["team"].isin(teams)]
    if locations:
        matrix = matrix[matrix["location"].isin(locations)]

    if matrix.empty:
        st.warning("No people match the selected filters.")
        return

    st.caption(f"{len(matrix)} people in view.")
    averages = team_averages(matrix, config)

    # Heatmap of person x capability.
    cap_cols = [c.name for c in config.capabilities if c.name in matrix.columns]
    heat_df = matrix.set_index("name")[cap_cols]
    fig = px.imshow(
        heat_df,
        color_continuous_scale="Blues",
        zmin=1,
        zmax=5,
        aspect="auto",
        labels=dict(color="Score"),
    )
    fig.update_layout(
        height=max(360, 40 * len(heat_df) + 160),
        margin=dict(l=40, r=40, t=40, b=120),
    )
    st.markdown("##### Capability heatmap")
    st.plotly_chart(fig, use_container_width=True)

    left, right = st.columns(2)
    with left:
        st.markdown("##### Average score by area")
        avg_df = averages.rename("Average").reset_index()
        avg_df.columns = ["Capability", "Average"]
        bar = px.bar(
            avg_df,
            x="Average",
            y="Capability",
            orientation="h",
            range_x=[0, 5],
        )
        bar.update_layout(height=480, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(bar, use_container_width=True)
    with right:
        st.markdown("##### Capability gaps across the team")
        gaps = team_gaps(averages)
        if gaps:
            for g in gaps:
                st.markdown(f"- **{g}** (team average {averages[g]:.1f})")
        else:
            st.caption("No capability area is below the 3.0 team threshold.")

        st.markdown("##### People strong in each capability (≥ 4.0)")
        strong = people_strong_in(matrix, config)
        for cap_name, names in strong.items():
            if names:
                st.markdown(f"- **{cap_name}**: {', '.join(names)}")


def _multiselect_filter(label: str, series: pd.Series):
    options = sorted([v for v in series.dropna().unique() if str(v).strip()])
    return st.multiselect(label, options)


# --- Admin view -----------------------------------------------------------


def render_admin(config) -> None:
    st.header("Admin")
    db = get_db()
    people = db.get_people()
    responses = db.get_responses()

    st.markdown("##### Export results")
    if people.empty:
        st.info("No data to export yet.")
    else:
        ecols = st.columns(3)
        with ecols[0]:
            csv_data = export_mod.people_scores_csv(people, responses, config)
            st.download_button(
                "Download scores CSV",
                data=csv_data,
                file_name="team_results.csv",
                mime="text/csv",
            )
        with ecols[1]:
            json_data = export_mod.ai_ready_json(people, responses, config)
            st.download_button(
                "Download AI-ready JSON",
                data=json_data,
                file_name="team_results.json",
                mime="application/json",
            )
        with ecols[2]:
            raw = pd.DataFrame(db.export_records())
            st.download_button(
                "Download raw records JSON",
                data=raw.to_json(orient="records", indent=2),
                file_name="raw_records.json",
                mime="application/json",
            )

    st.divider()
    st.markdown("##### Import previous results")
    st.caption("Upload a raw records JSON exported from this app to merge it in.")
    uploaded = st.file_uploader("Records JSON", type=["json"], key="import_records")
    if uploaded is not None and st.button("Import records"):
        try:
            records = pd.read_json(io.BytesIO(uploaded.getvalue())).to_dict(
                orient="records"
            )
            inserted = db.import_people(records)
            st.success(f"Imported {inserted} new assessment(s).")
        except Exception as exc:  # noqa: BLE001 - surface any import problem
            st.error(f"Could not import file: {exc}")

    st.divider()
    st.markdown("##### Update the question set")
    st.caption(
        "Questions and capability areas live in YAML so the matrix can be "
        "changed without editing code. Edit below and save."
    )
    qtab, ctab = st.tabs(["questions.yaml", "capabilities.yaml"])
    _render_yaml_editor(qtab, "questions.yaml")
    _render_yaml_editor(ctab, "capabilities.yaml")

    st.divider()
    st.markdown("##### Manage people")
    if not people.empty:
        view = people[["name", "role", "team", "location", "date_completed"]]
        st.dataframe(view, use_container_width=True, hide_index=True)
        to_delete = st.selectbox(
            "Delete an assessment",
            options=["—"] + list(people["person_id"]),
            format_func=lambda pid: "—"
            if pid == "—"
            else f"{people.set_index('person_id').loc[pid, 'name']} ({pid[:8]})",
        )
        if to_delete != "—" and st.button("Delete selected", type="secondary"):
            db.delete_person(to_delete)
            st.success("Assessment deleted.")
            st.rerun()


def _render_yaml_editor(container, filename: str) -> None:
    import os

    path = os.path.join(APP_DIR, filename)
    with container:
        with open(path, "r", encoding="utf-8") as fh:
            current = fh.read()
        edited = st.text_area(
            filename, value=current, height=400, key=f"editor_{filename}"
        )
        if st.button(f"Save {filename}", key=f"save_{filename}"):
            try:
                yaml.safe_load(edited)  # validate it parses
            except yaml.YAMLError as exc:
                st.error(f"Not valid YAML: {exc}")
                return
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(edited)
            # Validate the whole config still loads consistently.
            try:
                load_config()
            except ConfigError as exc:
                st.error(f"Saved, but configuration is now invalid: {exc}")
                return
            st.success(f"Saved {filename}. Changes apply on next interaction.")


# --- Main -----------------------------------------------------------------


def main() -> None:
    st.sidebar.title("🧭 Skills Matrix")
    st.sidebar.caption("Capability assessment & team intelligence")

    try:
        config = get_config()
    except ConfigError as exc:
        st.error(f"Configuration error: {exc}")
        st.stop()

    view = st.sidebar.radio(
        "View",
        ["Assessment", "Individual", "Team", "Admin"],
    )
    st.sidebar.divider()
    st.sidebar.caption(
        f"{len(config.questions)} questions across "
        f"{len(config.capabilities)} capability areas."
    )

    if view == "Assessment":
        render_assessment(config)
    elif view == "Individual":
        render_individual(config)
    elif view == "Team":
        render_team(config)
    elif view == "Admin":
        render_admin(config)


if __name__ == "__main__":
    main()
