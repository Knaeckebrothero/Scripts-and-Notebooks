"""
Script to process PDFs and assess them for inclusion in a systematic literature review.
Uses LangChain for PDF processing and LLM-based assessment.
"""
import os
import sqlite3
import re
from typing import List, Optional
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv, find_dotenv
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain.prompts import PromptTemplate
from langchain.output_parsers import PydanticOutputParser
from langchain_community.document_loaders import PyPDFLoader
import PyPDF2


class PaperAssessment(BaseModel):
    """
    Data model for the research paper assessment.
    """
    is_neurosymbolic: bool = Field(
        description="Whether the paper is primarily about neurosymbolic AI. This is the case if at least half of the paper's total content deals with or focuses on topics that involve combining or integrating symbolic reasoning, knowledge representation, or logic-based approaches with neural network architectures. Key indicators include substantial discussion of hybrid systems, neural-symbolic integration techniques, or applications of neurosymbolic methods to AI problems. Papers that only briefly mention neurosymbolic AI without significant elaboration should not be considered primarily about the topic."
    )

    key_development: bool = Field(
        description="Whether the paper presents a significant development in neurosymbolic AI. This includes introducing novel architectures, frameworks, or algorithms that advance the state-of-the-art in integrating symbolic reasoning with neural networks. Key developments should demonstrate improved performance, efficiency, or capabilities compared to existing approaches. They may address fundamental challenges in neurosymbolic AI, such as enhancing interpretability, incorporating prior knowledge, or enabling logical reasoning. Significant developments can also include innovative applications of neurosymbolic methods to real-world problems or domains where traditional approaches struggle. Incremental improvements or minor variations on existing techniques should not be considered key developments."
    )

    contribution: str = Field(
        description="Concisely summarize the main contribution or advancement made by the paper in 1-2 sentences. This should capture the most significant or novel aspect of the work, such as introducing a new neurosymbolic architecture, proposing an efficient algorithm for integrating symbolic knowledge into neural networks, or demonstrating substantial performance gains on a challenging task. The summary should highlight the key technical innovation or insight that sets this paper apart from prior work. Avoid simply restating the paper's title or abstract; instead, distill the essence of the contribution in clear and specific terms. If the paper makes multiple contributions, focus on the most impactful or central one."
    )


def _load_prompt_template(file_path: str) -> str:
    """
    Load the prompt template from a file or return a default template.
    """
    try:
        with open(file_path, "r") as f:
            return f.read()
    except FileNotFoundError:
        return """
        Please analyze the following research paper content and provide a structured assessment.
        Focus on its relevance to neurosymbolic AI and its key developments.
        
        Paper content:
        {paper_content}
        
        Assessment instructions:
        {format_instructions}
        """


def standardize_doi(doi: str) -> Optional[str]:
    """
    Function to standardize a DOI string to the format '10.xxxx/yyyy.zzzz'.
    Handles various DOI formats and patterns found in academic papers.
    """
    if not doi:
        return None

    print(f"Original DOI string: {doi}")

    # Remove common prefixes and whitespace
    doi = doi.lower().strip()
    prefixes = [
        'https://doi.org/',
        'http://doi.org/',
        'doi.org/',
        'doi:',
        'doi: ',
        'digital object identifier ',
        'digital object identifier: '
    ]

    for prefix in prefixes:
        if doi.startswith(prefix):
            doi = doi[len(prefix):]

    print(f"After prefix removal: {doi}")

    # Define patterns for different DOI formats
    patterns = [
        # ACM specific patterns
        r'10\.1145/\d{7}\.\d{7}\b',
        r'10\.1145/\d{6,7}\b',  # Shorter ACM DOIs
        # IEEE conference and journal patterns
        r'10\.1109/[A-Z]+\d+\.\d+\.\d+',
        r'10\.1109/[A-Z]+\.\d{4}\.\d{7}',
        # Wiley patterns
        r'10\.1002/[a-z]+\.\d{4}',
        r'10\.1002/[a-z]+\.\d{4,5}',
        # Russian journal pattern
        r'10\.3103/S\d{13}\b',
        # General DOI patterns with optional suffix
        r'10\.\d{4,5}/[-._;()\/:A-Z0-9]+',
        # Alternative format with S-prefixed numbers
        r'10\.\d{4}/S\d+',
        # Handle DOIs embedded in URLs
        r'(?<=doi\.org/)(10\.\d{4,5}/[-._;()\/:A-Z0-9]+)',
        # Alternative format sometimes used
        r'10/[-._;()\/:A-Z0-9]+\.\d{4}',
        # ACM format with year
        r'10\.1145/\d{7}(?:\.\d{0,7})?',
    ]

    # Try each pattern
    for pattern in patterns:
        matches = re.finditer(pattern, doi, re.IGNORECASE)
        dois = [match.group(0) for match in matches]
        if dois:
            raw_doi = dois[0]
            print(f"Found DOI with pattern {pattern}: {raw_doi}")

            # Standardize ACM DOIs
            if raw_doi.startswith('10.1145/'):
                try:
                    prefix, numbers = raw_doi.split('/')
                    base, suffix = numbers.split('.')
                    base = base[:7]  # Take first 7 digits
                    suffix = suffix[:7]  # Take first 7 digits
                    standardized = f"{prefix}/{base}.{suffix}"
                    print(f"Standardized ACM DOI: {standardized}")
                    return standardized
                except Exception as e:
                    print(f"Error standardizing ACM DOI: {e}")
                    continue

            return raw_doi

    return None


