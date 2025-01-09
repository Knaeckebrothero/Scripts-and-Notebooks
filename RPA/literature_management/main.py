"""
This is the main script for the literature management application.
It provides a streamlit interface to import citations, process papers, and view the database.
"""
import streamlit as st
import pandas as pd
import sqlite3
import plotly.express as px
import json
import numpy as np
from dotenv import find_dotenv, load_dotenv
from process_papers import PdfProcessor
from pathlib import Path
from import_citations import CitationProcessor
from view_paper import papers_view
from datetime import datetime


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


class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


def display_papers_tab(papers_df):
    """Display the papers tab with basic statistics and visualizations."""
    st.title("Papers")

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
    st.plotly_chart(fig, use_container_width=True)

    # Top venues
    st.subheader("Top Publication Venues")
    venue_counts = papers_df['venue'].value_counts().head(10)
    fig = px.bar(x=venue_counts.values, y=venue_counts.index, orientation='h')
    fig.update_layout(xaxis_title="Number of Publications", yaxis_title="Venue")
    st.plotly_chart(fig, use_container_width=True)

    # Publication sources distribution
    st.subheader("Distribution by Publication Source")
    source_counts = papers_df['publication_source'].value_counts()
    fig = px.pie(values=source_counts.values, names=source_counts.index)
    st.plotly_chart(fig, use_container_width=True)

    # Interactive table
    st.subheader("Papers Database")
    st.dataframe(
        papers_df[[
            'title', 'authors', 'publication_year', 'venue', 
            'publication_type', 'publication_source', 'doi'
        ]],
        hide_index=True,
        use_container_width=True
    )


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


def create_filters(papers_df):
    """Create filter widgets that can be used across tabs"""
    filters = {}
    
    with st.sidebar:
        st.header("Filters")
        
        # Year filter
        years = sorted(papers_df['publication_year'].dropna().unique(), reverse=True)
        filters['years'] = st.multiselect(
            "Publication Years",
            years,
            default=years
        )
        
        # Paper type filter - handle NULL values
        paper_types = papers_df['paper_type'].dropna().unique().tolist()
        paper_types = ["All"] + sorted([pt for pt in paper_types if pd.notna(pt) and pt != ''])
        filters['paper_type'] = st.selectbox(
            "Paper Type",
            paper_types
        )
        
        # Focus filter
        filters['focus'] = st.radio(
            "Neurosymbolic Focus",
            ["All", "Yes", "No"]
        )
        
        # Development filter
        filters['development'] = st.radio(
            "Key Development",
            ["All", "Yes", "No"]
        )
    
    return filters


def apply_filters(df, filters):
    """Apply filters to a dataframe"""
    filtered_df = df.copy()
    
    # Handle year filter
    if filters['years']:
        filtered_df = filtered_df[filtered_df['publication_year'].isin(filters['years'])]
    
    # Handle paper type filter - consider NULL values
    if filters['paper_type'] != "All":
        filtered_df = filtered_df[
            (filtered_df['paper_type'] == filters['paper_type']) & 
            (filtered_df['paper_type'].notna())
        ]
    
    # Handle focus filter
    if filters['focus'] != "All":
        is_yes = filters['focus'] == "Yes"
        filtered_df = filtered_df[
            filtered_df['is_neurosymbolic'].fillna(False) == is_yes
        ]
    
    # Handle development filter
    if filters['development'] != "All":
        is_yes = filters['development'] == "Yes"
        filtered_df = filtered_df[
            filtered_df['is_development'].fillna(False) == is_yes
        ]
    
    return filtered_df


