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
    conn = sqlite3.connect('citations.db')
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

def insert_citation(conn, entry):
    """Insert a citation entry into the database"""
    cursor = conn.cursor()

    # Extract and process the fields
    doi = entry.get('doi', '')
    if not doi:  # Skip entries without DOI as it's required
        return

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
    INSERT OR REPLACE INTO citations 
    (doi, title, publication_year, authors, venue, volume, issue, download_link)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (doi, title, year, authors, venue, volume, issue, download_link))

    conn.commit()


# File path
bib_file = "acm.bib"  # Replace with your file path

# Create database
conn = create_database()

for
try:
    # Parse BibTeX file
    bib_database = parse_bib_file(bib_file)

    # Process each entry
    for entry in bib_database.entries:
        insert_citation(conn, entry)

    print(f"Successfully processed {len(bib_database.entries)} entries")

except Exception as e:
    print(f"An error occurred: {e}")

finally:
    conn.close()
