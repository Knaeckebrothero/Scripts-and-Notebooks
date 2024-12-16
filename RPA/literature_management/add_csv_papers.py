import bibtexparser
import sqlite3
from pathlib import Path
import csv
import pandas as pd


def create_database():
    """Create the SQLite database and table"""
    conn = sqlite3.connect('citations.db')
    cursor = conn.cursor()

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

def process_csv_file(file_path, conn, processed_dois):
    """Process IEEE CSV export file"""
    stats = {'inserted': 0, 'duplicate': 0, 'no_doi': 0}

    # Read CSV file
    df = pd.read_csv(file_path)

    for _, row in df.iterrows():
        doi = str(row['DOI']).strip()

        if not doi or pd.isna(doi):
            stats['no_doi'] += 1
            continue

        # Check for duplicates
        if doi in processed_dois:
            stats['duplicate'] += 1
            continue

        cursor = conn.cursor()
        cursor.execute('SELECT doi FROM citations WHERE doi = ?', (doi,))
        if cursor.fetchone() is not None:
            stats['duplicate'] += 1
            continue

        # Process the entry
        title = str(row['Document Title']).strip()
        year = int(row['Publication Year']) if pd.notna(row['Publication Year']) else 0
        authors = str(row['Authors']).strip()
        venue = str(row['Publication Title']).strip()
        volume = str(row['Volume']).strip() if pd.notna(row['Volume']) else ''
        issue = str(row['Issue']).strip() if pd.notna(row['Issue']) else ''
        download_link = str(row['PDF Link']).strip() if pd.notna(row['PDF Link']) else ''

        # Insert into database
        cursor.execute('''
        INSERT INTO citations 
        (doi, title, publication_year, authors, venue, volume, issue, download_link)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (doi, title, year, authors, venue, volume, issue, download_link))

        conn.commit()
        processed_dois.add(doi)
        stats['inserted'] += 1

    return stats

def insert_citation(conn, entry, processed_dois):
    """Insert a citation entry into the database if it's not a duplicate"""
    cursor = conn.cursor()

    doi = entry.get('doi', '').strip()
    if not doi:
        return 'no_doi'

    if doi in processed_dois:
        return 'duplicate'

    cursor.execute('SELECT doi FROM citations WHERE doi = ?', (doi,))
    if cursor.fetchone() is not None:
        return 'duplicate'

    title = entry.get('title', '').replace('{', '').replace('}', '')
    year = int(entry.get('year', 0))
    authors = entry.get('author', '')
    venue = entry.get('journal', entry.get('booktitle', ''))
    volume = entry.get('volume', '')
    issue = entry.get('number', '')
    download_link = entry.get('url', '')

    cursor.execute('''
    INSERT INTO citations 
    (doi, title, publication_year, authors, venue, volume, issue, download_link)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (doi, title, year, authors, venue, volume, issue, download_link))

    conn.commit()
    processed_dois.add(doi)
    return 'inserted'


# File paths
bib_files = [] # ["acm.bib", "ScienceDirect_citations_1734257911006.bib"]
csv_files = ["export2024.12.15-05.09.03.csv"]

# Create database
conn = create_database()

# Keep track of processed DOIs across all files
processed_dois = set()

try:
    # Process BibTeX files
    for bib_file in bib_files:
        print(f"\nProcessing {bib_file}:")
        try:
            bib_database = parse_bib_file(bib_file)
            stats = {'inserted': 0, 'duplicate': 0, 'no_doi': 0}

            for entry in bib_database.entries:
                result = insert_citation(conn, entry, processed_dois)
                stats[result] += 1

            print(f"Entries processed: {len(bib_database.entries)}")
            print(f"New entries inserted: {stats['inserted']}")
            print(f"Duplicates skipped: {stats['duplicate']}")
            print(f"Entries without DOI: {stats['no_doi']}")

        except Exception as e:
            print(f"Error processing {bib_file}: {e}")

    # Process CSV files
    for csv_file in csv_files:
        print(f"\nProcessing {csv_file}:")
        try:
            stats = process_csv_file(csv_file, conn, processed_dois)

            print(f"New entries inserted: {stats['inserted']}")
            print(f"Duplicates skipped: {stats['duplicate']}")
            print(f"Entries without DOI: {stats['no_doi']}")

        except Exception as e:
            print(f"Error processing {csv_file}: {e}")

finally:
    conn.close()