def display_assessments_tab(papers_df, filters):
    """Display the assessments tab with visualizations"""
    st.title("Paper Assessments")
    
    filtered_df = apply_filters(papers_df, filters)
    
    # Basic statistics with filtered data
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric(
            "Total Papers",
            len(filtered_df),
            delta=f"{len(filtered_df) - len(papers_df)} from total"
        )
    with col2:
        if 'is_neurosymbolic' in filtered_df.columns:
            neurosymbolic_count = len(filtered_df[filtered_df['is_neurosymbolic'] == True])
            st.metric("Neurosymbolic Papers", neurosymbolic_count)
        else:
            st.metric("Neurosymbolic Papers", "N/A")
    with col3:
        if 'is_development' in filtered_df.columns:
            dev_count = len(filtered_df[filtered_df['is_development'] == True])
            st.metric("Key Developments", dev_count)
        else:
            st.metric("Key Developments", "N/A")
    
    # Convert to regular Python types for JSON serialization
    filtered_data = filtered_df.to_dict(orient='records')
    filters_json = {k: [int(x) if isinstance(x, np.integer) else x for x in v] 
                   if isinstance(v, (list, np.ndarray)) else v 
                   for k, v in filters.items()}
    
    # Additional text-based insights
    st.subheader("Key Insights")
    
    with st.expander("Publication Trends"):
        yearly_counts = filtered_df['publication_year'].value_counts().sort_index()
        st.line_chart(yearly_counts)
        
        if len(yearly_counts) > 1:
            growth = ((yearly_counts.iloc[-1] / yearly_counts.iloc[0]) - 1) * 100
            st.write(f"Publication growth rate: {growth:.1f}%")
    
    with st.expander("Development Analysis"):
        if 'is_development' in filtered_df.columns and 'paper_type' in filtered_df.columns:
            # Create a mask for development papers
            dev_mask = filtered_df['is_development'].fillna(False) == True
            if dev_mask.any():  # Check if there are any development papers
                dev_by_type = filtered_df[dev_mask]['paper_type'].value_counts()
                st.write("Distribution of key developments by paper type:")
                if not dev_by_type.empty:
                    st.bar_chart(dev_by_type)
                else:
                    st.write("No development papers found in the current selection.")
            else:
                st.write("No development papers found in the current selection.")
        else:
            st.write("Development information not available.")
    
    # Paper Types Distribution
    with st.expander("Paper Types Distribution"):
        if 'paper_type' in filtered_df.columns:
            type_counts = filtered_df['paper_type'].value_counts()
            if not type_counts.empty:
                fig = px.pie(
                    values=type_counts.values,
                    names=type_counts.index,
                    title="Distribution of Paper Types"
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.write("No paper type information available.")
        else:
            st.write("Paper type information not available.")
    
    # Venue Analysis
    with st.expander("Venue Analysis"):
        venue_counts = filtered_df['venue'].value_counts().head(10)
        if not venue_counts.empty:
            fig = px.bar(
                x=venue_counts.values,
                y=venue_counts.index,
                orientation='h',
                title="Top 10 Publication Venues"
            )
            fig.update_layout(yaxis_title="Venue", xaxis_title="Number of Papers")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.write("No venue information available.")


def main():
    load_dotenv(find_dotenv())
    st.set_page_config(
        page_title="Literature Dashboard",
        initial_sidebar_state="expanded",
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

    try:
        papers_df = load_data("""
            SELECT p.*, a.*
            FROM papers p
            LEFT JOIN paper_assessments a ON p.id = a.paper_id
        """)
    except sqlite3.OperationalError:
        st.error("Please import citations and process papers first!")
        st.stop()

    # Create filters that will be used across tabs
    filters = create_filters(papers_df)

    # Create tabs
    papers_tab, assessments_tab, papers_view_tab, add_paper_tab = st.tabs(
        ["Papers", "Aggregated Assessments", "Paper Assessments", "Add Paper"])

    with papers_tab:
        filtered_df = apply_filters(papers_df, filters)
        display_papers_tab(filtered_df)

    with assessments_tab:
        display_assessments_tab(papers_df, filters)

    with papers_view_tab:
        papers_view(filters)

    with add_paper_tab:
        add_paper()


if __name__ == "__main__":
    main()
