"""Skills Matrix Assessment — Streamlit application.

A lightweight internal app to capture a structured, two-dimensional
capability assessment (Knowledge and Delivery), score it, and produce
individual and team-level insights — plus clean exports for later AI
analysis. Domains, capabilities, descriptions, weights and the scoring
scale all live in YAML so the matrix can be updated without code changes.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import io
import os

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
    latest_per_person,
    people_strong_in,
    score_person,
    team_averages,
    team_gaps,
    team_matrix,
)

st.set_page_config(
    page_title="Skills Matrix Assessment",
    page_icon="🧭",
    layout="wide",
)


@st.cache_resource
def get_db() -> Database:
    return Database()


def get_config():
    """Load config fresh each run so Admin edits take effect immediately."""
    return load_config()


def _scale_options(config):
    return sorted(config.maturity_levels)


def _scale_label(config):
    return lambda v: f"{v} — {config.maturity_levels[v].label}"


def _guidance_text(config, capability) -> str:
    """Per-capability 0-5 scoring guidance as a single help string."""
    guidance = config.level_guidance(capability)
    return "\n\n".join(f"**{lvl}** — {guidance[lvl]}" for lvl in sorted(guidance))


# --- Assessment view ------------------------------------------------------


def render_assessment(config) -> None:
    st.header("Complete your skills assessment")
    st.caption(
        "Rate each capability on **Knowledge** (do you understand it?) and "
        "**Delivery** (have you shipped it?). One domain per tab — fill them "
        "in any order, then submit at the bottom."
    )

    dims = config.dimensions
    with st.expander("How the 0–5 scale works", expanded=False):
        sc, dc = st.columns(2)
        with sc:
            st.markdown("**Scale**")
            for level in sorted(config.maturity_levels):
                ml = config.maturity_levels[level]
                st.markdown(f"`{level}` **{ml.label}** — {ml.description}")
        with dc:
            st.markdown("**Dimensions**")
            for d in dims:
                st.markdown(f"**{d.name}** — {d.description}")

    options = _scale_options(config)
    fmt = _scale_label(config)

    with st.form("assessment_form", clear_on_submit=False):
        # About you — compact row at the top.
        ac1, ac2, ac3, ac4 = st.columns(4)
        with ac1:
            name = st.text_input("Name *")
        with ac2:
            role = st.text_input("Role", placeholder="AI Specialist")
        with ac3:
            team = st.text_input("Team", placeholder="AI Practice")
        with ac4:
            location = st.text_input("Location", placeholder="London")

        # One tab per domain — far less to scroll past, far less noise.
        domain_tabs = st.tabs(
            [f"{d.name} · {d.weight:.0f}%" for d in config.domains]
        )
        responses: list = []
        for tab, domain in zip(domain_tabs, config.domains):
            caps = config.capabilities_for(domain.id)
            if not caps:
                continue
            with tab:
                if domain.description:
                    st.caption(domain.description)
                for cap in caps:
                    # Capability name + description visible; scoring guidance
                    # is on the slider's hover tooltip so it doesn't add chrome.
                    guidance = _guidance_text(config, cap)
                    help_text = (
                        (cap.description + "\n\n" if cap.description else "")
                        + "**Scoring guidance:**\n\n"
                        + guidance
                    )
                    st.markdown(f"**{cap.name}**")
                    if cap.description:
                        st.caption(cap.description)
                    rcols = st.columns(2)
                    for col, dim in zip(rcols, dims):
                        with col:
                            score = st.select_slider(
                                dim.name,
                                options=options,
                                value=0,
                                format_func=fmt,
                                key=f"score_{cap.id}_{dim.id}",
                                help=help_text,
                            )
                            responses.append(
                                {
                                    "capability_id": cap.id,
                                    "domain": cap.domain,
                                    "dimension": dim.id,
                                    "score": score,
                                }
                            )

        st.markdown("")
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

    # Pick a person by name, then (if they have a history) pick which of
    # their assessments to inspect. Defaults to the most recent.
    people = people.sort_values("date_completed", ascending=False).reset_index(
        drop=True
    )
    names = list(dict.fromkeys(people["name"]))  # de-duplicate, preserve order
    default_name = names[0]
    last_id = st.session_state.get("last_person_id")
    if last_id is not None:
        match = people[people["person_id"] == last_id]
        if not match.empty:
            default_name = match.iloc[0]["name"]
    pick = st.columns([2, 2, 1])
    with pick[0]:
        chosen_name = st.selectbox(
            "Person", names, index=names.index(default_name)
        )

    history = people[people["name"] == chosen_name].reset_index(drop=True)
    with pick[1]:
        if len(history) > 1:
            assessment_options = list(history.index)
            default_assessment = 0
            if last_id in set(history["person_id"]):
                default_assessment = int(
                    history.index[history["person_id"] == last_id][0]
                )
            chosen_idx = st.selectbox(
                f"Assessment ({len(history)} total)",
                options=assessment_options,
                index=default_assessment,
                format_func=lambda i: history.loc[i, "date_completed"][:10]
                + (" (latest)" if i == 0 else ""),
            )
        else:
            chosen_idx = 0
            st.caption("Only one assessment on record.")

    person = history.loc[chosen_idx].to_dict()
    responses = db.get_responses(person["person_id"])
    if responses.empty:
        st.warning("This assessment has no recorded responses.")
        return

    profile = build_person_profile(person, responses, config)
    dims = config.dimensions

    top = st.columns([1, 2])
    with top[0]:
        mcols = st.columns(len(dims))
        for col, dim in zip(mcols, dims):
            val = profile["overall"].get(dim.id)
            col.metric(dim.name, f"{val:.1f} / 5" if val is not None else "—")
        st.markdown(f"**Role:** {person.get('role') or '—'}")
        st.markdown(f"**Team:** {person.get('team') or '—'}")
        st.markdown(f"**Location:** {person.get('location') or '—'}")
    with top[1]:
        st.markdown("##### Summary")
        st.write(profile["summary"])

    # Radar: one trace per dimension across domains.
    domain_scores = profile["domain_scores"]
    if domain_scores:
        domain_names = [ds.domain_name for ds in domain_scores.values()]
        radar = go.Figure()
        for dim in dims:
            vals = [ds.scores.get(dim.id, 0) for ds in domain_scores.values()]
            radar.add_trace(
                go.Scatterpolar(
                    r=vals + [vals[0]],
                    theta=domain_names + [domain_names[0]],
                    fill="toself",
                    name=dim.name,
                    opacity=0.6,
                )
            )
        radar.update_layout(
            polar=dict(radialaxis=dict(visible=True, range=[0, 5])),
            showlegend=True,
            margin=dict(l=40, r=40, t=40, b=40),
            height=480,
        )
        st.plotly_chart(radar, width="stretch")

    cols = st.columns(3)
    with cols[0]:
        st.markdown("##### 💪 Strongest domains")
        for s in profile["strengths"]:
            st.markdown(f"- {s}")
    with cols[1]:
        st.markdown("##### 🚢 Genuinely delivered")
        if profile["delivered_capabilities"]:
            for d in profile["delivered_capabilities"]:
                st.markdown(f"- {d}")
        else:
            st.caption("No capability rated 4+ on Delivery yet.")
    with cols[2]:
        st.markdown("##### 📈 Development areas")
        for d in profile["development_areas"]:
            st.markdown(f"- {d}")

    st.markdown("##### Suggested next steps")
    for step in profile["next_steps"]:
        st.markdown(f"- {step}")

    with st.expander("Knowledge ahead of delivery"):
        st.caption(
            "Capabilities understood better than they've been shipped — the "
            "'read about it but never built it' signal."
        )
        if profile["knowledge_delivery_gaps"]:
            gap_df = pd.DataFrame(profile["knowledge_delivery_gaps"]).rename(
                columns={
                    "capability": "Capability",
                    "knowledge": "Knowledge",
                    "delivery": "Delivery",
                    "gap": "Gap",
                }
            )
            st.dataframe(gap_df, width="stretch", hide_index=True)
        else:
            st.caption("None — knowledge and delivery are well matched.")

    with st.expander("Detailed scores by domain"):
        rows = []
        for ds in profile["domain_scores"].values():
            row = {"Domain": ds.domain_name}
            for dim in dims:
                row[dim.name] = ds.scores.get(dim.id)
            row["Gap (K−D)"] = ds.gap()
            row["Capabilities"] = ds.num_capabilities
            rows.append(row)
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    # Show assessment history for this person if there's more than one.
    if len(history) > 1:
        with st.expander(f"Assessment history ({len(history)} on record)"):
            st.caption(
                "Aggregate views (Team, exports) use only the most recent "
                "assessment per person."
            )
            hist_rows = []
            for _, h in history.iterrows():
                hp = db.get_responses(h["person_id"])
                if hp.empty:
                    continue
                hs = score_person(hp, config)["overall"]
                row = {"Date": h["date_completed"][:10]}
                for dim in dims:
                    row[dim.name] = hs.get(dim.id)
                row["Assessment id"] = h["person_id"][:8]
                hist_rows.append(row)
            st.dataframe(
                pd.DataFrame(hist_rows), width="stretch", hide_index=True
            )


# --- Team view ------------------------------------------------------------


def render_team(config) -> None:
    st.header("Team capability view")
    db = get_db()
    all_people = db.get_people()
    responses = db.get_responses()
    if all_people.empty:
        st.info("No assessments yet. Complete one in the Assessment tab.")
        return

    # Aggregations use only the most recent assessment per person, so
    # someone retaking the assessment doesn't double-count.
    people = latest_per_person(all_people)
    if len(all_people) != len(people):
        st.caption(
            f"Aggregating **{len(people)} people** (using the most recent "
            f"of {len(all_people)} total assessments)."
        )

    dims = config.dimensions
    dim_by_name = {d.name: d for d in dims}
    chosen_name = st.radio(
        "Dimension", list(dim_by_name), horizontal=True
    )
    dimension = dim_by_name[chosen_name].id

    matrix = team_matrix(people, responses, config, dimension)

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

    st.caption(f"{len(matrix)} people in view · showing **{chosen_name}** scores.")
    averages = team_averages(matrix, config)

    domain_cols = [d.name for d in config.domains if d.name in matrix.columns]
    heat_df = matrix.set_index("name")[domain_cols]
    fig = px.imshow(
        heat_df,
        color_continuous_scale="Blues",
        zmin=0,
        zmax=5,
        aspect="auto",
        labels=dict(color=chosen_name),
    )
    fig.update_layout(
        height=max(360, 40 * len(heat_df) + 160),
        margin=dict(l=40, r=40, t=40, b=140),
    )
    st.markdown(f"##### Capability heatmap — {chosen_name}")
    st.plotly_chart(fig, width="stretch")

    # Knowledge vs Delivery scatter — the headline distinction.
    st.markdown("##### Knowledge vs Delivery (overall, per person)")
    st.caption(
        "Above the diagonal: knows more than they've shipped. On/below: "
        "delivery keeps pace with knowledge."
    )
    scatter_rows = []
    keep_ids = set(matrix["person_id"])
    for _, person in people[people["person_id"].isin(keep_ids)].iterrows():
        pr = responses[responses["person_id"] == person["person_id"]]
        overall = score_person(pr, config)["overall"]
        scatter_rows.append(
            {
                "name": person["name"],
                "Knowledge": overall.get("knowledge"),
                "Delivery": overall.get("delivery"),
            }
        )
    sdf = pd.DataFrame(scatter_rows).dropna(subset=["Knowledge", "Delivery"])
    if not sdf.empty:
        scat = px.scatter(
            sdf,
            x="Knowledge",
            y="Delivery",
            text="name",
            range_x=[0, 5],
            range_y=[0, 5],
        )
        scat.add_shape(
            type="line", x0=0, y0=0, x1=5, y1=5, line=dict(dash="dash", color="grey")
        )
        scat.update_traces(textposition="top center")
        scat.update_layout(height=460, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(scat, width="stretch")

    left, right = st.columns(2)
    with left:
        st.markdown(f"##### Average {chosen_name} by domain")
        avg_df = averages.rename("Average").reset_index()
        avg_df.columns = ["Domain", "Average"]
        bar = px.bar(
            avg_df, x="Average", y="Domain", orientation="h", range_x=[0, 5]
        )
        bar.update_layout(height=420, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(bar, width="stretch")
    with right:
        st.markdown("##### Domain gaps across the team")
        gaps = team_gaps(averages)
        if gaps:
            for g in gaps:
                st.markdown(f"- **{g}** (team average {averages[g]:.1f})")
        else:
            st.caption("No domain is below the 3.0 team threshold.")

        st.markdown(f"##### Strong in each domain ({chosen_name} ≥ 4.0)")
        strong = people_strong_in(matrix, config)
        for dom_name, names in strong.items():
            if names:
                st.markdown(f"- **{dom_name}**: {', '.join(names)}")


def _multiselect_filter(label: str, series: pd.Series):
    options = sorted([v for v in series.dropna().unique() if str(v).strip()])
    return st.multiselect(label, options)


# --- Admin view -----------------------------------------------------------


def render_admin(config) -> None:
    st.header("Admin")
    db = get_db()
    all_people = db.get_people()
    responses = db.get_responses()

    # Aggregate exports use latest assessment per person; raw records keeps
    # the full history.
    people = latest_per_person(all_people)

    st.markdown("##### Export results")
    if all_people.empty:
        st.info("No data to export yet.")
    else:
        if len(all_people) != len(people):
            st.caption(
                f"Aggregate exports use the most recent assessment per person "
                f"({len(people)} of {len(all_people)} total). The raw records "
                f"export contains the full history."
            )
        ecols = st.columns(3)
        with ecols[0]:
            st.download_button(
                "Download scores CSV",
                data=export_mod.people_scores_csv(people, responses, config),
                file_name="team_results.csv",
                mime="text/csv",
            )
        with ecols[1]:
            st.download_button(
                "Download AI-ready JSON",
                data=export_mod.ai_ready_json(people, responses, config),
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
    st.markdown("##### Update the matrix")
    st.caption(
        "Domains, capabilities, descriptions, weights and the scoring scale "
        "all live in YAML so the matrix can change without editing code."
    )
    dtab, ctab = st.tabs(["domains.yaml", "capabilities.yaml"])
    _render_yaml_editor(dtab, "domains.yaml")
    _render_yaml_editor(ctab, "capabilities.yaml")

    st.divider()
    st.markdown("##### Manage assessments")
    if not all_people.empty:
        view = all_people[["name", "role", "team", "location", "date_completed"]]
        st.dataframe(view, width="stretch", hide_index=True)
        to_delete = st.selectbox(
            "Delete an assessment",
            options=["—"] + list(all_people["person_id"]),
            format_func=lambda pid: "—"
            if pid == "—"
            else (
                f"{all_people.set_index('person_id').loc[pid, 'name']} · "
                f"{all_people.set_index('person_id').loc[pid, 'date_completed'][:10]} "
                f"({pid[:8]})"
            ),
        )
        if to_delete != "—" and st.button("Delete selected", type="secondary"):
            db.delete_person(to_delete)
            st.success("Assessment deleted.")
            st.rerun()


def _render_yaml_editor(container, filename: str) -> None:
    path = os.path.join(APP_DIR, filename)
    with container:
        with open(path, "r", encoding="utf-8") as fh:
            current = fh.read()
        edited = st.text_area(
            filename, value=current, height=400, key=f"editor_{filename}"
        )
        if st.button(f"Save {filename}", key=f"save_{filename}"):
            try:
                yaml.safe_load(edited)
            except yaml.YAMLError as exc:
                st.error(f"Not valid YAML: {exc}")
                return
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(edited)
            try:
                load_config()
            except ConfigError as exc:
                st.error(f"Saved, but configuration is now invalid: {exc}")
                return
            st.success(f"Saved {filename}. Changes apply on next interaction.")


# --- Main -----------------------------------------------------------------


def main() -> None:
    st.sidebar.title("🧭 Skills Matrix")
    st.sidebar.caption("Two-dimensional capability assessment")

    try:
        config = get_config()
    except ConfigError as exc:
        st.error(f"Configuration error: {exc}")
        st.stop()

    view = st.sidebar.radio("View", ["Assessment", "Individual", "Team", "Admin"])
    st.sidebar.divider()
    st.sidebar.caption(
        f"{len(config.capabilities)} capabilities across "
        f"{len(config.domains)} domains, rated on "
        f"{len(config.dimensions)} dimensions."
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
