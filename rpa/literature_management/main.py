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
import plotly.graph_objects as go
import plotly.express as px
import networkx as nx
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


@st.cache_data
def load_keyword_data():
    """Load keyword data with relationships and paper metadata"""
    conn = get_connection()
    query = """
    SELECT 
        k.keyword,
        k.id as keyword_id,
        p.publication_year,
        p.id as paper_id,
        p.publication_source,
        pa.paper_type,
        pa.is_neurosymbolic
    FROM keywords k
    JOIN rel_keywords_papers r ON k.id = r.keyword_id
    JOIN papers p ON r.paper_id = p.id
    LEFT JOIN paper_assessments pa ON p.id = pa.paper_id
    """
    return pd.read_sql_query(query, conn)


@st.cache_data
def process_keyword_network(df, max_nodes=30):
    """Process keyword data for network visualization with size limits"""
    # Get top keywords first to limit network size
    top_keywords = df['keyword'].value_counts().head(max_nodes).index
    df_filtered = df[df['keyword'].isin(top_keywords)]
    
    # Create co-occurrence matrix
    paper_keyword_groups = df_filtered.groupby('paper_id')['keyword'].agg(list)
    keyword_pairs = []
    
    for keywords in paper_keyword_groups:
        if len(keywords) > 1:  # Only process if there are at least 2 keywords
            for i in range(len(keywords)):
                for j in range(i + 1, len(keywords)):
                    keyword_pairs.append(tuple(sorted([keywords[i], keywords[j]])))
    
    # Count co-occurrences
    co_occurrences = pd.DataFrame(
        keyword_pairs, 
        columns=['source', 'target']
    ).value_counts().reset_index()
    co_occurrences.columns = ['source', 'target', 'weight']
    
    return co_occurrences


@st.cache_data
def process_temporal_evolution(df, top_n=10):
    """Process keyword data for temporal evolution"""
    top_keywords = df['keyword'].value_counts().head(top_n).index
    mask = df['keyword'].isin(top_keywords)
    return df[mask].groupby(['publication_year', 'keyword']).size().reset_index(name='count')


@st.cache_data
def process_keyword_frequency(df, top_n=20):
    """Process keyword frequency data"""
    return df['keyword'].value_counts().head(top_n).reset_index()


def display_keywords_tab(papers_df, filters):
    """Display the keywords analysis tab"""
    st.title("Keyword Analysis")
    
    with st.spinner("Loading keyword data..."):
        keyword_data = load_keyword_data()
        filtered_df = apply_filters(papers_df, filters)
        filtered_keywords = keyword_data[
            keyword_data['paper_id'].isin(filtered_df['id'])
        ]
    
    if filtered_keywords.empty:
        st.warning("No keyword data available for the current selection.")
        return
    
    # Add controls for visualization parameters
    with st.expander("Visualization Settings"):
        col1, col2 = st.columns(2)
        with col1:
            max_nodes = st.slider("Max number of keywords in network", 5, 50, 30)
            top_n_temporal = st.slider("Number of keywords in temporal view", 5, 20, 10)
        with col2:
            top_n_freq = st.slider("Number of keywords in frequency view", 5, 50, 20)
            min_cooccurrence = st.slider("Minimum co-occurrence strength", 1, 10, 2)
    
    # Create two columns for the top section
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("Keyword Co-occurrence Network")
        with st.spinner("Generating network visualization..."):
            network_data = process_keyword_network(filtered_keywords, max_nodes)
            network_data = network_data[network_data['weight'] >= min_cooccurrence]
            
            if network_data.empty:
                st.warning("Not enough co-occurring keywords found with current settings.")
                return
            
            import networkx as nx
            G = nx.from_pandas_edgelist(
                network_data, 
                'source', 
                'target', 
                edge_attr='weight'
            )
            
            if len(G.nodes()) > 0:
                pos = nx.spring_layout(G, k=1/np.sqrt(len(G.nodes())), iterations=50)
                
                # Create separate traces for each edge with weight-dependent width
                edge_traces = []
                for edge in G.edges(data=True):
                    x0, y0 = pos[edge[0]]
                    x1, y1 = pos[edge[1]]
                    weight = edge[2].get('weight', 1)
                    
                    edge_trace = go.Scatter(
                        x=[x0, x1],
                        y=[y0, y1],
                        line=dict(
                            width=weight/network_data['weight'].max() * 5,
                            color='#888'
                        ),
                        hoverinfo='text',
                        text=f"Weight: {weight}",
                        mode='lines'
                    )
                    edge_traces.append(edge_trace)

                # Create nodes trace
                node_x = [pos[node][0] for node in G.nodes()]
                node_y = [pos[node][1] for node in G.nodes()]
                node_text = list(G.nodes())
                
                node_trace = go.Scatter(
                    x=node_x,
                    y=node_y,
                    mode='markers+text',
                    hoverinfo='text',
                    text=node_text,
                    textposition="top center",
                    marker=dict(
                        size=10,
                        color='lightblue',
                        line_width=2
                    )
                )

                # Combine all traces
                fig = go.Figure(
                    data=edge_traces + [node_trace],
                    layout=go.Layout(
                        showlegend=False,
                        hovermode='closest',
                        margin=dict(b=0,l=0,r=0,t=0),
                        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
                        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False)
                    )
                )
                
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.warning("Not enough connected keywords to create network visualization")

    with col2:
        st.subheader("Keyword Frequency")
        with st.spinner("Generating frequency visualization..."):
            freq_data = process_keyword_frequency(filtered_keywords, top_n_freq)
            fig = px.bar(
                freq_data, 
                x='count', 
                y='keyword',
                orientation='h',
                title='Most Common Keywords'
            )
            fig.update_layout(yaxis={'categoryorder':'total ascending'})
            st.plotly_chart(fig, use_container_width=True)
    
    st.subheader("Temporal Keyword Evolution")
    with st.spinner("Generating temporal visualization..."):
        temporal_data = process_temporal_evolution(filtered_keywords, top_n_temporal)
        fig = px.line(
            temporal_data,
            x='publication_year',
            y='count',
            color='keyword',
            title='Keyword Usage Over Time'
        )
        st.plotly_chart(fig, use_container_width=True)
    
    # Add export functionality
    st.subheader("Export Data")
    col1, col2, col3 = st.columns(3)
    
    with col1:
        csv = network_data.to_csv(index=False).encode('utf-8')
        st.download_button(
            "Download Co-occurrence Data",
            csv,
            "keyword_network.csv",
            "text/csv",
            key='download-network'
        )
        
    with col2:
        csv = temporal_data.to_csv(index=False).encode('utf-8')
        st.download_button(
            "Download Temporal Data",
            csv,
            "keyword_temporal.csv",
            "text/csv",
            key='download-temporal'
        )
        
    with col3:
        csv = freq_data.to_csv(index=False).encode('utf-8')
        st.download_button(
            "Download Frequency Data",
            csv,
            "keyword_frequency.csv",
            "text/csv",
            key='download-frequency'
        )


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
    papers_tab, assessments_tab, keywords_tab, papers_view_tab, add_paper_tab = st.tabs([
        "Papers", "Aggregated Assessments", "Keyword Analysis", "Paper Assessments", "Add Paper"
    ])

    with papers_tab:
        filtered_df = apply_filters(papers_df, filters)
        display_papers_tab(filtered_df)

    with assessments_tab:
        display_assessments_tab(papers_df, filters)

    with papers_view_tab:
        papers_view(filters)

    with add_paper_tab:
        add_paper()

    with keywords_tab:
        display_keywords_tab(papers_df, filters)


if __name__ == "__main__":
    main()