def extract_doi_from_pdf(pdf_path: str) -> Optional[str]:
    """
    Function to extract the DOI from PDF content or metadata.
    Now handles various DOI formats and locations in academic papers.
    """
    try:
        with open(pdf_path, 'rb') as file:
            pdf = PyPDF2.PdfReader(file)

            # Try metadata first
            if pdf.metadata and '/doi' in pdf.metadata:
                doi = standardize_doi(pdf.metadata['/doi'])
                if doi:
                    return doi

            # Search first few pages
            # Search first few pages
            search_phrases = [
                'doi',
                'digital object identifier',
                'https://doi.org',
                'doi.org',
                '10.1109/',  # IEEE
                '10.1145/',  # ACM
                '10.1007/',  # Springer
                '10.3103/',  # Russian journals
                '10.1002/',  # Wiley
                'acm.org',   # ACM permissions
                'permission',  # Often appears near DOIs
                'copyright',   # Often appears near DOIs
                '©',          # Copyright symbol often precedes DOI
                'received:',   # Often appears near DOIs in headers
                'revised:',    # Often appears near DOIs in headers
                'accepted:'    # Often appears near DOIs in headers
            ]

            # Expand search range to catch DOIs that might appear later
            for i in range(min(5, len(pdf.pages))):
                text = pdf.pages[i].extract_text().lower()
                lines = text.split('\n')

                # First try to find lines containing DOI indicators
                for line in lines:
                    if any(phrase in line.lower() for phrase in search_phrases):
                        doi = standardize_doi(line)
                        if doi:
                            return doi

                # If no DOI found with indicators, try pattern matching on all lines
                # This catches cases where DOI appears without explicit marking
                for line in lines:
                    doi = standardize_doi(line)
                    if doi:
                        return doi

            return None

    except Exception as e:
        print(f"Error extracting DOI from {pdf_path}: {e}")
        return None


def extract_title_from_pdf(pdf_path: str) -> Optional[str]:
    """
    Function to extract the title from PDF.
    """
    try:
        with open(pdf_path, 'rb') as file:
            pdf = PyPDF2.PdfReader(file)

            # Try metadata first
            if pdf.metadata and '/Title' in pdf.metadata:
                return pdf.metadata['/Title'].strip()

            # Try first page
            first_page_text = pdf.pages[0].extract_text()
            lines = first_page_text.split('\n')

            # Usually the title is one of the first non-empty lines
            for line in lines[:10]:  # Check first 10 lines
                line = line.strip()
                if line and len(line) > 20:  # Basic heuristic for title-like text
                    return line

            return None
    except Exception as e:
        print(f"Error extracting title from {pdf_path}: {e}")
        return None


