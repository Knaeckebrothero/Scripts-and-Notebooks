"""
This is the main script for the literature management application.
It provides a streamlit interface to import citations, process papers, and view the database.
"""
import streamlit as st
import pandas as pd
import sqlite3
import plotly.express as px
from dotenv import find_dotenv, load_dotenv
from process_papers import PdfProcessor
from pathlib import Path
from import_citations import CitationProcessor
from view_paper import papers_view


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


def setup_database(db_path: str = 'literature.db'):
    """
    Function to create the SQLite database, set up the connection
    and create citation and assessment tables if needed.
    """
    connector = sqlite3.connect(db_path)
    cursor = connector.cursor()

    # Main table for papers
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS papers (
        id INTEGER PRIMARY KEY AUTOINCREMENT, 
        doi TEXT UNIQUE NOT NULL,
        title TEXT,
        publication_year INTEGER,
        authors TEXT,
        venue TEXT,
        volume TEXT,
        publication_type TEXT, 
        publication_source TEXT,
        processed BOOLEAN DEFAULT 0, 
        file_path TEXT DEFAULT NULL 
    )
    """)

    # Table for paper assessments
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS paper_assessments (
        paper_id INTEGER PRIMARY KEY,
        is_neurosymbolic BOOLEAN,
        is_development BOOLEAN,  
        paper_type TEXT,
        summary TEXT,
        takeaways TEXT, 
        assessment_date TIMESTAMP,
        FOREIGN KEY (paper_id) REFERENCES papers (id)
    )
    """)

    # Table for keywords
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS keywords (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        keyword TEXT UNIQUE
    )
    """)

    # Relationship table for keywords and papers
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS rel_keywords_papers (
        paper_id INTEGER,
        keyword_id INTEGER,
        FOREIGN KEY (paper_id) REFERENCES papers (id),
        FOREIGN KEY (keyword_id) REFERENCES keywords (id)
    )
    """)

    connector.commit()


def import_citations():
    # Configure base path for search results
    search_dir = Path('search_results')

    # Configure files with relative paths
    file_config = {
        'bibtex': [
            search_dir / 'acm.bib',
            search_dir / 'ScienceDirect_1.bib',
            search_dir / 'ScienceDirect_2.bib',
            search_dir / 'wiley_1.bib',
            search_dir / 'wiley_2.bib'
        ],
        'ieee': [search_dir / 'ieee.csv'],
        'springer': [
            search_dir / 'SpringerLink_1.csv',
            search_dir / 'SpringerLink_2.csv'
        ],
    }

    processor = CitationProcessor()
    processor.process_files(file_config)


def process_papers():
    processor = PdfProcessor()
    try:
        processor.process_directory('papers')
    finally:
        processor.close()


def add_paper():
    st.title("Add Paper")

    # Form for paper details
    with st.form("paper_form"):
        doi = st.text_input("DOI*", help="Digital Object Identifier (required)")
        title = st.text_input("Title*", help="Paper title (required)")
        year = st.number_input("Publication Year*", min_value=1900, max_value=2100, value=2024)
        authors = st.text_input("Authors*", help="Comma-separated list of authors")
        venue = st.text_input("Venue", help="Journal or conference name")
        volume = st.text_input("Volume")
        publication_type = st.text_input("Publication Type")
        publication_source = st.text_input("Publication Source", help="Where this paper was found")

        submitted = st.form_submit_button("Add Paper")

        if submitted:
            if not all([doi, title, authors]):
                st.error("Please fill in all required fields (marked with *)")
                return

            conn = get_connection()
            cursor = conn.cursor()

            try:
                cursor.execute("""
                    INSERT INTO papers 
                    (doi, title, publication_year, authors, venue, volume, publication_type, publication_source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (doi, title, year, authors, venue, volume, publication_type, publication_source))

                conn.commit()
                st.success("Paper added successfully!")
            except sqlite3.IntegrityError:
                st.error("A paper with this DOI already exists in the database")
            except Exception as e:
                st.error(f"Error adding paper: {str(e)}")
            finally:
                conn.close()


def main():
    load_dotenv(find_dotenv())
    st.set_page_config(
        page_title="Literature Dashboard",
        initial_sidebar_state="collapsed",
        layout="wide"
    )

    # Initialize the db
    setup_database()

    with st.sidebar:
        if st.button("Import Citations"):
            with st.spinner("Importing citations..."):
                import_citations()

        if st.button("Process Papers"):
            with st.spinner("Processing papers..."):
                process_papers()

    papers_tab, assessments_tab, papers_view_tab, add_paper_tab = st.tabs(
        ["Papers", "Aggregated Assessments", "Paper Assessments", "Add Paper"])

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

    # Papers tab that displays basic statistics about the papers in the database.
    with papers_tab:
        st.title("Papers")

        try:
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


    # Assessment tab that displays aggregated statistics about the papers in the database.
    with assessments_tab:
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

    with papers_view_tab:
        papers_view()

    with add_paper_tab:
        add_paper()


if __name__ == "__main__":
    main()
