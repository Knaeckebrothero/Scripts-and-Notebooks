"""
This is the main script for the literature management application.
It provides a streamlit interface to import citations, process papers, and view the database.
"""
import streamlit as st
import pandas as pd
import sqlite3
import plotly.express as px
from pathlib import Path
from process_papers import process_papers
from import_paper_citations import import_citations


@st.cache_resource
def get_connection():
    return sqlite3.connect('literature.db', check_same_thread=False)


def load_data(query):
    conn = get_connection()
    df = pd.read_sql_query(query, conn)

    # Convert SQLite integer boolean columns to Python boolean
    bool_columns = ['is_neurosymbolic', 'key_development']
    for col in bool_columns:
        if col in df.columns:
            df[col] = df[col].astype(bool)

    return df


if __name__ == "__main__":
    st.set_page_config(
        page_title="Literature Dashboard",
        initial_sidebar_state="collapsed",
        layout="wide"
    )

    with st.sidebar:
        if st.button("Import Citations"):
            with st.spinner("Importing citations..."):
                import_citations()

        if st.button("Process Papers"):
            with st.spinner("Processing papers..."):
                process_papers()

    papers_tab, assessments_tab = st.tabs(["Papers", "Assessments"])

    # Add CSS for better styling
    st.markdown("""
        <style>
            .stMetric {
                padding: 10px;
                border-radius: 5px;
            }
            .stMetric > div {
                display: flex;
                justify-content: center;
            }
        </style>
        """, unsafe_allow_html=True)

    with papers_tab:
        st.title("Papers")

        try:
            if st.checkbox("Include without doi"):
                papers_df = load_data("""SELECT * FROM papers UNION SELECT * FROM papers_no_doi""")
            else:
                papers_df = load_data("SELECT * FROM papers")
        except sqlite3.OperationalError:
            st.error("Please import citations first!")

            if st.button("Import Citations"):
                with st.spinner("Importing citations..."):
                    import_citations()

            st.stop()

        # Basic statistics
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total Papers", len(papers_df))
        with col2:
            st.metric("Unique Venues", papers_df['venue'].nunique())
        with col3:
            st.metric("Year Range", f"{papers_df['publication_year'].min()}-{papers_df['publication_year'].max()}")

        # Publications per year
        st.subheader("Publications per Year")
        year_counts = papers_df['publication_year'].value_counts().sort_index()
        fig = px.bar(x=year_counts.index, y=year_counts.values)
        fig.update_layout(xaxis_title="Year", yaxis_title="Number of Publications")
        st.plotly_chart(fig)

        # Top venues
        st.subheader("Top Publication Venues")
        venue_counts = papers_df['venue'].value_counts().head(10)
        fig = px.bar(x=venue_counts.values, y=venue_counts.index, orientation='h')
        fig.update_layout(xaxis_title="Number of Publications", yaxis_title="Venue")
        st.plotly_chart(fig)

        # Interactive table
        st.subheader("Papers Database")
        st.dataframe(papers_df)

    with assessments_tab:  # Paper Assessments
        st.title("Paper Assessments")

        try:
            assessments_df = load_data("""
                SELECT a.*, p.title, p.authors, p.publication_year
                FROM paper_assessments a
                LEFT JOIN papers p ON a.paper_id = p.doi
            """)
        except sqlite3.OperationalError:
            st.error("Please process papers first!")

            if st.button("Process Papers"):
                with st.spinner("Processing papers..."):
                    process_papers()

            st.stop()

        # Basic statistics
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total Assessments", len(assessments_df))
        with col2:
            if 'is_neurosymbolic' in assessments_df.columns:
                neurosymbolic_count = len(assessments_df[assessments_df['is_neurosymbolic'] == 1])
                st.metric("Neurosymbolic Papers", neurosymbolic_count)
            else:
                st.metric("Neurosymbolic Papers", "N/A")
        with col3:
            if 'key_development' in assessments_df.columns:
                key_dev_count = len(assessments_df[assessments_df['key_development'] == 1])
                st.metric("Key Developments", key_dev_count)
            else:
                st.metric("Key Developments", "N/A")

        # Quality score distribution
        if 'quality_score' in assessments_df.columns:
            st.subheader("Quality Score Distribution")
            score_counts = assessments_df['quality_score'].value_counts().sort_index()
            if not score_counts.empty:
                fig = px.bar(x=score_counts.index, y=score_counts.values)
                fig.update_layout(xaxis_title="Quality Score", yaxis_title="Number of Papers")
                st.plotly_chart(fig)
            else:
                st.info("No quality score data available")

        # Development types
        if 'development_type' in assessments_df.columns:
            st.subheader("Development Types")
            dev_type_counts = assessments_df['development_type'].value_counts()
            if not dev_type_counts.empty:
                fig = px.pie(values=dev_type_counts.values, names=dev_type_counts.index)
                st.plotly_chart(fig)
            else:
                st.info("No development type data available")

        # Interactive table
        st.subheader("Assessment Details")
        st.dataframe(assessments_df)
