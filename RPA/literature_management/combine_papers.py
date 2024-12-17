"""
This script should combine the papers from the different sources into one file.

Attributes: DOI, Title, Publication year, Author names, Journal/conference name,
 Volume/issue numbers, Downloadlink
"""
import bibtexparser
import sqlite3
from pathlib import Path

def create_database():
    """Create the SQLite database and table"""
    conn = sqlite3.connect('old_citations.db')
    cursor = conn.cursor()

    # Create table with all required fields
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS citations (
        doi TEXT PRIMARY KEY,
        title TEXT,
        publication_year INTEGER,
        authors TEXT,
        venue TEXT,
        volume TEXT,
        issue TEXT,
        download_link TEXT
    )
    ''')

    conn.commit()
    return conn

def parse_bib_file(file_path):
    """Parse BibTeX file and return the database"""
    with open(file_path, 'r', encoding='utf-8') as bibtex_file:
        parser = bibtexparser.bparser.BibTexParser(common_strings=True)
        bib_database = bibtexparser.load(bibtex_file, parser)
    return bib_database

def insert_citation(conn, entry, processed_dois):
    """Insert a citation entry into the database if it's not a duplicate"""
    cursor = conn.cursor()

    # Extract and process the fields
    doi = entry.get('doi', '').strip()
    if not doi:  # Skip entries without DOI as it's required
        return 'no_doi'

    # Check if we've already processed this DOI in this session
    if doi in processed_dois:
        return 'duplicate'

    # Check if DOI already exists in database
    cursor.execute('SELECT doi FROM citations WHERE doi = ?', (doi,))
    if cursor.fetchone() is not None:
        return 'duplicate'

    title = entry.get('title', '').replace('{', '').replace('}', '')
    year = int(entry.get('year', 0))
    authors = entry.get('author', '')

    # Determine venue (journal or conference)
    venue = entry.get('journal', entry.get('booktitle', ''))

    volume = entry.get('volume', '')
    issue = entry.get('number', '')  # 'number' field is commonly used for issue
    download_link = entry.get('url', '')

    # Insert into database
    cursor.execute('''
    INSERT INTO citations 
    (doi, title, publication_year, authors, venue, volume, issue, download_link)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (doi, title, year, authors, venue, volume, issue, download_link))

    conn.commit()
    processed_dois.add(doi)
    return 'inserted'


# File path
bib_files = ["acm.bib", "ScienceDirect_citations_1734257911006.bib"]

# Create database
conn = create_database()

# Keep track of processed DOIs across all files
processed_dois = set()

try:
    for bib_file in bib_files:
        print(f"\nProcessing {bib_file}:")
        try:
            # Parse BibTeX file
            bib_database = parse_bib_file(bib_file)

            # Track statistics for this file
            stats = {'inserted': 0, 'duplicate': 0, 'no_doi': 0}

            # Process each entry
            for entry in bib_database.entries:
                result = insert_citation(conn, entry, processed_dois)
                stats[result] += 1

            print(f"Entries processed: {len(bib_database.entries)}")
            print(f"New entries inserted: {stats['inserted']}")
            print(f"Duplicates skipped: {stats['duplicate']}")
            print(f"Entries without DOI: {stats['no_doi']}")

        except Exception as e:
            print(f"Error processing {bib_file}: {e}")

finally:
    conn.close()
