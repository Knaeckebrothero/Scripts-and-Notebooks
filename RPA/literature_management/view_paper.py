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
        p.id,
        p.doi,
        p.title,
        p.authors,
        p.publication_year,
        p.venue,
        p.volume,
        p.publication_type,
        p.publication_source,
        p.processed,
        p.file_path,
        a.is_neurosymbolic,
        a.is_development,
        a.paper_type,
        a.summary,
        a.takeaways,
        a.assessment_date
    FROM papers p
    LEFT JOIN paper_assessments a ON p.id = a.paper_id
    WHERE p.processed = 1
    ORDER BY a.assessment_date DESC, p.publication_year DESC
    """
    return pd.read_sql_query(query, conn)


def display_paper_details(paper):
    """Display detailed information about a single paper."""
    st.header("Paper Details")

    # Basic paper information
    col1, col2 = st.columns([2, 1])
    with col1:
        st.markdown(f"### {paper['title']}")
        st.markdown(f"**Authors:** {paper['authors']}")
        st.markdown(f"**Venue:** {paper['venue']}")
        if pd.notna(paper['volume']):
            st.markdown(f"**Volume:** {paper['volume']}")

    with col2:
        st.markdown(f"**Year:** {paper['publication_year']}")
        st.markdown(f"**DOI:** {paper['doi']}")
        st.markdown(f"**Type:** {paper['publication_type']}")
        st.markdown(f"**Source:** {paper['publication_source']}")

    # Assessment metrics
    st.header("Assessment")
    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric(
            "Neurosymbolic Focus",
            "Yes" if paper['is_neurosymbolic'] else "No",
            help="Whether the paper primarily focuses on neurosymbolic AI"
        )
    with col2:
        st.metric(
            "Key Development",
            "Yes" if paper['is_development'] else "No",
            help="Whether the paper presents a significant development in the field"
        )
    with col3:
        st.metric(
            "Paper Type",
            paper['paper_type'],
            help="The primary type/category of the paper"
        )

    # Detailed assessment
    if pd.notna(paper['summary']):
        st.subheader("Summary")
        st.markdown(paper['summary'])

    if pd.notna(paper['takeaways']):
        st.subheader("Key Takeaways")
        st.markdown(paper['takeaways'])

    # Assessment metadata
    st.caption(f"Assessment performed on: {pd.to_datetime(paper['assessment_date']).strftime('%Y-%m-%d %H:%M')}")

    # PDF link if available
    if pd.notna(paper['file_path']):
        pdf_path = Path(paper['file_path'])
        if pdf_path.exists():
            st.markdown(f"[View PDF]({pdf_path})")


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
        st.warning("No processed paper assessments found in the database.")
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

    # Paper type filter
    paper_types = ["All"] + sorted(papers_df['paper_type'].unique().tolist())
    selected_type = st.sidebar.selectbox(
        "Paper Type",
        paper_types
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

    if selected_type != "All":
        filtered_df = filtered_df[
            filtered_df['paper_type'] == selected_type
            ]

    if focus_filter != "All":
        filtered_df = filtered_df[
            filtered_df['is_neurosymbolic'] == (focus_filter == "Yes")
            ]

    if dev_filter != "All":
        filtered_df = filtered_df[
            filtered_df['is_development'] == (dev_filter == "Yes")
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

        # Export functionality
        if st.button("Export Paper Details"):
            paper_dict = paper.to_dict()
            export_df = pd.DataFrame([paper_dict])
            csv = export_df.to_csv(index=False)
            st.download_button(
                "Download CSV",
                csv,
                f"paper_{paper['id']}.csv",
                "text/csv",
                key='download-csv'
            )
