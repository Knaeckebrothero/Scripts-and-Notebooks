"""
Script to process citation files from various sources and consolidate them into a SQLite database.
Supports BibTeX, IEEE CSV, Springer CSV, DBLP CSV, and ProQuest CSV formats.
Papers with DOIs go to the main 'papers' table, those without to 'papers_no_doi'.
"""
import bibtexparser
import sqlite3
import pandas as pd
from pathlib import Path
from typing import Dict, Set, Tuple


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


    def _standardize_doi(self, doi: str) -> str:
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
            'doi.org/',
            'DOI: ',
            'doi:',
            'DOI:'
        ]

        for prefix in prefixes:
            if doi.lower().startswith(prefix.lower()):
                doi = doi[len(prefix):]
                break

        return doi.strip()


    def _insert_paper(self, paper_data: Dict) -> str:
        """
        Function to insert a paper into the appropriate table.
        """
        cursor = self.conn.cursor()

        # Standardize DOI format
        doi = self._standardize_doi(paper_data.get('doi', ''))
        title = paper_data.get('title', '').strip()
        authors = paper_data.get('authors', '').strip()

        if not doi:
            # Check for duplicate in no_doi table using title and authors
            title_author_key = (title.lower(), authors.lower())
            if title_author_key in self.processed_titles_authors:
                return 'duplicate'

            try:
                cursor.execute('''
                INSERT INTO papers_no_doi 
                (title, publication_year, authors, venue, volume, issue, download_link, source_file)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (title, paper_data['year'], authors, paper_data['venue'],
                      paper_data['volume'], paper_data['issue'], paper_data['download_link'],
                      paper_data['source_file']))
                self.conn.commit()
                self.processed_titles_authors.add(title_author_key)
                return 'no_doi'
            except sqlite3.IntegrityError:
                return 'duplicate'

        # Handle papers with DOI
        if doi in self.processed_dois:
            return 'duplicate'

        cursor.execute('SELECT doi FROM papers WHERE doi = ?', (doi,))
        if cursor.fetchone() is not None:
            return 'duplicate'

        cursor.execute('''
        INSERT INTO papers 
        (doi, title, publication_year, authors, venue, volume, issue, download_link, source_file)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (doi, title, paper_data['year'], authors, paper_data['venue'],
              paper_data['volume'], paper_data['issue'], paper_data['download_link'],
              paper_data['source_file']))

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
        source_file = Path(file_path).name
        stats = {'inserted': 0, 'duplicate': 0, 'no_doi': 0}

        with open(file_path, 'r', encoding='utf-8') as bibtex_file:
            parser = bibtexparser.bparser.BibTexParser(common_strings=True)
            bib_database = bibtexparser.load(bibtex_file, parser)

        for entry in bib_database.entries:
            paper_data = {
                'doi': entry.get('doi', '').strip(),
                'title': entry.get('title', '').replace('{', '').replace('}', '').strip(),
                'year': int(entry.get('year', 0)),
                'authors': entry.get('author', ''),
                'venue': entry.get('journal', entry.get('booktitle', '')),
                'volume': entry.get('volume', ''),
                'issue': entry.get('number', ''),
                'download_link': entry.get('url', ''),
                'source_file': source_file
            }

            result = self._insert_paper(paper_data)
            stats[result] += 1

        self._print_stats(stats)

    def _process_ieee_csv(self, file_path: str):
        """Process IEEE CSV file"""
        source_file = Path(file_path).name
        stats = {'inserted': 0, 'duplicate': 0, 'no_doi': 0}

        df = pd.read_csv(file_path)

        for _, row in df.iterrows():
            paper_data = {
                'doi': str(row['DOI']).strip() if pd.notna(row['DOI']) else '',
                'title': str(row['Document Title']).strip() if pd.notna(row['Document Title']) else '',
                'year': int(row['Publication Year']) if pd.notna(row['Publication Year']) else 0,
                'authors': str(row['Authors']).strip() if pd.notna(row['Authors']) else '',
                'venue': str(row['Publication Title']).strip() if pd.notna(row['Publication Title']) else '',
                'volume': str(row['Volume']).strip() if pd.notna(row['Volume']) else '',
                'issue': str(row['Issue']).strip() if pd.notna(row['Issue']) else '',
                'download_link': str(row['PDF Link']).strip() if pd.notna(row['PDF Link']) else '',
                'source_file': source_file
            }

            result = self._insert_paper(paper_data)
            stats[result] += 1

        self._print_stats(stats)

    def _process_springer_csv(self, file_path: str):
        """Process Springer CSV file"""
        source_file = Path(file_path).name
        stats = {'inserted': 0, 'duplicate': 0, 'no_doi': 0}

        df = pd.read_csv(file_path)

        for _, row in df.iterrows():
            # Handle different venue types
            if pd.notna(row.get('Content Type')) and row['Content Type'] == 'Book':
                venue = str(row['Book Series Title']).strip() if pd.notna(row['Book Series Title']) else ''
            else:
                venue = str(row['Publication Title']).strip() if pd.notna(row['Publication Title']) else ''

            paper_data = {
                'doi': str(row['Item DOI']).strip() if pd.notna(row['Item DOI']) else '',
                'title': str(row['Item Title']).strip() if pd.notna(row['Item Title']) else '',
                'year': int(row['Publication Year']) if pd.notna(row['Publication Year']) else 0,
                'authors': str(row['Authors']).strip() if pd.notna(row['Authors']) else '',
                'venue': venue,
                'volume': str(row['Journal Volume']).strip() if pd.notna(row.get('Journal Volume')) else '',
                'issue': str(row['Journal Issue']).strip() if pd.notna(row.get('Journal Issue')) else '',
                'download_link': str(row['URL']).strip() if pd.notna(row['URL']) else '',
                'source_file': source_file
            }

            result = self._insert_paper(paper_data)
            stats[result] += 1

        self._print_stats(stats)

    def _process_dblp_csv(self, file_path: str):
        """Process DBLP CSV file"""
        source_file = Path(file_path).name
        stats = {'inserted': 0, 'duplicate': 0, 'no_doi': 0}

        df = pd.read_csv(file_path)

        for _, row in df.iterrows():
            download_link = str(row['downloadlink']).strip() if pd.notna(row.get('downloadlink')) else ''
            if not download_link and pd.notna(row.get('publication')):
                download_link = str(row['publication']).strip()

            paper_data = {
                'doi': str(row['doi']).strip() if pd.notna(row.get('doi')) else '',
                'title': str(row['title']).strip() if pd.notna(row.get('title')) else '',
                'year': int(row['year']) if pd.notna(row.get('year')) else 0,
                'authors': str(row['authors']).strip() if pd.notna(row.get('authors')) else '',
                'venue': str(row['venueName']).strip() if pd.notna(row.get('venueName')) else '',
                'volume': str(row['volume']).strip() if pd.notna(row.get('volume')) else '',
                'issue': str(row['issue']).strip() if pd.notna(row.get('issue')) else '',
                'download_link': download_link,
                'source_file': source_file
            }

            result = self._insert_paper(paper_data)
            stats[result] += 1

        self._print_stats(stats)

    def _process_proquest_csv(self, file_path: str):
        """Process ProQuest CSV file"""
        source_file = Path(file_path).name
        stats = {'inserted': 0, 'duplicate': 0, 'no_doi': 0}

        df = pd.read_csv(file_path, sep=';')

        for _, row in df.iterrows():
            paper_data = {
                'doi': str(row['digitalObjectIdentifier']).strip() if pd.notna(row.get('digitalObjectIdentifier')) else '',
                'title': str(row['Title']).strip() if pd.notna(row.get('Title')) else '',
                'year': int(row['year']) if pd.notna(row.get('year')) else 0,
                'authors': str(row['Authors']).strip() if pd.notna(row.get('Authors')) else '',
                'venue': str(row['pubtitle']).strip() if pd.notna(row.get('pubtitle')) else '',
                'volume': str(row['volume']).strip() if pd.notna(row.get('volume')) else '',
                'issue': str(row['issue']).strip() if pd.notna(row.get('issue')) else '',
                'download_link': str(row['DocumentURL']).strip() if pd.notna(row.get('DocumentURL')) else '',
                'source_file': source_file
            }

            result = self._insert_paper(paper_data)
            stats[result] += 1

        self._print_stats(stats)

    def process_files(self, file_config: Dict[str, list]):
        """Process all files based on their type"""
        try:
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

def import_citations(citations_path: str = 'search_results'):
    # Configure base path for search results
    search_dir = Path(citations_path)

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
        'springer': [search_dir / 'SpringerLink.csv']
    }

    processor = CitationProcessor()
    processor.process_files(file_config)
