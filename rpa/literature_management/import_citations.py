"""
Script to process citation files from various sources and consolidate them into a SQLite database.
Supports BibTeX, IEEE CSV, Springer CSV, DBLP CSV, and ProQuest CSV formats.
Papers with DOIs go to the main 'papers' table, those without to 'papers_no_doi'.
"""
import bibtexparser
import sqlite3
import pandas as pd
from pathlib import Path
from typing import Dict


def _standardize_doi(doi: str) -> str:
    """
    Function to standardize the DOI format by removing common prefixes.
    """
    if not doi:
        return ''

    # Remove common prefixes and whitespace
    doi = doi.strip()
    prefixes = [
        'https://doi.org/',
        'http://doi.org/',
        'doi.org/'
    ]

    for prefix in prefixes:
        if doi.lower().startswith(prefix.lower()):
            doi = doi[len(prefix):]
            break

    return doi.strip()


def _print_stats(stats: Dict[str, int]):
    # Print statistics for file processing
    print(f"New entries inserted: {stats['inserted']}")
    print(f"Duplicates skipped: {stats['duplicate']}")
    print(f"Entries without DOI: {stats['no_doi']}")


class CitationProcessor:
    def __init__(self, db_name: str = 'literature.db'):
        """
        Initialize the citation processor with database connection
        """
        self.db_name = db_name
        self.processed_dois = set()
        self.processed_titles_authors = set()  # For checking duplicates in no_doi table
        self.conn = sqlite3.connect(db_name)


    def __del__(self):
        if self.conn:
            self.conn.close()


    def close(self):
        if self.conn:
            self.conn.close()


    def process_keywords(self, paper_id: int, keywords: list[str]):
        """
        Process a list of keywords for a paper, adding them to the keywords table if they don't exist
        and creating relationships in the rel_keywords_papers table.

        Args:
            paper_id: The ID of the paper in the papers table
            keywords: List of keyword strings to process
        """
        cursor = self.conn.cursor()

        # Clean and filter keywords
        cleaned_keywords = [kw.strip() for kw in keywords if kw.strip()]

        for keyword in cleaned_keywords:
            try:
                # Try to insert the keyword if it doesn't exist
                cursor.execute('''
                    INSERT OR IGNORE INTO keywords (keyword)
                    VALUES (?)
                ''', (keyword,))

                # Get the keyword_id (whether it was just inserted or already existed)
                cursor.execute('''
                    SELECT id FROM keywords WHERE keyword = ?
                ''', (keyword,))
                keyword_id = cursor.fetchone()[0]

                # Create the relationship between paper and keyword
                cursor.execute('''
                    INSERT OR IGNORE INTO rel_keywords_papers (paper_id, keyword_id)
                    VALUES (?, ?)
                ''', (paper_id, keyword_id))

            except sqlite3.Error as e:
                print(f"Error processing keyword '{keyword}' for paper {paper_id}: {e}")

        self.conn.commit()


    def _insert_paper(self, paper_data: Dict) -> str:
        """
        Function to insert a paper into the appropriate table.
        """
        cursor = self.conn.cursor()

        # Standardize DOI format
        doi = _standardize_doi(paper_data.get('doi', ''))
        title = paper_data.get('title', '').strip()
        authors = paper_data.get('authors', '').strip()

        # Handle papers with DOI
        if doi in self.processed_dois:
            return 'duplicate'

        cursor.execute('SELECT doi FROM papers WHERE doi = ?', (doi,))
        if cursor.fetchone() is not None:
            return 'duplicate'

        cursor.execute('''
        INSERT INTO papers 
        (doi, title, publication_year, authors, venue, volume, publication_type, 
         publication_source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (doi, title, paper_data['publication_year'], authors, paper_data['venue'],
              paper_data['volume'], paper_data['publication_type'], paper_data['publication_source']))

        self.conn.commit()
        self.processed_dois.add(doi)
        return 'inserted'


    def _process_bibtex(self, file_path: str):
        """
        Method to process a BibTeX file.
        """
        source_file = Path(file_path).name
        stats = {'inserted': 0, 'duplicate': 0, 'no_doi': 0}

        with open(file_path, 'r', encoding='utf-8') as bibtex_file:
            parser = bibtexparser.bparser.BibTexParser(common_strings=True)
            bib_database = bibtexparser.load(bibtex_file, parser)

        for entry in bib_database.entries:
            paper_data = {
                'doi': entry.get('doi', '').strip(),
                'title': entry.get('title', '').replace('{', '').replace('}', '').strip(),
                'publication_year': int(entry.get('year', 0)),
                'authors': entry.get('author', ''),
                'venue': entry.get('journal', entry.get('booktitle', '')),
                'volume': entry.get('volume', ''),
                # 'issue': entry.get('number', ''),
                'publication_type': 'journal' if not entry.get('journal') else 'Conference',
                'publication_source': source_file
                .replace('_1.bib', '').replace('_2.bib', '').strip()
            }

            # Insert the paper and get its result
            result = self._insert_paper(paper_data)
            stats[result] += 1

            # If paper was inserted successfully, process its keywords
            if result == 'inserted' and entry.get('keywords'):
                # Get paper_id for the newly inserted paper
                cursor = self.conn.cursor()
                cursor.execute('SELECT id FROM papers WHERE doi = ?', (_standardize_doi(paper_data['doi']),))
                paper_id = cursor.fetchone()[0]

                self.process_keywords(paper_id, entry.get('keywords').split(','))

        _print_stats(stats)


    def _process_ieee_csv(self, file_path: str):
        """
        Method to process IEEE CSV file.
        """
        stats = {'inserted': 0, 'duplicate': 0, 'no_doi': 0}
        df = pd.read_csv(file_path)

        for _, row in df.iterrows():
            paper_data = {
                'doi': str(row['DOI']).strip() if pd.notna(row['DOI']) else '',
                'title': str(row['Document Title']).strip() if pd.notna(row['Document Title']) else '',
                'publication_year': int(row['Publication Year']) if pd.notna(row['Publication Year']) else None,
                'authors': str(row['Authors']).strip() if pd.notna(row['Authors']) else '',
                'venue': str(row['Publication Title']).strip() if pd.notna(row['Publication Title']) else '',
                'volume': str(row['Volume']).strip() if pd.notna(row['Volume']) else '',
                'publication_type': str(row['Document Identifier'].replace('IEEE', '').strip().lower())
                if pd.notna(row['Document Identifier']) else '',
                'publication_source': 'ieee'
            }

            # Insert the paper and get its result
            result = self._insert_paper(paper_data)
            stats[result] += 1

            # If paper was inserted successfully, process its keywords
            if result == 'inserted':
                # Get paper_id for the newly inserted paper
                cursor = self.conn.cursor()
                cursor.execute('SELECT id FROM papers WHERE doi = ?', (_standardize_doi(paper_data['doi']),))
                paper_id = cursor.fetchone()[0]

                # Combine and process keywords
                combined_keywords = []
                if pd.notna(row.get('Author Keywords')):
                    combined_keywords.extend(row['Author Keywords'].split(';'))
                if pd.notna(row.get('IEEE Terms')):
                    combined_keywords.extend(row['IEEE Terms'].split(';'))

                self.process_keywords(paper_id, combined_keywords)

        _print_stats(stats)


    def _process_springer_csv(self, file_path: str):
        """
        Method to process Springer CSV file.
        """
        source_file = Path(file_path).name
        stats = {'inserted': 0, 'duplicate': 0, 'no_doi': 0}

        df = pd.read_csv(file_path)

        for _, row in df.iterrows():
            paper_data = {
                'doi': str(row['Item DOI']).strip() if pd.notna(row['Item DOI']) else '',
                'title': str(row['Item Title']).strip() if pd.notna(row['Item Title']) else '',
                'publication_year': int(row['Publication Year']) if pd.notna(row['Publication Year']) else None,
                'authors': str(row['Authors']).strip() if pd.notna(row['Authors']) else '',
                'venue': str(row['Publication Title']).strip() if pd.notna(row['Publication Title']) else '',
                'volume': str(row['Journal Volume']).strip() if pd.notna(row.get('Journal Volume')) else '',
                # 'issue': str(row['Journal Issue']).strip() if pd.notna(row.get('Journal Issue')) else '',
                'publication_type': str(row['Content Type']).strip() if pd.notna(row['Content Type']) else '',
                'publication_source': 'springer'
            }

            # Insert the paper and get its result
            result = self._insert_paper(paper_data)
            stats[result] += 1

        _print_stats(stats)


    def process_files(self, file_config: Dict[str, list]):
        """
        Method to process all files based on their type.
        """
        try:
            for file_type, files in file_config.items():
                for file_path in files:
                    if not Path(file_path).exists():
                        print(f"File not found: {file_path}")
                        continue

                    # Process
                    print(f"\nProcessing {file_path} ({file_type}):")
                    try:
                        if file_type == 'bibtex':
                            self._process_bibtex(file_path)
                        elif file_type == 'ieee':
                            self._process_ieee_csv(file_path)
                        elif file_type == 'springer':
                            self._process_springer_csv(file_path)
                    except Exception as e:
                        print(f"Error processing {file_path}: {e}")
        finally:
            self.close()
