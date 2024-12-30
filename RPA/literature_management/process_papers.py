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


class PdfProcessor:
    """
    ETL class for importing and processing research papers.
    """
    def __init__(self, prompt_path: str = 'assessment_prompt.txt', db_path: str = 'literature.db'):
        self.conn = sqlite3.connect(db_path)
        cursor = self.conn.cursor()

        # Initialize LangChain components
        self.llm = ChatOpenAI(
            model="gpt-4o", # gpt-4-turbo
            temperature=0.0
        )
        self.parser = PydanticOutputParser(pydantic_object=PaperAssessment)
        self.prompt = PromptTemplate(
            template=self._load_prompt_template(prompt_path),
            input_variables=["paper_content"],
            partial_variables={"format_instructions": self.parser.get_format_instructions()}
        )
    

    def __del__(self):
        if self.conn:
            self.conn.close()


    def close(self):
        if self.conn:
            self.conn.close()


    def _load_prompt_template(self, file_path: str) -> str:
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
            
            {format_instructions}
            """


    def standardize_doi(self, doi: str) -> Optional[str]:
        """
        Function to standardize a DOI string to the format '10.xxxx/yyyy.zzzz'.
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
            'doi: '
        ]

        for prefix in prefixes:
            if doi.startswith(prefix):
                doi = doi[len(prefix):]

        print(f"After prefix removal: {doi}")

        # Find potential DOIs and standardize them
        # First try exact ACM pattern
        acm_pattern = r'10\.1145/\d{7}\.\d{7}\b'
        matches = re.finditer(acm_pattern, doi)
        dois = [match.group(0) for match in matches]
        if dois:
            print(f"Found exact ACM DOI: {dois[0]}")
            return dois[0]

        # If no exact match, try general pattern and trim
        general_pattern = r'10\.\d{4}/\d+\.\d+'
        matches = re.finditer(general_pattern, doi)
        dois = [match.group(0) for match in matches]
        if dois:
            # Take first match and standardize it
            raw_doi = dois[0]
            print(f"Found raw DOI: {raw_doi}")

            # Split into parts and standardize
            try:
                prefix, numbers = raw_doi.split('/')
                base, suffix = numbers.split('.')
                # For ACM DOIs, ensure exactly 7 digits in each part
                if prefix == '10.1145':
                    base = base[:7]  # Take first 7 digits
                    suffix = suffix[:7]  # Take first 7 digits
                    standardized = f"{prefix}/{base}.{suffix}"
                    print(f"Standardized DOI: {standardized}")
                    return standardized
                return raw_doi
            except Exception as e:
                print(f"Error standardizing DOI: {e}")
                return None

        return None


    def extract_doi_from_pdf(self, pdf_path: str) -> Optional[str]:
        """
        Function to extract the DOI from PDF content or metadata.
        """
        try:
            with open(pdf_path, 'rb') as file:
                pdf = PyPDF2.PdfReader(file)

                # Try to get DOI from metadata
                if pdf.metadata and '/doi' in pdf.metadata:
                    return self.standardize_doi(pdf.metadata['/doi'])

                # Search first few pages for DOI pattern
                for i in range(min(3, len(pdf.pages))):
                    text = pdf.pages[i].extract_text().lower()

                    # Look for DOI patterns in the text
                    if 'doi' in text:
                        # Split into lines for better pattern matching
                        lines = text.split('\n')
                        for line in lines:
                            if 'doi' in line:
                                standardized_doi = self.standardize_doi(line)
                                if standardized_doi:
                                    return standardized_doi

                return None
        except Exception as e:
            print(f"Error extracting DOI from {pdf_path}: {e}")
            return None


    def extract_title_from_pdf(self, pdf_path: str) -> Optional[str]:
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


    def find_paper_id(self, pdf_path: str) -> Optional[str]:
        """
        Function to find the paper ID (DOI) from the database using DOI or title fallback.
        """
        cursor = self.conn.cursor()

        # Try DOI first
        doi = self.extract_doi_from_pdf(pdf_path)
        if doi:
            cursor.execute('SELECT doi FROM papers WHERE doi = ?', (doi,))
            result = cursor.fetchone()
            if result:
                return result[0]
            print(f"DOI {doi} not found in database, trying title matching...")

        # Fallback to title matching
        title = self.extract_title_from_pdf(pdf_path)
        if title:
            # Use simple string similarity for matching
            cursor.execute('''
                SELECT doi, title 
                FROM papers 
                WHERE LOWER(title) LIKE ?
                   OR ? LIKE CONCAT('%', LOWER(title), '%')
                   OR LOWER(title) LIKE CONCAT('%', ?, '%')
            ''', (title.lower(), title.lower(), title.lower()))

            results = cursor.fetchall()
            if results:
                if len(results) == 1:
                    return results[0][0]
                else:
                    print(f"Multiple potential matches found for title: {title}")
                    # Could implement manual selection here if needed
                    return results[0][0]  # Return first match

        print(f"No matching paper found for {pdf_path}")
        return None


    def process_pdf(self, pdf_path: str) -> Optional[PaperAssessment]:
        """
        Function to process a single PDF and return its assessment.
        """
        try:
            # Load PDF
            loader = PyPDFLoader(pdf_path)
            pages = loader.load()

            # Combine pages into a single text, focusing on important sections
            content = ""
            for page in pages:
                content += page.page_content + "\n"

            # Generate assessment using LLM
            chain = self.prompt | self.llm | self.parser
            assessment = chain.invoke({"paper_content": content[:8000]})  # Limit content length

            return assessment

        except Exception as e:
            print(f"Error processing {pdf_path}: {e}")
            return None


    def save_assessment(self, paper_id: str, assessment: PaperAssessment):
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
        Function to process all PDFs in the specified directory.
        """
        pdf_files = Path(directory_path).glob('*.pdf')

        for pdf_path in pdf_files:
            print(f"\nProcessing {pdf_path.name}...")

            # Find paper ID (using DOI or title fallback)
            paper_id = self.find_paper_id(str(pdf_path))
            if not paper_id:
                print(f"No matching paper found for {pdf_path.name}, skipping...")
                continue

            # Process PDF and save assessment
            assessment = self.process_pdf(str(pdf_path))
            if assessment:
                self.save_assessment(paper_id, assessment)
                print(f"Assessment saved for {pdf_path.name}")
            else:
                print(f"Failed to process {pdf_path.name}")
