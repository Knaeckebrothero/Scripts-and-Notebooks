"""
Script to process citation files from various sources and consolidate them into a SQLite database.
Supports BibTeX, IEEE CSV, Springer CSV, DBLP CSV, and ProQuest CSV formats.
"""

import bibtexparser
import sqlite3
import pandas as pd
from pathlib import Path
from typing import Dict, Set

class CitationProcessor:
    def __init__(self, db_name: str = 'citations.db'):
        """Initialize the citation processor with database connection"""
        self.db_name = db_name
        self.processed_dois = set()
        self.conn = None
        self.setup_database()

    def setup_database(self):
        """Create the SQLite database and required table"""
        self.conn = sqlite3.connect(self.db_name)
        cursor = self.conn.cursor()

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

        self.conn.commit()

    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()

    def _insert_citation(self, doi: str, title: str, year: int, authors: str,
                         venue: str, volume: str, issue: str, download_link: str) -> str:
        """Insert a citation into the database"""
        if not doi:
            return 'no_doi'

        if doi in self.processed_dois:
            return 'duplicate'

        cursor = self.conn.cursor()
        cursor.execute('SELECT doi FROM citations WHERE doi = ?', (doi,))
        if cursor.fetchone() is not None:
            return 'duplicate'

        cursor.execute('''
        INSERT INTO citations 
        (doi, title, publication_year, authors, venue, volume, issue, download_link)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (doi, title, year, authors, venue, volume, issue, download_link))

        self.conn.commit()
        self.processed_dois.add(doi)
        return 'inserted'

    def _print_stats(self, stats: Dict[str, int]):
        """Print statistics for file processing"""
        print(f"New entries inserted: {stats['inserted']}")
        print(f"Duplicates skipped: {stats['duplicate']}")
        print(f"Entries without DOI: {stats['no_doi']}")

    def _process_bibtex(self, file_path: str):
        """Process BibTeX file"""
        with open(file_path, 'r', encoding='utf-8') as bibtex_file:
            parser = bibtexparser.bparser.BibTexParser(common_strings=True)
            bib_database = bibtexparser.load(bibtex_file, parser)

        stats = {'inserted': 0, 'duplicate': 0, 'no_doi': 0}

        for entry in bib_database.entries:
            doi = entry.get('doi', '').strip()
            title = entry.get('title', '').replace('{', '').replace('}', '')
            year = int(entry.get('year', 0))
            authors = entry.get('author', '')
            venue = entry.get('journal', entry.get('booktitle', ''))
            volume = entry.get('volume', '')
            issue = entry.get('number', '')
            download_link = entry.get('url', '')

            result = self._insert_citation(doi, title, year, authors, venue,
                                           volume, issue, download_link)
            stats[result] += 1

        self._print_stats(stats)

    def _process_ieee_csv(self, file_path: str):
        """Process IEEE CSV file"""
        stats = {'inserted': 0, 'duplicate': 0, 'no_doi': 0}
        df = pd.read_csv(file_path)

        for _, row in df.iterrows():
            doi = str(row['DOI']).strip() if pd.notna(row['DOI']) else ''
            if not doi:
                stats['no_doi'] += 1
                continue

            title = str(row['Document Title']).strip() if pd.notna(row['Document Title']) else ''
            year = int(row['Publication Year']) if pd.notna(row['Publication Year']) else 0
            authors = str(row['Authors']).strip() if pd.notna(row['Authors']) else ''
            venue = str(row['Publication Title']).strip() if pd.notna(row['Publication Title']) else ''
            volume = str(row['Volume']).strip() if pd.notna(row['Volume']) else ''
            issue = str(row['Issue']).strip() if pd.notna(row['Issue']) else ''
            download_link = str(row['PDF Link']).strip() if pd.notna(row['PDF Link']) else ''

            result = self._insert_citation(doi, title, year, authors, venue,
                                           volume, issue, download_link)
            stats[result] += 1

        self._print_stats(stats)

    def _process_springer_csv(self, file_path: str):
        """Process Springer CSV file"""
        stats = {'inserted': 0, 'duplicate': 0, 'no_doi': 0}
        df = pd.read_csv(file_path)

        for _, row in df.iterrows():
            doi = str(row['Item DOI']).strip() if pd.notna(row['Item DOI']) else ''
            if not doi:
                stats['no_doi'] += 1
                continue

            title = str(row['Item Title']).strip() if pd.notna(row['Item Title']) else ''
            year = int(row['Publication Year']) if pd.notna(row['Publication Year']) else 0
            authors = str(row['Authors']).strip() if pd.notna(row['Authors']) else ''

            # Handle different venue types
            if pd.notna(row.get('Content Type')) and row['Content Type'] == 'Book':
                venue = str(row['Book Series Title']).strip() if pd.notna(row['Book Series Title']) else ''
            else:
                venue = str(row['Publication Title']).strip() if pd.notna(row['Publication Title']) else ''

            volume = str(row['Journal Volume']).strip() if pd.notna(row.get('Journal Volume')) else ''
            issue = str(row['Journal Issue']).strip() if pd.notna(row.get('Journal Issue')) else ''
            download_link = str(row['URL']).strip() if pd.notna(row['URL']) else ''

            result = self._insert_citation(doi, title, year, authors, venue,
                                           volume, issue, download_link)
            stats[result] += 1

        self._print_stats(stats)

    def _process_dblp_csv(self, file_path: str):
        """Process DBLP CSV file"""
        stats = {'inserted': 0, 'duplicate': 0, 'no_doi': 0}
        df = pd.read_csv(file_path)

        for _, row in df.iterrows():
            doi = str(row['doi']).strip() if pd.notna(row.get('doi')) else ''
            if not doi:
                stats['no_doi'] += 1
                continue

            title = str(row['title']).strip() if pd.notna(row.get('title')) else ''
            year = int(row['year']) if pd.notna(row.get('year')) else 0
            authors = str(row['authors']).strip() if pd.notna(row.get('authors')) else ''
            venue = str(row['venueName']).strip() if pd.notna(row.get('venueName')) else ''
            volume = str(row['volume']).strip() if pd.notna(row.get('volume')) else ''
            issue = str(row['issue']).strip() if pd.notna(row.get('issue')) else ''
            download_link = str(row['downloadlink']).strip() if pd.notna(row.get('downloadlink')) else ''

            # Use DBLP URL as fallback for download link
            if not download_link and pd.notna(row.get('publication')):
                download_link = str(row['publication']).strip()

            result = self._insert_citation(doi, title, year, authors, venue,
                                           volume, issue, download_link)
            stats[result] += 1

        self._print_stats(stats)

    def _process_proquest_csv(self, file_path: str):
        """Process ProQuest CSV export file"""
        stats = {'inserted': 0, 'duplicate': 0, 'no_doi': 0}

        df = pd.read_csv(file_path, sep=';')

        for _, row in df.iterrows():
            doi = str(row['digitalObjectIdentifier']).strip() if pd.notna(row.get('digitalObjectIdentifier')) else ''
            if not doi:
                stats['no_doi'] += 1
                continue

            title = str(row['Title']).strip() if pd.notna(row.get('Title')) else ''
            year = int(row['year']) if pd.notna(row.get('year')) else 0
            authors = str(row['Authors']).strip() if pd.notna(row.get('Authors')) else ''
            venue = str(row['pubtitle']).strip() if pd.notna(row.get('pubtitle')) else ''
            volume = str(row['volume']).strip() if pd.notna(row.get('volume')) else ''
            issue = str(row['issue']).strip() if pd.notna(row.get('issue')) else ''
            download_link = str(row['DocumentURL']).strip() if pd.notna(row.get('DocumentURL')) else ''

            result = self._insert_citation(doi, title, year, authors, venue,
                                           volume, issue, download_link)
            stats[result] += 1

        self._print_stats(stats)

    def process_files(self, file_config: Dict[str, list]):
        """Process all files based on their type"""
        try:
            # Process each file type
            for file_type, files in file_config.items():
                for file_path in files:
                    if not Path(file_path).exists():
                        print(f"File not found: {file_path}")
                        continue

                    print(f"\nProcessing {file_path} ({file_type}):")
                    try:
                        if file_type == 'bibtex':
                            self._process_bibtex(file_path)
                        elif file_type == 'ieee':
                            self._process_ieee_csv(file_path)
                        elif file_type == 'springer':
                            self._process_springer_csv(file_path)
                        elif file_type == 'dblp':
                            self._process_dblp_csv(file_path)
                        elif file_type == 'proquest':
                            self._process_proquest_csv(file_path)
                    except Exception as e:
                        print(f"Error processing {file_path}: {e}")
        finally:
            self.close()


def main():
    file_config = {
        'bibtex': ['acm.bib', 'ScienceDirect.bib', 'wiley.bib'],
        'ieee': ['ieee.csv'],
        'springer': ['SpringerLink.csv'],
        'dblp': ['dblp.csv'],
        'proquest': ['ProQuest.csv']
    }

    # Process all files
    processor = CitationProcessor()
    processor.process_files(file_config)


if __name__ == "__main__":
    main()