class PdfProcessor:
    """
    ETL class for importing and processing research papers.
    """
    def __init__(self, prompt_path: str = 'assessment_prompt.txt', db_path: str = 'literature.db'):
        self.conn = sqlite3.connect(db_path)
        # cursor = self.conn.cursor()

        # Initialize LangChain components
        self.llm = ChatOpenAI(
            model="gpt-4o-mini", # gpt-4-turbo gpt-4o
            temperature=0.0,
            seed=3459746589468594
        )
        self.parser = PydanticOutputParser(pydantic_object=PaperAssessment)
        self.prompt = PromptTemplate(
            template=_load_prompt_template(prompt_path),
            input_variables=["paper_content"],
            partial_variables={"format_instructions": self.parser.get_format_instructions()}
        )


    def __del__(self):
        if self.conn:
            self.conn.close()


    def close(self):
        if self.conn:
            self.conn.close()


    def find_paper_id(self, pdf_path: str) -> Optional[int]:
        """
        Function to find the paper ID from the database using DOI or title fallback.
        Returns the integer ID from the papers table.
        """
        cursor = self.conn.cursor()

        # Try DOI first
        doi = extract_doi_from_pdf(pdf_path)
        if doi:
            # Debug: Print all DOIs in database for comparison
            cursor.execute('SELECT doi FROM papers')
            all_dois = [row[0] for row in cursor.fetchall()]
            print(f"Found DOI in PDF: {doi}")
            print(f"Looking for match among database DOIs: {all_dois[:5]}...")  # Show first 5 for brevity

            # Try exact match first
            cursor.execute('SELECT id FROM papers WHERE doi = ?', (doi,))
            result = cursor.fetchone()
            if result:
                return result[0]

            # If no exact match, try case-insensitive match
            cursor.execute('SELECT id FROM papers WHERE LOWER(doi) = LOWER(?)', (doi,))
            result = cursor.fetchone()
            if result:
                return result[0]

            # If still no match, try without any potential trailing characters
            base_doi = re.match(r'(10\.\d{4,5}/[^/\s]+)', doi)
            if base_doi:
                cursor.execute('SELECT id FROM papers WHERE doi LIKE ?', (f"{base_doi.group(1)}%",))
                result = cursor.fetchone()
                if result:
                    return result[0]

            print(f"DOI {doi} not found in database with any matching method, trying title matching...")

        # Fallback to title matching
        title = extract_title_from_pdf(pdf_path)
        if title:
            print(f"Attempting to match title: {title}")

            # Clean the title for better matching
            clean_title = re.sub(r'[^\w\s-]', '', title.lower())
            words = clean_title.split()
            if len(words) > 3:  # Only try if we have enough words to make a meaningful match
                # Create a LIKE pattern matching any 3 consecutive words
                patterns = []
                for i in range(len(words) - 2):
                    pattern = f"%{words[i]}%{words[i+1]}%{words[i+2]}%"
                    patterns.append(pattern)

                # Try each pattern
                for pattern in patterns:
                    cursor.execute('''
                        SELECT id, title
                        FROM papers 
                        WHERE LOWER(REPLACE(title, ':', '')) LIKE ?
                    ''', (pattern,))

                    results = cursor.fetchall()
                    if results:
                        print(f"Found {len(results)} potential matches:")
                        for r in results:
                            print(f"ID: {r[0]}, Title: {r[1]}")
                        return results[0][0]  # Return first match

            print(f"No title matches found using any pattern")

        print(f"No matching paper found for {pdf_path}")
        return None


    def process_pdf(self, pdf_path: str) -> Optional[PaperAssessment]:
        """
        Function to process a single PDF and return its assessment.
        """
        try:
            #if len(PyPDF2.PdfReader(pdf_path).pages) > 40:
            #    print(f"Skipping {pdf_path} due to excessive page count")
            #    return None

            # Load PDF
            loader = PyPDFLoader(pdf_path)
            pages = loader.load()

            # Combine pages into a single text, focusing on important sections
            content = ""
            for page in pages:
                content += page.page_content + "\n"

            # Generate assessment using a LLM
            # chain = self.prompt | self.llm | self.parser
            # assessment = chain.invoke({"paper_content": content})

            # return assessment

            print("Processed")
            return PaperAssessment(is_neurosymbolic=True, key_development=True, contribution="This is a test")

        except Exception as e:
            print(f"Error processing {pdf_path}: {e}")
            return None


    def save_assessment(self, paper_id: int, assessment: PaperAssessment):
        """
        Function to save the paper assessment to the database.
        """
        cursor = self.conn.cursor()

        cursor.execute('''
        INSERT OR REPLACE INTO paper_assessments
        (paper_id, is_neurosymbolic, key_development, contribution, assessment_date)
        VALUES (?, ?, ?, ?, ?)
        ''', (
            paper_id,
            assessment.is_neurosymbolic,
            assessment.key_development,
            assessment.contribution,
            datetime.now().isoformat()
        ))

        self.conn.commit()


    def process_directory(self, directory_path: str):
        """
        Method to process all PDFs in the specified directory.
        """
        pdf_files = Path(directory_path).glob('*.pdf')

        for pdf_path in pdf_files:
            print(f"\nProcessing {pdf_path.name}...")

            # Find paper ID (using DOI or title fallback)
            paper_id = self.find_paper_id(str(pdf_path))
            print("Paper ID -> ", paper_id)
            if not paper_id:
                print(f"No matching paper found for {pdf_path.name}, skipping...")
                continue

            # Process PDF and save assessment
            assessment = self.process_pdf(str(pdf_path))
            if assessment:
                self.save_assessment(paper_id, assessment)
                print(f"Assessment saved for {pdf_path.name}")

                # Save the path in the database
                cursor = self.conn.cursor()
                cursor.execute('''
                    UPDATE papers
                    SET file_path = ?
                    WHERE id = ?
                ''', (str(pdf_path), paper_id))

            else:
                print(f"Failed to process {pdf_path.name}")
