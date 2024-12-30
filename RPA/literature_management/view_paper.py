"""
Streamlit page for viewing individual paper assessments from the SLR database.
"""
import streamlit as st
import pandas as pd
import sqlite3
from pathlib import Path

def get_connection():
    """Create a connection to the SQLite database."""
    return sqlite3.connect('literature.db', check_same_thread=False)

def load_paper_details():
    """Load all paper details with their assessments."""
    conn = get_connection()
    query = """
    SELECT 
        p.doi,
        p.title,
        p.authors,
        p.publication_year,
        p.venue,
        p.volume,
        p.issue,
        p.download_link,
        p.source_file,
        a.is_neurosymbolic,
        a.key_development,
        a.contribution,
        a.assessment_date
    FROM papers p
    LEFT JOIN paper_assessments a ON p.doi = a.paper_id
    WHERE a.assessment_date IS NOT NULL
    ORDER BY a.assessment_date DESC, p.publication_year DESC
    """
    return pd.read_sql_query(query, conn)

def display_paper_details(paper):
    """Display detailed information about a single paper."""
    # Paper metadata section
    st.header("Paper Details")
    col1, col2 = st.columns([2, 1])

    with col1:
        st.markdown(f"### {paper['title']}")
        st.markdown(f"**Authors:** {paper['authors']}")
        st.markdown(f"**Venue:** {paper['venue']}")
        if pd.notna(paper['volume']) or pd.notna(paper['issue']):
            st.markdown(f"**Volume/Issue:** {paper['volume']}/{paper['issue']}")

    with col2:
        st.markdown(f"**Year:** {paper['publication_year']}")
        st.markdown(f"**DOI:** {paper['doi']}")
        if pd.notna(paper['download_link']):
            st.markdown(f"[Download Paper]({paper['download_link']})")

    # Assessment section
    st.header("AI Assessment")

    # Metrics in columns
    col1, col2 = st.columns(2)
    with col1:
        st.metric(
            "Neurosymbolic Focus",
            "Yes" if paper['is_neurosymbolic'] else "No",
            help="Whether the paper primarily focuses on neurosymbolic AI"
        )
    with col2:
        st.metric(
            "Key Development",
            "Yes" if paper['key_development'] else "No",
            help="Whether the paper presents a significant development in the field"
        )

    # Contribution assessment
    st.subheader("Key Contribution")
    st.markdown(paper['contribution'])

    # Assessment metadata
    st.caption(f"Assessment performed on: {pd.to_datetime(paper['assessment_date']).strftime('%Y-%m-%d %H:%M')}")

def papers_view():
    """Main function for the papers view page."""
    st.title("Paper Assessments")

    # Load all paper details
    try:
        papers_df = load_paper_details()
    except sqlite3.OperationalError as e:
        st.error(f"Database error: {e}")
        st.stop()

    if papers_df.empty:
        st.warning("No paper assessments found in the database.")
        st.stop()

    # Sidebar filters
    st.sidebar.header("Filters")

    # Year filter
    years = sorted(papers_df['publication_year'].unique(), reverse=True)
    selected_years = st.sidebar.multiselect(
        "Publication Years",
        years,
        default=years
    )

    # Focus filter
    focus_filter = st.sidebar.radio(
        "Neurosymbolic Focus",
        ["All", "Yes", "No"]
    )

    # Development filter
    dev_filter = st.sidebar.radio(
        "Key Development",
        ["All", "Yes", "No"]
    )

    # Apply filters
    filtered_df = papers_df[
        papers_df['publication_year'].isin(selected_years)
    ]

    if focus_filter != "All":
        filtered_df = filtered_df[
            filtered_df['is_neurosymbolic'] == (focus_filter == "Yes")
            ]

    if dev_filter != "All":
        filtered_df = filtered_df[
            filtered_df['key_development'] == (dev_filter == "Yes")
            ]

    # Display stats
    st.sidebar.markdown("---")
    st.sidebar.markdown(f"**Showing {len(filtered_df)} of {len(papers_df)} papers**")

    # Paper selection
    selected_paper_title = st.selectbox(
        "Select a paper to view",
        filtered_df['title'].tolist(),
        format_func=lambda x: f"{x[:100]}..." if len(x) > 100 else x
    )

    # Display selected paper
    if selected_paper_title:
        paper = filtered_df[filtered_df['title'] == selected_paper_title].iloc[0]
        display_paper_details(paper)

        # Add export button for current paper
        if st.button("Export Paper Details"):
            paper_dict = paper.to_dict()
            export_df = pd.DataFrame([paper_dict])
            csv = export_df.to_csv(index=False)
            st.download_button(
                "Download CSV",
                csv,
                f"paper_{paper['doi'].replace('/', '_')}.csv",
                "text/csv",
                key='download-csv'
            )
