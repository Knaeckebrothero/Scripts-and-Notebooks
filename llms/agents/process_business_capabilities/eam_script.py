"""
Business Capability Validation System
Enterprise Architecture Management - SpeedParcel Case Study

This system validates business capabilities against case studies and literature
using AI-powered analysis to find supporting evidence.

Key Features:
1. Loads business capabilities from markdown or JSON files
2. Validates each capability against the SpeedParcel case study
3. Searches academic literature using ChromaDB vector database
4. Generates comprehensive descriptions based on collected evidence
5. Detects capability overlaps and redundancies
6. Outputs results in JSON and/or Markdown format

The overlap detection feature helps identify:
- Duplicate capabilities with different names
- Capabilities that should be merged or split
- Unclear boundaries between capabilities
- Opportunities for capability consolidation

How Overlap Detection Works:
1. Pre-filters capabilities by level and name similarity
2. Uses LLM to analyze functional overlap between capabilities
3. Categorizes overlaps as DUPLICATE (90%+), MAJOR (60-90%), or MINOR (30-60%)
4. Provides specific recommendations for each overlap
5. Generates actionable reports for capability map refinement

Requirements:
pip install langchain langchain-anthropic chromadb tqdm sentence-transformers

Usage:
1. Set your Anthropic API key:
   export ANTHROPIC_API_KEY='your-api-key'

2. Test capability loading:
   python capability_validator.py test

3. Test case study validation:
   python capability_validator.py test-case-study

4. Test literature validation:
   python capability_validator.py test-literature

5. Test description generation:
   python capability_validator.py test-description

6. Test overlap detection:
   python capability_validator.py test-overlap

7. Test full validation workflow:
   python capability_validator.py test-full

8. Run all tests:
   python capability_validator.py test-all

9. Run full validation:
   python capability_validator.py --capabilities Business_Capability_Map.md

10. Run with overlap analysis export:
    python capability_validator.py --capabilities Business_Capability_Map.md --export-overlaps

11. Run with external prompts file:
    python capability_validator.py --prompts prompts.json --capabilities Business_Capability_Map.md

12. Rebuild literature database:
    python capability_validator.py --rebuild-literature --capabilities Business_Capability_Map.md

ChromaDB Persistence:
The literature database is persisted to disk by default (.chroma_db directory).
This means embeddings are calculated only once and reused across runs.
Use --rebuild-literature to force a rebuild of the database.

Implementation Status:
- [✓] Load capabilities from markdown/JSON
- [✓] Validate against case study using LLM
- [✓] Validate against literature (ChromaDB + RAG)
- [✓] Generate descriptions from evidence
- [✓] Check for capability overlaps

All core functionality is now implemented!
"""
import json
import logging
import pickle
import re
import hashlib
#import chromadb
import time
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from chromadb import Settings
import pypdf

import dotenv
from chromadb.utils import embedding_functions
from dotenv import load_dotenv, find_dotenv
from pydantic import BaseModel, Field
from tqdm import tqdm
from langchain_anthropic import ChatAnthropic
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_core.prompts import ChatPromptTemplate
from langchain.output_parsers import PydanticOutputParser

from langchain_anthropic import ChatAnthropic
from langchain_community.vectorstores import Chroma # <-- ADD THIS
from langchain_community.embeddings.sentence_transformer import SentenceTransformerEmbeddings # <-- ADD THIS


# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def check_api_key():
    """
    Check if Anthropic API key is available
    """
    if not os.environ.get('ANTHROPIC_API_KEY'):
        logger.error("ANTHROPIC_API_KEY environment variable not set!")
        logger.error("Please set it with: export ANTHROPIC_API_KEY='your-api-key'")
        return False
    return True


# ============================================================================
# Pydantic Models for Structured Output
# ============================================================================

class EvidenceItem(BaseModel):
    """Single piece of evidence from case study"""
    quote: str = Field(description="Direct quote from the case study")
    section: str = Field(description="Section or context where the quote appears")
    relevance_explanation: str = Field(description="Why this evidence supports the capability")
    relevance_score: float = Field(description="Relevance score from 0.0 to 1.0")

class CaseStudyValidationOutput(BaseModel):
    """Structured output from case study validation"""
    capability_justified: bool = Field(description="Whether the capability is justified by the case study")
    justification_summary: str = Field(description="Overall summary of why/why not the capability is justified")
    evidence_items: List[EvidenceItem] = Field(description="List of evidence supporting the capability")
    related_departments: List[str] = Field(description="Departments or teams mentioned in relation to this capability")
    related_systems: List[str] = Field(description="IT systems mentioned in relation to this capability")

@dataclass
class ValidatorConfig:
    """Configuration for the validation system"""
    # LLM settings
    llm_model: str = "claude-3-haiku-20240307"  # Most cost-effective. Options: claude-3-opus-20240229, claude-3-sonnet-20240229
    temperature: float = 0.1
    max_tokens: int = 2000
    anthropic_api_key: Optional[str] = None  # Will use ANTHROPIC_API_KEY env var if not set

    # ChromaDB settings
    chunk_size: int = 500
    chunk_overlap: int = 50
    embedding_model: str = "all-MiniLM-L6-v2"
    collection_name: str = "ea_literature"
    chroma_persist_directory: Optional[str] = ".chroma_db"  # None for in-memory, path for persistent

    # Validation settings
    similarity_threshold: float = 0.8  # For overlap detection
    min_evidence_score: float = 0.7   # Minimum relevance score
    batch_size: int = 5               # Capabilities to process at once

    # Cache settings
    enable_cache: bool = True
    cache_dir: str = ".validation_cache"
    cache_expiry_days: int = 7

    # Output settings
    save_intermediate: bool = True
    output_format: str = "json"  # json, markdown, or both

    # Prompts configuration
    prompts_dir: str = "prompts"  # Directory containing prompt files


@dataclass
class Capability:
    """Represents a business capability"""
    id: str
    name: str
    level: int  # 1 or 2
    category: str  # Core, Guiding, or Enabling
    parent_id: Optional[str] = None
    description: Optional[str] = None
    l2_capabilities: List[str] = field(default_factory=list)


@dataclass
class ValidationEvidence:
    """Evidence supporting a capability"""
    source_type: str  # 'case_study' or 'literature'
    quote: str
    reference: str  # Page, section, or citation
    relevance_score: float = 0.0


@dataclass
class ValidationResult:
    """Complete validation result for a capability"""
    capability_id: str
    case_study_evidences: List[ValidationEvidence] = field(default_factory=list)
    literature_evidences: List[ValidationEvidence] = field(default_factory=list)
    generated_description: str = ""
    overlaps: List[Dict[str, str]] = field(default_factory=list)  # List of overlapping capabilities
    validation_status: str = "pending"  # pending, validated, needs_review
    validation_timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


# ============================================================================
# Main Validator Class
# ============================================================================

class CapabilityValidator:
    """Main validation system for business capabilities"""

    def __init__(self, case_study_path: str, literature_paths: List[str],
                 config: Optional[ValidatorConfig] = None):
        self.case_study_path = case_study_path
        self.literature_paths = literature_paths
        self.config = config or ValidatorConfig()
        self.capabilities: Dict[str, Capability] = {}
        self.validation_results: Dict[str, ValidationResult] = {}
        self.vector_store = None  # This will hold our LangChain Chroma DB
        self.cache_dir = Path(self.config.cache_dir)
        self.case_study_content = None
        self.llm = None
        self.prompts = {}  # Will store loaded prompts

        # Create cache directory if caching is enabled
        if self.config.enable_cache:
            self.cache_dir.mkdir(exist_ok=True)

        # Initialize LLM
        self._initialize_llm()

        # Load prompts
        self._load_prompts()

    # ========================================================================
    # Setup Methods
    # ========================================================================

    def _initialize_llm(self) -> None:
        """Initialize the Anthropic LLM via LangChain"""
        try:
            # Use config API key if provided, otherwise rely on environment variable
            kwargs = {
                "model": self.config.llm_model,
                "temperature": self.config.temperature,
                "max_tokens": self.config.max_tokens
            }

            if self.config.anthropic_api_key:
                kwargs["anthropic_api_key"] = self.config.anthropic_api_key

            self.llm = ChatAnthropic(**kwargs)
            logger.info(f"Initialized LLM: {self.config.llm_model}")
        except Exception as e:
            logger.error(f"Failed to initialize LLM: {e}")
            logger.error("Make sure ANTHROPIC_API_KEY environment variable is set or provide api_key in config")
            logger.error(f"Current model: {self.config.llm_model}")
            logger.error("Valid models: claude-3-opus-20240229, claude-3-sonnet-20240229, claude-3-haiku-20240307")
            raise

    def _load_case_study(self) -> None:
        """Load the case study content from file"""
        if self.case_study_content is not None:
            return  # Already loaded

        # Try to load from file first
        case_study_path = Path(self.case_study_path)
        if case_study_path.exists():
            try:
                if case_study_path.suffix.lower() == '.pdf':
                    logger.warning("PDF parsing not implemented. Using embedded case study.")
                else:
                    with open(case_study_path, 'r', encoding='utf-8') as f:
                        self.case_study_content = f.read()
                    logger.info(f"Loaded case study from file: {len(self.case_study_content)} characters")
                    return
            except Exception as e:
                logger.warning(f"Could not load from file: {e}. Using embedded case study.")

        # Use embedded case study content as fallback
        self.case_study_content = """Case Study: SpeedParcel

Business Background:
SpeedParcel is a global parcel logistics company with headquarter in Frankfurt/Main (Germany) operating since the year 2007. It is serving business customers in the following areas:
• Domestic delivery: Last-mile-delivery networks in Germany, Austria and the Czech Republic
• X-border: International parcel shipping from South-East Asia (SEA), China and Australia
• Order fulfilment: storing goods for small/medium-sized web shop providers and delivering shipments for consumer orders

SpeedParcel is not a postal organisation and, therefore, does not offer services for private customers who want to send individual parcels. A customer sending parcels is usually referred to as shipper and a parcel or a group of related parcels from a shipper to the same recipient is called a shipment.

Domestic Operations: The term domestic relates to shipments within a country. SpeedParcel is picking up shipments from the shipper's premises on a daily basis. Shipments can be consolidated on pallets as well as in containers, boxes or mail bags (generally referred to as consignment). Information about shipments and consignments need to be sent up-front by the shipper (pre-advise) so that SpeedParcel is able to schedule resources in each of its 13 sorting centres each of which covers a certain area. Consignments will be broken up (i.e. shipments taken out of the containers etc.) in a sorting centre and the shipments are then sorted with respect to the area of their destination. There is an internal truck fleet for moving the parcels from the origin sorting centre to the destination sorting centre. The last mile delivery is done from the destination sorting centre to the recipients address, a parcel locker or a local parcel shop. SpeedParcel is tracking any shipment and transmits the shipping status to the shipper via a so-called track event. Undeliverable shipments are returned to the shipper.

X-border Operations: International shipments are offered for a limited number of regions only. Similar to domestic, X-border consignments are picked up from shippers and processed in a sorting centre. Consignments are broken up and sorted with respect to destination countries. These new consignments are then handed over to airlines which transport them to the destination country and hand them over to the national postal service provider for last mile delivery. SpeedParcel also transmits shipment data to the airline, the destination country's postal company and customs organisations in the country of origin for export and to the respective authorities in the country of destination (for import customs clearance). SpeedParcel needs to check whether they are compliant with any export and import regulation. SpeedParcel handles DDU (Delivery and Duties Unpaid) so that the recipient has to take care of customs fees.

Order Fulfilment: Small and medium-sized companies selling goods (called seller) via the internet (web shop or electronic marketplace) who cannot afford a warehouse on their own can use the order fulfilment service of SpeedParcel. SpeedParcel maintains dedicated warehouses where sellers can store their goods and SpeedParcel will ship them as requested for each individual order to the corresponding consumer. The consumer places the order on the web shop (or marketplace) which will be forwarded to the warehouse and processed by local staff. A picker takes the goods from the warehouse and a packer puts them into a box. Required labels are attached for further processing via domestic delivery (shipping label) or X-border (shipping and customs label CN22 or CN23). Consolidated shipments are then handed over to SpeedParcel domestic or X-border.

Company Structure:
The organisational structure includes:
- Performance & Compliance: Staff department supporting corporate management, defining and measuring KPIs, generating corporate reports, business process improvement, compliance regulations for international trade
- Product Management: Develops new logistics products and implements them, consults sales teams on product features, develops work instructions for operations
- Marketing: Supports customer-facing units with market data and analytics, plans and performs marketing campaigns
- Business Development: Investigates and enables new market opportunities, currently working on Netherlands expansion and extending fulfilment business
- Sales domestic: Dedicated teams for Germany, Austria, and Czech Republic, finding new customers, up- and cross-selling
- Sales X-border: International logistics sales, different products vary in rates, delivery time and additional services
- Network Management: Defines locations for sorting centres and warehouses, transportation links, long-term contracts with airlines
- Mail Terminal Operations: Defines standards and reference processes for mail terminals, manages local operations in sorting centres
- Customs: Bundles competencies for customs processing, manages customs experts in X-border terminals
- Airline Booking: Negotiates contracts for pre-booked capacity with airlines, procures capacity on spot market
- Trucking: Manages trucking fleet and additional cars, operates like internal car rental company
- Order fulfilment: Responsible for setting up warehouses, customer onboarding and e-business integration
- Last Mile Delivery: Manages delivery to households, partners with local shop owners, installs parcel lockers
- HR: Recruiting, onboarding, training, legal office, ESG department, global and local teams
- Finance: Budgeting, financial reporting, controlling and accounting
- Information Management: Standard IT services (ITIL), application lifecycle management, data warehouse

IT Environment:
SpeedParcel owns two data centres (Frankfurt and Cyberjaya). Applications include:
- salesforce: Central CRM system (€150k annual cost)
- Jira: Ticketing and workflow management (€273k annual)
- API Gateway: Web services to external partners (€600k annual)
- RabbitMQ: Message-oriented middleware for data flows (€202k annual)
- Power BI: Sales data visualization (€76k annual)
- StatMan/Statman+: Shipment sorting optimization systems
- TrackDB: Tracking data aggregation (€133k annual)
- IBM OMS: Order management system (€730k annual)
- WHAU: Warehouse management in Australia and China
- SAP HR: Human resource management (€345k annual)
- SAP FI: Financial accounting Europe (€543k annual)
- Plus many legacy systems from acquisitions

Challenges:
- Integration issues from acquisitions: processes executed differently across countries, friction in data exchange due to incompatible systems
- Redundant applications as stakeholders resist abandoning established IT systems
- Plans to extend domestic delivery to Singapore, Hong Kong, and Taiwan (flexible Uber-like model)
- Harmonising business processes for shipments
- Integrating fulfilment processes with shipping
- Defining reference processes for new domestic delivery
- Simplifying application landscape
- Management frustrated as growth strategy hampered by IT issues
- No reliable business and financial reports available, management by gut feeling"""

        logger.info(f"Using embedded case study: {len(self.case_study_content)} characters")

    def _load_prompts(self) -> None:
        """Load prompts from the prompts directory"""
        prompts_path = Path(self.config.prompts_dir)

        if not prompts_path.exists():
            logger.error(f"Prompts directory not found: {self.config.prompts_dir}")
            raise FileNotFoundError(f"Prompts directory not found: {self.config.prompts_dir}")

        # Define required prompt files
        required_prompts = [
            'case_study_validation_system.txt',
            'case_study_validation_human.txt',
            'literature_validation.txt',
            'description_generation.txt',
            'overlap_detection.txt',
            'case_study_validation_fallback.txt'
        ]

        # Load each prompt file
        for prompt_file in required_prompts:
            prompt_path = prompts_path / prompt_file
            if prompt_path.exists():
                try:
                    with open(prompt_path, 'r', encoding='utf-8') as f:
                        prompt_key = prompt_file.replace('.txt', '')
                        self.prompts[prompt_key] = f.read().strip()
                    logger.debug(f"Loaded prompt: {prompt_file}")
                except Exception as e:
                    logger.error(f"Failed to load prompt {prompt_file}: {e}")
                    raise
            else:
                logger.error(f"Required prompt file not found: {prompt_path}")
                raise FileNotFoundError(f"Required prompt file not found: {prompt_path}")

        logger.info(f"Loaded {len(self.prompts)} prompt templates from {self.config.prompts_dir}")

    def initialize_literature_db(self) -> None:
        """Initialize the Chroma vector store using LangChain."""
        logger.info("Initializing LangChain Chroma vector store...")

        #persist_directory = None
        persist_directory = self.config.chroma_persist_directory
        embedding_function = SentenceTransformerEmbeddings(model_name=self.config.embedding_model)

        # Check if a database already exists and we are not forcing a rebuild
        if Path(persist_directory).exists() and not getattr(self, 'force_rebuild', False):
            logger.info(f"Loading existing vector store from: {persist_directory}")
            self.vector_store = Chroma(
                persist_directory=persist_directory,
                embedding_function=embedding_function
            )
            logger.info("Vector store loaded successfully.")
            return

        # If we are rebuilding, clear the directory first
        if getattr(self, 'force_rebuild', False) and Path(persist_directory).exists():
            import shutil
            logger.info(f"Rebuilding database, clearing directory: {persist_directory}")
            shutil.rmtree(persist_directory)

        # If the database does not exist or a rebuild is forced, create it
        logger.info("No existing database found or rebuild forced. Creating new vector store...")

        # 1. Load all literature documents into memory
        all_docs_content = []
        for lit_path in tqdm(self.literature_paths, desc="Loading literature files"):
            content = self._load_literature_file(lit_path)
            if content:
                # We add metadata here to preserve the source
                from langchain_core.documents import Document
                doc = Document(page_content=content, metadata={"source": lit_path})
                all_docs_content.append(doc)

        if not all_docs_content:
            logger.error("No literature content could be loaded. Aborting vector store creation.")
            return

        # 2. Split the documents into chunks
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.config.chunk_size,
            chunk_overlap=self.config.chunk_overlap,
            separators=["\n\n", "\n", ". ", " ", ""]
        )
        chunks = text_splitter.split_documents(all_docs_content)
        logger.info(f"Split {len(all_docs_content)} documents into {len(chunks)} chunks.")

        # 3. Create the Chroma vector store from the chunks
        logger.info("Creating embeddings and persisting the vector store. This may take a while...")
        self.vector_store = Chroma.from_documents(
            documents=chunks,
            embedding=embedding_function,
            persist_directory=persist_directory
        )
        logger.info("Successfully created and persisted the vector store.")

    def _process_literature_documents(self) -> None:
        """Process and chunk literature documents for ChromaDB"""
        # Initialize text splitter
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.config.chunk_size,
            chunk_overlap=self.config.chunk_overlap,
            separators=["\n\n", "\n", ". ", " ", ""]
        )

        all_documents = []
        all_metadatas = []
        all_ids = []

        # Process each literature file
        for lit_path in tqdm(self.literature_paths, desc="Processing literature files"):
            try:
                # For this implementation, use embedded literature content
                # In production, you would load from actual files
                literature_content = self._load_literature_file(lit_path)

                if not literature_content:
                    continue

                # Split into chunks
                chunks = text_splitter.split_text(literature_content)

                # Prepare data for ChromaDB
                for i, chunk in enumerate(chunks):
                    doc_id = f"{Path(lit_path).stem}_chunk_{i}"

                    all_documents.append(chunk)
                    all_metadatas.append({
                        "source": lit_path,
                        "chunk_index": i,
                        "total_chunks": len(chunks)
                    })
                    all_ids.append(doc_id)

                logger.info(f"Processed {len(chunks)} chunks from {lit_path}")

            except Exception as e:
                logger.error(f"Error processing {lit_path}: {e}")
                continue

        # Add all documents to ChromaDB in batches
        if all_documents:
            batch_size = 100
            total_batches = (len(all_documents) + batch_size - 1) // batch_size

            logger.info(f"Adding {len(all_documents)} chunks to ChromaDB in {total_batches} batches...")

            for i in tqdm(range(0, len(all_documents), batch_size), desc="Adding to ChromaDB"):
                batch_end = min(i + batch_size, len(all_documents))

                self.literature_collection.add(
                    documents=all_documents[i:batch_end],
                    metadatas=all_metadatas[i:batch_end],
                    ids=all_ids[i:batch_end]
                )

            logger.info(f"Successfully added {len(all_documents)} chunks to ChromaDB")
        else:
            logger.warning("No documents were processed for ChromaDB")

    def _load_literature_file(self, file_path: str) -> str:
        """
        Load literature content from a file.
        This implementation handles PDF files.
        """
        logger.debug(f"Attempting to load literature from: {file_path}")
        path = Path(file_path)

        if not path.exists():
            logger.error(f"Literature file not found: {file_path}")
            return ""

        try:
            # This implementation now only handles PDFs
            if path.suffix.lower() == '.pdf':
                with open(path, 'rb') as f:
                    reader = pypdf.PdfReader(f)
                    content = ""
                    for page in reader.pages:
                        page_text = page.extract_text()
                        if page_text:
                            content += page_text + "\n"
                    logger.debug(f"Successfully read {len(reader.pages)} pages from {file_path}")
                    return content
            else:
                logger.warning(f"Unsupported literature file format: {path.suffix}. Skipping.")
                return ""
        except Exception as e:
            logger.error(f"Failed to read and parse {file_path}: {e}")
            return ""

    def load_capabilities(self, capabilities_file: str) -> None:
        """Load capabilities from JSON/Markdown file into memory"""
        logger.info(f"Loading capabilities from {capabilities_file}")

        file_path = Path(capabilities_file)
        if not file_path.exists():
            raise FileNotFoundError(f"Capabilities file not found: {capabilities_file}")

        # Read the file content
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Determine file type and parse accordingly
        if file_path.suffix.lower() == '.json':
            self._load_capabilities_from_json(content)
        elif file_path.suffix.lower() in ['.md', '.markdown']:
            self._load_capabilities_from_markdown(content)
        else:
            raise ValueError(f"Unsupported file format: {file_path.suffix}")

        logger.info(f"Loaded {len(self.capabilities)} capabilities")

        # Log summary
        l1_count = sum(1 for c in self.capabilities.values() if c.level == 1)
        l2_count = sum(1 for c in self.capabilities.values() if c.level == 2)
        logger.info(f"  - Level 1: {l1_count} capabilities")
        logger.info(f"  - Level 2: {l2_count} capabilities")

        for category in ['Core', 'Guiding', 'Enabling']:
            cat_count = sum(1 for c in self.capabilities.values()
                            if c.category == category and c.level == 1)
            logger.info(f"  - {category}: {cat_count} L1 capabilities")

    def _calculate_name_similarity(self, name1: str, name2: str) -> float:
        """Calculate simple name similarity between two capability names"""
        # Convert to lowercase and split into words
        words1 = set(name1.lower().split())
        words2 = set(name2.lower().split())

        # Remove common words
        common_words = {'management', 'and', 'the', 'of', 'for', '&'}
        words1 = words1 - common_words
        words2 = words2 - common_words

        # Calculate Jaccard similarity
        if not words1 or not words2:
            return 0.0

        intersection = len(words1 & words2)
        union = len(words1 | words2)

        return intersection / union if union > 0 else 0.0

    def _should_check_overlap(self, cap1: Capability, cap2: Capability) -> bool:
        """Determine if two capabilities should be checked for overlap"""
        # Same capability
        if cap1.id == cap2.id:
            return False

        # Different levels
        if cap1.level != cap2.level:
            return False

        # For L2 capabilities, only check if they have different parents
        if cap1.level == 2:
            if cap1.parent_id == cap2.parent_id:
                return False  # Same parent, likely complementary

        # Check name similarity as a pre-filter
        name_similarity = self._calculate_name_similarity(cap1.name, cap2.name)
        if name_similarity > 0.5:  # More than 50% word overlap
            return True

        # Always check capabilities in the same category
        if cap1.category == cap2.category:
            return True

        # Skip if categories are very different
        if (cap1.category == "Core" and cap2.category == "Enabling") or \
                (cap1.category == "Enabling" and cap2.category == "Core"):
            return False

        return True
        """
        Use vector similarity to find potentially overlapping capabilities
        This is more efficient for large capability sets

        Args:
            capability: The capability to compare
            threshold: Similarity threshold (0-1)

        Returns:
            List of (capability_id, capability, similarity_score) tuples
        """
        # This would use ChromaDB or similar for efficient similarity search
        # For now, returns empty list - can be implemented when needed
        # Would create a separate collection for capabilities and use embedding search
        return []

    def generate_overlap_report(self) -> Dict[str, List[Dict[str, str]]]:
        """Generate a comprehensive overlap report for all capabilities"""
        overlap_report = {}

        logger.info("Generating comprehensive overlap report...")

        # Check each capability
        for cap_id, capability in tqdm(self.capabilities.items(), desc="Checking overlaps"):
            if cap_id in self.validation_results:
                result = self.validation_results[cap_id]
                if result.overlaps:
                    overlap_report[cap_id] = result.overlaps

        # Summary statistics
        total_overlaps = sum(len(overlaps) for overlaps in overlap_report.values())
        duplicate_count = sum(
            1 for overlaps in overlap_report.values()
            for overlap in overlaps
            if overlap.get('overlap_type') == 'DUPLICATE'
        )

        logger.info(f"Overlap report complete: {len(overlap_report)} capabilities with overlaps")
        logger.info(f"Total overlaps: {total_overlaps}, Duplicates: {duplicate_count}")

        return overlap_report

    # ========================================================================
    # Validation Methods
    # ========================================================================

    def validate_against_case_study(self, capability: Capability) -> List[ValidationEvidence]:
        """
        Step 1: Check if the capability is justified based on the case study

        Args:
            capability: The capability to validate

        Returns:
            List of evidence from the case study supporting this capability
        """
        logger.info(f"Validating '{capability.name}' against case study...")

        # Load case study if not already loaded
        if self.case_study_content is None:
            self._load_case_study()

        # Create the prompt template
        prompt = ChatPromptTemplate.from_messages([
            ("system", self.prompts['case_study_validation_system']),
            ("human", self.prompts['case_study_validation_human'])
        ])

        # Set up the parser
        parser = PydanticOutputParser(pydantic_object=CaseStudyValidationOutput)

        # Update prompt to include format instructions
        prompt = ChatPromptTemplate.from_messages([
            ("system", self.prompts['case_study_validation_system'] + "\n\n{format_instructions}"),
            ("human", self.prompts['case_study_validation_human'])
        ])

        # Create the chain
        chain = prompt | self.llm | parser

        try:
            # Invoke the chain
            result = chain.invoke({
                "capability_name": capability.name,
                "capability_level": capability.level,
                "capability_category": capability.category,
                "capability_description": capability.description or "No description provided",
                "case_study": self.case_study_content[:8000],  # Limit context length
                "format_instructions": parser.get_format_instructions()
            })

            # Convert to ValidationEvidence objects
            evidences = []
            for item in result.evidence_items:
                evidence = ValidationEvidence(
                    source_type="case_study",
                    quote=item.quote,
                    reference=f"Case Study - {item.section}",
                    relevance_score=item.relevance_score
                )
                evidences.append(evidence)

            # Log summary
            logger.info(f"Found {len(evidences)} pieces of evidence for '{capability.name}'")
            if result.capability_justified:
                logger.info(f"Capability JUSTIFIED: {result.justification_summary[:100]}...")
            else:
                logger.warning(f"Capability NOT JUSTIFIED: {result.justification_summary[:100]}...")

            return evidences

        except Exception as e:
            logger.error(f"Error validating capability '{capability.name}': {e}")

            # Try a simpler approach as fallback
            try:
                logger.info("Attempting simplified validation...")
                simple_prompt = self.prompts['case_study_validation_fallback'].format(
                    capability_name=capability.name,
                    case_study_excerpt=self.case_study_content[:2000]
                )

                response = self.llm.invoke(simple_prompt)

                # Create a basic evidence entry
                evidence = ValidationEvidence(
                    source_type="case_study",
                    quote=f"Simplified analysis: {response.content[:200]}...",
                    reference="Case Study - General Analysis",
                    relevance_score=0.5
                )
                return [evidence]

            except Exception as e2:
                logger.error(f"Fallback validation also failed: {e2}")
                return []

    def validate_against_literature(self, capability: Capability) -> List[ValidationEvidence]:
        """
        Step 2: Check if the capability is justified based on literature using LangChain's RAG.
        """
        logger.info(f"Validating '{capability.name}' against literature...")

        if self.vector_store is None:
            logger.warning("Vector store not initialized. Initializing now...")
            self.initialize_literature_db()
            if self.vector_store is None:
                logger.error("Failed to initialize vector store. Cannot validate against literature.")
                return []

        try:
            query_text = f"{capability.name} {capability.category} business capability"
            if capability.description:
                query_text += f" {capability.description[:200]}"

            # Use LangChain's similarity search to get documents and their scores
            results = self.vector_store.similarity_search_with_relevance_scores(
                query=query_text,
                k=5  # Get top 5 most relevant chunks
            )

            if not results:
                logger.warning(f"No relevant literature found for '{capability.name}'")
                return []

            # Prepare context from retrieved chunks
            literature_context = "\n\n---\n\n".join([
                f"Source: {doc.metadata.get('source', 'Unknown')}\n{doc.page_content}"
                for doc, score in results
            ])

            # Use LLM to analyze the literature (this part remains mostly the same)
            prompt_text = self.prompts['literature_validation'].format(
                capability_name=capability.name,
                capability_category=capability.category,
                capability_level=capability.level,
                capability_description=capability.description or 'No description provided',
                literature_context=literature_context[:6000]
            )

            response = self.llm.invoke(prompt_text)
            content = response.content

            # Parse evidence from response (this part remains mostly the same)
            evidences = []
            evidence_blocks = content.split("EVIDENCE:")
            for block in evidence_blocks[1:]:
                try:
                    # Parsing logic is identical to the original
                    lines = block.strip().split('\n')
                    quote, source, relevance = "", "", 0.5
                    for line in lines:
                        line = line.strip()
                        if line.startswith("Quote:"): quote = line[6:].strip().strip('"')
                        elif line.startswith("Source:"): source = line[7:].strip()
                        elif line.startswith("Relevance:"): relevance = float(line[10:].strip())
                    if quote and source:
                        evidences.append(ValidationEvidence(
                            source_type="literature",
                            quote=quote[:500],
                            reference=f"Literature - {source}",
                            relevance_score=min(max(relevance, 0.0), 1.0)
                        ))
                except Exception as e:
                    logger.debug(f"Failed to parse evidence block: {e}")
                    continue

            logger.info(f"Found {len(evidences)} literature evidences for '{capability.name}'.")
            return evidences

        except Exception as e:
            logger.error(f"Error validating against literature: {e}")
            return [ValidationEvidence(source_type="literature", quote=f"Error: {e}", reference="Error", relevance_score=0.0)]

    def generate_overlap_recommendations(self) -> List[Dict[str, Any]]:
        """Generate actionable recommendations based on overlap analysis"""
        recommendations = []

        # Analyze all overlaps
        for cap_id, result in self.validation_results.items():
            if not result.overlaps:
                continue

            capability = self.capabilities[cap_id]

            # Group by overlap type
            duplicates = [o for o in result.overlaps if o.get('overlap_type') == 'DUPLICATE']
            major_overlaps = [o for o in result.overlaps if o.get('overlap_type') == 'MAJOR']

            # Generate recommendations
            if duplicates:
                recommendations.append({
                    'type': 'MERGE_DUPLICATE',
                    'priority': 'HIGH',
                    'capability': capability.name,
                    'duplicates': [d.get('capability_name') for d in duplicates],
                    'action': f"Merge '{capability.name}' with duplicate capabilities",
                    'details': duplicates[0].get('recommendation', '')
                })

            if major_overlaps:
                recommendations.append({
                    'type': 'CLARIFY_BOUNDARIES',
                    'priority': 'MEDIUM',
                    'capability': capability.name,
                    'overlapping_with': [o.get('capability_name') for o in major_overlaps],
                    'action': f"Clarify boundaries between '{capability.name}' and related capabilities",
                    'details': "Consider splitting or merging based on functional analysis"
                })

        # Sort by priority
        priority_order = {'HIGH': 0, 'MEDIUM': 1, 'LOW': 2}
        recommendations.sort(key=lambda x: priority_order.get(x['priority'], 3))

        return recommendations

    def _summarize_evidence_quality(self, evidences: List[ValidationEvidence]) -> Dict[str, Any]:
        """Helper method to summarize evidence quality metrics"""
        if not evidences:
            return {
                'count': 0,
                'avg_score': 0.0,
                'high_relevance_count': 0,
                'sources': []
            }

        scores = [e.relevance_score for e in evidences]
        sources = list(set(e.reference.split(' - ')[1] if ' - ' in e.reference else e.reference
                           for e in evidences))

        return {
            'count': len(evidences),
            'avg_score': sum(scores) / len(scores),
            'high_relevance_count': sum(1 for s in scores if s >= self.config.min_evidence_score),
            'sources': sources[:3]  # Top 3 unique sources
        }
    def generate_description(self,
                             capability: Capability,
                             case_study_evidences: List[ValidationEvidence],
                             literature_evidences: List[ValidationEvidence]) -> str:

        logger.info(f"Generating description for '{capability.name}'...")

        # Analyze evidence quality
        case_study_metrics = self._summarize_evidence_quality(case_study_evidences)
        literature_metrics = self._summarize_evidence_quality(literature_evidences)

        # Prepare case study evidence summary
        case_study_context = ""
        if case_study_evidences:
            case_study_context = "CASE STUDY EVIDENCE:\n"
            for i, evidence in enumerate(case_study_evidences[:5], 1):  # Limit to top 5
                case_study_context += f"\n{i}. Quote: \"{evidence.quote}\"\n"
                case_study_context += f"   Reference: {evidence.reference}\n"
                case_study_context += f"   Relevance: {evidence.relevance_score:.2f}\n"
        else:
            case_study_context = "CASE STUDY EVIDENCE: No specific evidence found\n"

        # Prepare literature evidence summary
        literature_context = ""
        if literature_evidences:
            literature_context = "\nLITERATURE EVIDENCE:\n"
            for i, evidence in enumerate(literature_evidences[:5], 1):  # Limit to top 5
                literature_context += f"\n{i}. Quote: \"{evidence.quote}\"\n"
                literature_context += f"   Reference: {evidence.reference}\n"
                literature_context += f"   Relevance: {evidence.relevance_score:.2f}\n"
        else:
            literature_context = "\nLITERATURE EVIDENCE: No specific evidence found\n"

        # Prepare the prompt
        # TODO: Use self.prompts['description_generation'] when prompts are externalized
        prompt_text = f"""Generate a comprehensive description for this business capability based on the evidence collected.

CAPABILITY DETAILS:
- Name: {capability.name}
- Level: {capability.level}
- Category: {capability.category} (Core = value-creating, Guiding = strategic/directing, Enabling = supporting)
- Current Description: {capability.description or 'None provided'}

EVIDENCE SUMMARY:
- Total evidence pieces: {len(case_study_evidences) + len(literature_evidences)}
- Case study evidence: {case_study_metrics['count']} pieces (avg relevance: {case_study_metrics['avg_score']:.2f})
- Literature evidence: {literature_metrics['count']} pieces (avg relevance: {literature_metrics['avg_score']:.2f})
- High-relevance evidence (>={self.config.min_evidence_score}): {case_study_metrics['high_relevance_count'] + literature_metrics['high_relevance_count']}

{case_study_context}
{literature_context}

REQUIREMENTS FOR THE DESCRIPTION:
1. Start with a clear definition of what this capability enables the organization to do
2. Explain the business value and why it's needed
3. Reference specific evidence from both case study and literature where relevant
4. Keep it concise but comprehensive (2-4 sentences)
5. Use business language, not technical jargon
6. Focus on the "what" not the "how"
7. Make it specific to SpeedParcel's logistics context
8. If evidence is limited, still provide a meaningful description based on the capability name and category

For a Level {capability.level} capability in the {capability.category} category, ensure the description:
- {"Directly relates to value creation and core business operations" if capability.category == "Core" else ""}
- {"Provides strategic direction and governance" if capability.category == "Guiding" else ""}
- {"Supports business operations without being unique to the business model" if capability.category == "Enabling" else ""}
- {"Is high-level and encompasses multiple related activities" if capability.level == 1 else "Is specific and focused on particular activities"}

Generate a professional description that would be suitable for an Enterprise Architecture document:"""

        try:
            # Get response from LLM
            response = self.llm.invoke(prompt_text)
            generated_description = response.content.strip()

            # Validate the description
            if len(generated_description) < 50:
                logger.warning(f"Generated description seems too short: {len(generated_description)} chars")
                # Add a fallback
                generated_description = f"{generated_description} This capability is essential for SpeedParcel's operations."
            elif len(generated_description) > 1000:
                logger.warning(f"Generated description is very long: {len(generated_description)} chars")
                # Optionally truncate or summarize

            # Log summary
            logger.info(f"Generated description of {len(generated_description)} characters")
            logger.info(f"Evidence used: {case_study_metrics['count']} case study, {literature_metrics['count']} literature")
            logger.debug(f"Description preview: {generated_description[:100]}...")

            return generated_description

        except Exception as e:
            logger.error(f"Error generating description for '{capability.name}': {e}")

            # Generate a basic fallback description
            fallback = f"{capability.name} represents the ability to "

            if capability.category == "Core":
                fallback += f"deliver essential {capability.name.lower().replace(' management', '')} services that directly create value for SpeedParcel's customers."
            elif capability.category == "Guiding":
                fallback += f"provide strategic direction and governance for {capability.name.lower().replace(' management', '')} across the organization."
            else:  # Enabling
                fallback += f"support business operations through effective {capability.name.lower().replace(' management', '')}."

            # Add evidence summary if available
            total_evidence = len(case_study_evidences) + len(literature_evidences)
            if total_evidence > 0:
                high_rel = sum(1 for e in case_study_evidences + literature_evidences
                               if e.relevance_score >= self.config.min_evidence_score)
                fallback += f" This capability is supported by {total_evidence} pieces of evidence"
                if high_rel > 0:
                    fallback += f" ({high_rel} with high relevance)"
                fallback += "."

            logger.warning(f"Using fallback description for '{capability.name}'")
            return fallback

    def _calculate_name_similarity(self, name1: str, name2: str) -> float:
        """Calculate simple name similarity between two capability names"""
        # Convert to lowercase and split into words
        words1 = set(name1.lower().split())
        words2 = set(name2.lower().split())

        # Remove common words
        common_words = {'management', 'and', 'the', 'of', 'for', '&'}
        words1 = words1 - common_words
        words2 = words2 - common_words

        # Calculate Jaccard similarity
        if not words1 or not words2:
            return 0.0

        intersection = len(words1 & words2)
        union = len(words1 | words2)

        return intersection / union if union > 0 else 0.0

    def _should_check_overlap(self, cap1: Capability, cap2: Capability) -> bool:
        """Determine if two capabilities should be checked for overlap"""
        # Same capability
        if cap1.id == cap2.id:
            return False

        # Different levels
        if cap1.level != cap2.level:
            return False

        # For L2 capabilities, only check if they have different parents
        if cap1.level == 2:
            if cap1.parent_id == cap2.parent_id:
                return False  # Same parent, likely complementary

        # Check name similarity as a pre-filter
        name_similarity = self._calculate_name_similarity(cap1.name, cap2.name)
        if name_similarity > 0.5:  # More than 50% word overlap
            return True

        # Always check capabilities in the same category
        if cap1.category == cap2.category:
            return True

        # Skip if categories are very different
        if (cap1.category == "Core" and cap2.category == "Enabling") or \
                (cap1.category == "Enabling" and cap2.category == "Core"):
            return False

        return True

    def check_capability_overlaps(self, capability: Capability) -> List[Dict[str, str]]:
        """
        Step 4: Compare capability against all others to detect overlaps

        This uses semantic similarity and LLM analysis to identify:
        - Duplicate capabilities with different names
        - Partially overlapping capabilities that should be merged
        - Parent-child relationships that aren't properly structured
        - Capabilities that should be split or combined

        The analysis considers:
        1. Name similarity (Jaccard similarity of words)
        2. Category alignment (Core/Guiding/Enabling)
        3. Functional scope overlap
        4. Level consistency (L1 vs L2)

        Overlap types:
        - DUPLICATE: 90%+ overlap (should be merged)
        - MAJOR: 60-90% overlap (boundaries need clarification)
        - MINOR: 30-60% overlap (some shared activities)
        - RELATED: 10-30% overlap (distinct but connected)

        Args:
            capability: The capability to check for overlaps

        Returns:
            List of overlap dictionaries containing:
            - capability_id: ID of overlapping capability
            - capability_name: Name of overlapping capability
            - overlap_type: DUPLICATE/MAJOR/MINOR
            - overlap_percentage: Estimated overlap %
            - explanation: Why they overlap
            - recommendation: Suggested action
        """
        logger.info(f"Checking overlaps for '{capability.name}'...")

        overlaps = []

        # Pre-filter capabilities to check
        capabilities_to_check = [
            (cap_id, cap) for cap_id, cap in self.capabilities.items()
            if self._should_check_overlap(capability, cap)
        ]

        # If no candidates, return empty list
        if not capabilities_to_check:
            logger.info("No potential overlapping capabilities found")
            return []

        logger.info(f"Checking {len(capabilities_to_check)} potential overlaps")

        # Sort by name similarity to check most likely overlaps first
        capabilities_to_check.sort(
            key=lambda x: self._calculate_name_similarity(capability.name, x[1].name),
            reverse=True
        )

        # Batch capabilities for efficiency
        batch_size = 5
        for i in range(0, len(capabilities_to_check), batch_size):
            batch = capabilities_to_check[i:i+batch_size]

            # Prepare comparison context
            comparison_context = "CAPABILITIES TO COMPARE:\n\n"

            for idx, (other_id, other_cap) in enumerate(batch, 1):
                comparison_context += f"{idx}. {other_cap.name}\n"
                comparison_context += f"   Category: {other_cap.category}\n"
                comparison_context += f"   Description: {other_cap.description or 'No description'}\n"
                if other_cap.parent_id and other_cap.parent_id in self.capabilities:
                    parent = self.capabilities[other_cap.parent_id]
                    comparison_context += f"   Parent: {parent.name}\n"
                comparison_context += "\n"

            # Use LLM to analyze overlaps
            # TODO: Use self.prompts['overlap_detection'] when prompts are externalized
            prompt_text = f"""Analyze these business capabilities to identify any overlaps with the target capability.

TARGET CAPABILITY:
- Name: {capability.name}
- Category: {capability.category}
- Level: {capability.level}
- Description: {capability.description or 'No description provided'}
{f'- Parent: {self.capabilities[capability.parent_id].name}' if capability.parent_id and capability.parent_id in self.capabilities else ''}

{comparison_context}

For each capability in the comparison list, determine if there is:
1. DUPLICATE: Same capability with different name (90%+ overlap)
2. MAJOR OVERLAP: Significant functional overlap (60-90% overlap)
3. MINOR OVERLAP: Some shared activities (30-60% overlap)
4. RELATED: Related but distinct (10-30% overlap)
5. NO OVERLAP: Completely different capabilities (<10% overlap)

Consider:
- Functional scope overlap
- Similar activities or outcomes
- Whether they could/should be merged
- Whether one encompasses the other

For each overlap found (DUPLICATE, MAJOR, or MINOR), provide:
OVERLAP:
Capability: [Name of overlapping capability]
Type: [DUPLICATE/MAJOR/MINOR]
Overlap Percentage: [Estimated %]
Explanation: [Why they overlap]
Recommendation: [Merge/Keep separate/Clarify boundaries]

Only report overlaps that are MINOR or greater."""

            try:
                # Get response from LLM
                response = self.llm.invoke(prompt_text)
                content = response.content

                # Parse overlaps from response
                overlap_blocks = content.split("OVERLAP:")

                for block in overlap_blocks[1:]:  # Skip first split
                    try:
                        lines = block.strip().split('\n')
                        overlap_info = {
                            'capability_id': '',
                            'capability_name': '',
                            'overlap_type': '',
                            'overlap_percentage': '',
                            'explanation': '',
                            'recommendation': ''
                        }

                        for line in lines:
                            line = line.strip()
                            if line.startswith("Capability:"):
                                cap_name = line[11:].strip()
                                # Find the capability ID by name
                                for other_id, other_cap in batch:
                                    if other_cap.name == cap_name:
                                        overlap_info['capability_id'] = other_id
                                        overlap_info['capability_name'] = cap_name
                                        break
                            elif line.startswith("Type:"):
                                overlap_info['overlap_type'] = line[5:].strip()
                            elif line.startswith("Overlap Percentage:"):
                                overlap_info['overlap_percentage'] = line[19:].strip()
                            elif line.startswith("Explanation:"):
                                overlap_info['explanation'] = line[12:].strip()
                            elif line.startswith("Recommendation:"):
                                overlap_info['recommendation'] = line[15:].strip()

                        # Only add if we found a valid overlap
                        if overlap_info['capability_name'] and overlap_info['overlap_type']:
                            overlaps.append(overlap_info)
                            logger.debug(f"Found {overlap_info['overlap_type']} overlap with {overlap_info['capability_name']}")

                    except Exception as e:
                        logger.debug(f"Failed to parse overlap block: {e}")
                        continue

            except Exception as e:
                logger.error(f"Error checking overlaps for batch: {e}")
                continue

        # Sort overlaps by severity (DUPLICATE > MAJOR > MINOR)
        overlap_priority = {'DUPLICATE': 0, 'MAJOR': 1, 'MINOR': 2}
        overlaps.sort(key=lambda x: overlap_priority.get(x.get('overlap_type', ''), 3))

        # Log summary
        if overlaps:
            logger.warning(f"Found {len(overlaps)} potential overlaps for '{capability.name}'")
            for overlap in overlaps:
                logger.info(f"  - {overlap['overlap_type']} overlap with {overlap['capability_name']}")
        else:
            logger.info(f"No significant overlaps found for '{capability.name}'")

        return overlaps

    # ========================================================================
    # Validation Orchestration
    # ========================================================================

    def validate_single_capability(self, capability_id: str) -> ValidationResult:
        """Validate a single capability through all steps"""
        capability = self.capabilities[capability_id]
        logger.info(f"Starting validation for capability: {capability.name}")

        # Check cache first
        cached_result = self._load_from_cache(capability_id)
        if cached_result:
            return cached_result

        # Create result object
        result = ValidationResult(capability_id=capability_id)

        try:
            # Step 1: Case study validation
            result.case_study_evidences = self.validate_against_case_study(capability)

            # Step 2: Literature validation
            result.literature_evidences = self.validate_against_literature(capability)

            # Step 3: Generate description
            result.generated_description = self.generate_description(
                capability,
                result.case_study_evidences,
                result.literature_evidences
            )

            # Update the capability's description if generation was successful
            if result.generated_description and len(result.generated_description) > 50:
                logger.info(f"Updated description for '{capability.name}'")
                # Optionally update the capability object (commented out to preserve original)
                # capability.description = result.generated_description

            # Step 4: Check overlaps
            result.overlaps = self.check_capability_overlaps(capability)

            # Determine validation status
            if result.case_study_evidences and result.literature_evidences:
                result.validation_status = "validated"
            else:
                result.validation_status = "needs_review"

            # Save to cache
            self._save_to_cache(capability_id, result)

            # Save intermediate results if configured
            if self.config.save_intermediate:
                self._save_intermediate_result(capability_id, result)

        except Exception as e:
            logger.error(f"Error validating {capability.name}: {str(e)}")
            result.validation_status = "error"

        return result

    def validate_all_capabilities(self) -> None:
        """Validate all loaded capabilities"""
        logger.info(f"Starting validation of {len(self.capabilities)} capabilities...")

        # Process L1 capabilities first, then L2
        l1_capabilities = [c for c in self.capabilities.values() if c.level == 1]
        l2_capabilities = [c for c in self.capabilities.values() if c.level == 2]

        # Count cached vs. new validations
        total_capabilities = len(l1_capabilities) + len(l2_capabilities)
        cached_count = sum(1 for c in self.capabilities.values()
                           if self._load_from_cache(c.id) is not None)
        new_validations = total_capabilities - cached_count

        logger.info(f"Found {cached_count} cached validations, processing {new_validations} new ones")

        # Validate L1 capabilities with progress bar
        logger.info("Validating Level 1 capabilities...")
        for capability in tqdm(l1_capabilities, desc="L1 Capabilities",
                               disable=logging.getLogger().level > logging.INFO):
            result = self.validate_single_capability(capability.id)
            self.validation_results[capability.id] = result

        # Validate L2 capabilities with progress bar
        logger.info("Validating Level 2 capabilities...")
        for capability in tqdm(l2_capabilities, desc="L2 Capabilities",
                               disable=logging.getLogger().level > logging.INFO):
            result = self.validate_single_capability(capability.id)
            self.validation_results[capability.id] = result

        logger.info("Validation complete!")

    def validate_batch(self, capability_ids: List[str]) -> Dict[str, ValidationResult]:
        """Validate a batch of capabilities in parallel (if supported)"""
        results = {}

        logger.info(f"Validating batch of {len(capability_ids)} capabilities")

        # For now, process sequentially, but this could be parallelized
        # with concurrent.futures or async processing
        for cap_id in capability_ids:
            results[cap_id] = self.validate_single_capability(cap_id)

        return results

    # ========================================================================
    # Cache Management
    # ========================================================================

    def _get_cache_key(self, capability_id: str) -> str:
        """Generate a cache key for a capability based on its content"""
        capability = self.capabilities[capability_id]
        content = f"{capability.name}_{capability.level}_{capability.category}_{capability.description}"
        return hashlib.md5(content.encode()).hexdigest()

    def _load_from_cache(self, capability_id: str) -> Optional[ValidationResult]:
        """Load validation result from cache if available and not expired"""
        if not self.config.enable_cache:
            return None

        cache_key = self._get_cache_key(capability_id)
        cache_file = self.cache_dir / f"{cache_key}.pkl"

        if cache_file.exists():
            try:
                with open(cache_file, 'rb') as f:
                    cached_data = pickle.load(f)

                # Check if cache is expired
                cached_time = datetime.fromisoformat(cached_data['timestamp'])
                age_days = (datetime.now() - cached_time).days

                if age_days < self.config.cache_expiry_days:
                    logger.info(f"Loading '{self.capabilities[capability_id].name}' from cache")
                    return cached_data['result']
                else:
                    logger.info(f"Cache expired for '{self.capabilities[capability_id].name}'")
                    cache_file.unlink()  # Remove expired cache
            except Exception as e:
                logger.warning(f"Failed to load cache for {capability_id}: {e}")

        return None

    def _save_to_cache(self, capability_id: str, result: ValidationResult) -> None:
        """Save validation result to cache"""
        if not self.config.enable_cache:
            return

        cache_key = self._get_cache_key(capability_id)
        cache_file = self.cache_dir / f"{cache_key}.pkl"

        try:
            with open(cache_file, 'wb') as f:
                pickle.dump({
                    'timestamp': datetime.now().isoformat(),
                    'result': result
                }, f)
            logger.debug(f"Cached validation result for '{self.capabilities[capability_id].name}'")
        except Exception as e:
            logger.warning(f"Failed to cache result for {capability_id}: {e}")

    def clear_cache(self) -> None:
        """Clear all cached validation results"""
        if self.cache_dir.exists():
            for cache_file in self.cache_dir.glob("*.pkl"):
                cache_file.unlink()
            logger.info("Cache cleared")

    def clear_literature_db(self) -> None:
        """Clear the literature database"""
        if self.literature_db and self.config.collection_name:
            try:
                self.literature_db.delete_collection(name=self.config.collection_name)
                logger.info(f"Cleared literature collection: {self.config.collection_name}")
            except Exception as e:
                logger.warning(f"Could not clear literature collection: {e}")

    # ========================================================================
    # File Parsing Methods
    # ========================================================================

    def _load_capabilities_from_markdown(self, content: str) -> None:
        """Parse capabilities from markdown format"""
        # Define patterns for parsing
        category_pattern = r'^#\s+(CORE|GUIDING|ENABLING)\s+CAPABILITIES'
        l1_pattern = r'^##\s+L1\s+(?:CAPABILITY|Capability):\s*(.+)$'
        l2_pattern = r'^###\s+(.+)$'

        # Parse line by line
        lines = content.split('\n')
        current_category = None
        current_l1_capability = None
        current_l2_capability = None
        current_section = None
        section_content = []

        i = 0
        while i < len(lines):
            line = lines[i].strip()

            # Check for category header
            category_match = re.match(category_pattern, line, re.IGNORECASE)
            if category_match:
                # Save any pending L2 capability
                if current_l2_capability and section_content:
                    self._parse_capability_metadata(current_l2_capability, section_content)
                    section_content = []

                current_category = category_match.group(1).title()  # Core, Guiding, or Enabling
                current_l1_capability = None
                current_l2_capability = None
                logger.debug(f"Found category: {current_category}")
                i += 1
                continue

            # Check for L1 capability
            l1_match = re.match(l1_pattern, line, re.IGNORECASE)
            if l1_match and current_category:
                # Save any pending L2 capability
                if current_l2_capability and section_content:
                    self._parse_capability_metadata(current_l2_capability, section_content)
                    section_content = []

                # Create L1 capability
                cap_name = l1_match.group(1).strip()
                cap_id = self._generate_capability_id(cap_name, 1)

                current_l1_capability = Capability(
                    id=cap_id,
                    name=cap_name,
                    level=1,
                    category=current_category,
                    parent_id=None,
                    l2_capabilities=[]
                )

                self.capabilities[cap_id] = current_l1_capability
                current_l2_capability = None
                section_content = []
                logger.debug(f"Found L1 capability: {cap_name}")
                i += 1
                continue

            # Check for L2 capability
            l2_match = re.match(l2_pattern, line)
            if l2_match and current_l1_capability and not line.startswith('####'):
                # Save any pending L2 capability
                if current_l2_capability and section_content:
                    self._parse_capability_metadata(current_l2_capability, section_content)
                    section_content = []

                # Create L2 capability
                cap_name = l2_match.group(1).strip()
                cap_id = self._generate_capability_id(cap_name, 2)

                current_l2_capability = Capability(
                    id=cap_id,
                    name=cap_name,
                    level=2,
                    category=current_category,
                    parent_id=current_l1_capability.id
                )

                self.capabilities[cap_id] = current_l2_capability
                current_l1_capability.l2_capabilities.append(cap_id)
                section_content = []
                logger.debug(f"Found L2 capability: {cap_name}")
                i += 1
                continue

            # Collect content for current capability
            if current_l1_capability or current_l2_capability:
                section_content.append(line)

            i += 1

        # Save any pending capability metadata
        if current_l2_capability and section_content:
            self._parse_capability_metadata(current_l2_capability, section_content)
        elif current_l1_capability and section_content and not current_l2_capability:
            self._parse_capability_metadata(current_l1_capability, section_content)

    def _load_capabilities_from_json(self, content: str) -> None:
        """Load capabilities from JSON format"""
        data = json.loads(content)

        # Handle different JSON structures
        if isinstance(data, list):
            # List of capabilities
            for cap_data in data:
                self._create_capability_from_dict(cap_data)
        elif isinstance(data, dict):
            # Nested structure or single capability
            if 'capabilities' in data:
                for cap_data in data['capabilities']:
                    self._create_capability_from_dict(cap_data)
            else:
                self._create_capability_from_dict(data)

    def _create_capability_from_dict(self, cap_data: Dict[str, Any]) -> None:
        """Create a Capability object from dictionary data"""
        capability = Capability(
            id=cap_data.get('id', self._generate_capability_id(cap_data['name'], cap_data.get('level', 1))),
            name=cap_data['name'],
            level=cap_data.get('level', 1),
            category=cap_data.get('category', 'Core'),
            parent_id=cap_data.get('parent_id'),
            description=cap_data.get('description'),
            l2_capabilities=cap_data.get('l2_capabilities', [])
        )

        self.capabilities[capability.id] = capability

    def _generate_capability_id(self, name: str, level: int) -> str:
        """Generate a unique ID for a capability"""
        # Create a readable ID from the name
        # Remove special characters and convert to lowercase with underscores
        clean_name = re.sub(r'[^\w\s-]', '', name)
        clean_name = re.sub(r'[-\s]+', '_', clean_name)
        base_id = f"L{level}_{clean_name.lower()}"

        # Ensure uniqueness
        if base_id not in self.capabilities:
            return base_id

        # Add counter if needed
        counter = 1
        while f"{base_id}_{counter}" in self.capabilities:
            counter += 1
        return f"{base_id}_{counter}"

    def _parse_capability_metadata(self, capability: Capability, content_lines: List[str]) -> None:
        """Parse metadata from capability content"""
        content = '\n'.join(content_lines)

        # Extract description
        desc_pattern = r'-\s*(?:Description|Overview):\s*(.+?)(?=\n-|\n###|\Z)'
        desc_match = re.search(desc_pattern, content, re.DOTALL | re.IGNORECASE)
        if desc_match:
            capability.description = desc_match.group(1).strip()

        # For L1 capabilities, also check for Justification and Supports
        if capability.level == 1:
            # Try to extract from bullet points
            just_pattern = r'-\s*\*\*Justification:\*\*\s*(.+?)(?=\n-|\n###|\Z)'
            just_match = re.search(just_pattern, content, re.DOTALL)
            if just_match:
                justification = just_match.group(1).strip()
                if not capability.description:
                    capability.description = f"Justification: {justification}"
                else:
                    capability.description = f"{capability.description}\n\nJustification: {justification}"

            # Extract Overview if present
            overview_pattern = r'-\s*\*\*Overview:\*\*\s*(.+?)(?=\n###|\Z)'
            overview_match = re.search(overview_pattern, content, re.DOTALL)
            if overview_match:
                overview = overview_match.group(1).strip()
                if capability.description:
                    capability.description = f"{capability.description}\n\nOverview: {overview}"
                else:
                    capability.description = overview

    def format_description_with_metadata(self, capability: Capability, result: ValidationResult) -> str:
        """Format the generated description with metadata about evidence quality"""
        description = result.generated_description or capability.description or "No description available"

        # Add evidence metadata as a suffix
        total_evidence = len(result.case_study_evidences) + len(result.literature_evidences)
        if total_evidence > 0:
            case_metrics = self._summarize_evidence_quality(result.case_study_evidences)
            lit_metrics = self._summarize_evidence_quality(result.literature_evidences)

            metadata = f"\n\n*Evidence: {total_evidence} pieces total"
            if case_metrics['count'] > 0:
                metadata += f" ({case_metrics['count']} case study, avg: {case_metrics['avg_score']:.1f})"
            if lit_metrics['count'] > 0:
                metadata += f" ({lit_metrics['count']} literature, avg: {lit_metrics['avg_score']:.1f})"
            metadata += "*"

            return description + metadata

        return description

    # ========================================================================
    # Output and Reporting Methods
    # ========================================================================

    def save_results(self, output_file: str) -> None:
        """Save validation results to file in configured format(s)"""
        logger.info(f"Saving results to {output_file}")

        output_path = Path(output_file)

        # Prepare results data
        results_data = {
            'metadata': {
                'timestamp': datetime.now().isoformat(),
                'total_capabilities': len(self.capabilities),
                'validation_summary': self.generate_validation_report()
            },
            'capabilities': {},
            'validation_results': {}
        }

        # Add capability and validation data
        for cap_id, capability in self.capabilities.items():
            results_data['capabilities'][cap_id] = {
                'name': capability.name,
                'level': capability.level,
                'category': capability.category,
                'parent_id': capability.parent_id,
                'description': capability.description,
                'l2_capabilities': capability.l2_capabilities
            }

            if cap_id in self.validation_results:
                result = self.validation_results[cap_id]
                results_data['validation_results'][cap_id] = {
                    'status': result.validation_status,
                    'timestamp': result.validation_timestamp,
                    'case_study_evidence_count': len(result.case_study_evidences),
                    'literature_evidence_count': len(result.literature_evidences),
                    'generated_description': result.generated_description,
                    'overlaps': result.overlaps,  # This now contains detailed overlap info
                    'evidences': {
                        'case_study': [
                            {
                                'quote': e.quote,
                                'reference': e.reference,
                                'score': e.relevance_score
                            } for e in result.case_study_evidences
                        ],
                        'literature': [
                            {
                                'quote': e.quote,
                                'reference': e.reference,
                                'score': e.relevance_score
                            } for e in result.literature_evidences
                        ]
                    }
                }

        # Save in requested format(s)
        if self.config.output_format in ['json', 'both']:
            json_path = output_path.with_suffix('.json')
            with open(json_path, 'w') as f:
                json.dump(results_data, f, indent=2)
            logger.info(f"JSON results saved to {json_path}")

        if self.config.output_format in ['markdown', 'both']:
            md_path = output_path.with_suffix('.md')
            self._save_markdown_report(md_path, results_data)
            logger.info(f"Markdown report saved to {md_path}")

    def _save_intermediate_result(self, capability_id: str, result: ValidationResult) -> None:
        """Save intermediate validation results"""
        intermediate_dir = Path("intermediate_results")
        intermediate_dir.mkdir(exist_ok=True)

        capability = self.capabilities[capability_id]
        filename = f"{capability.category}_{capability.name.replace(' ', '_')}.json"

        with open(intermediate_dir / filename, 'w') as f:
            json.dump({
                'capability': {
                    'id': capability.id,
                    'name': capability.name,
                    'level': capability.level,
                    'category': capability.category
                },
                'validation_result': {
                    'case_study_evidences': [
                        {
                            'quote': e.quote,
                            'reference': e.reference,
                            'score': e.relevance_score
                        } for e in result.case_study_evidences
                    ],
                    'literature_evidences': [
                        {
                            'quote': e.quote,
                            'reference': e.reference,
                            'score': e.relevance_score
                        } for e in result.literature_evidences
                    ],
                    'description': result.generated_description,
                    'overlaps': result.overlaps,
                    'status': result.validation_status,
                    'timestamp': result.validation_timestamp
                }
            }, f, indent=2)

    def _save_markdown_report(self, output_path: Path, results_data: Dict[str, Any]) -> None:
        """Generate and save a markdown report of validation results"""
        with open(output_path, 'w') as f:
            # Header
            f.write("# Business Capability Validation Report\n\n")
            f.write(f"Generated: {results_data['metadata']['timestamp']}\n\n")

            # Summary
            summary = results_data['metadata']['validation_summary']
            f.write("## Summary\n\n")
            f.write(f"- Total Capabilities: {summary['total_capabilities']}\n")
            f.write(f"- Validated: {summary['validated']}\n")
            f.write(f"- Needs Review: {summary['needs_review']}\n")
            f.write(f"- Errors: {summary['errors']}\n")
            f.write(f"- Capabilities with Overlaps: {summary['capabilities_with_overlaps']}\n")

            # Add overlap statistics if available
            if 'overlap_statistics' in summary:
                overlap_stats = summary['overlap_statistics']
                f.write(f"\n### Overlap Analysis\n")
                f.write(f"- Duplicate Capabilities: {overlap_stats.get('duplicate_pairs', 0)}\n")
                f.write(f"- Major Overlaps: {overlap_stats.get('major_overlaps', 0)}\n")
                f.write(f"- Minor Overlaps: {overlap_stats.get('minor_overlaps', 0)}\n")

            f.write("\n")

            # Detailed results by category
            for category in ['Core', 'Guiding', 'Enabling']:
                f.write(f"## {category} Capabilities\n\n")

                # L1 capabilities
                l1_caps = [(id, cap) for id, cap in results_data['capabilities'].items()
                           if cap['level'] == 1 and cap['category'] == category]

                for cap_id, cap in sorted(l1_caps, key=lambda x: x[1]['name']):
                    f.write(f"### {cap['name']}\n\n")

                    if cap_id in results_data['validation_results']:
                        result = results_data['validation_results'][cap_id]
                        f.write(f"**Status:** {result['status']}\n\n")

                        if result['generated_description']:
                            f.write(f"**Description:** {result['generated_description']}\n\n")
                        elif cap.get('description'):
                            f.write(f"**Original Description:** {cap['description']}\n\n")

                        if result['evidences']['case_study']:
                            f.write("**Case Study Evidence:**\n")
                            for e in result['evidences']['case_study'][:3]:  # Top 3
                                f.write(f"- \"{e['quote']}\" ({e['reference']})\n")
                            f.write("\n")

                        if result['evidences']['literature']:
                            f.write("**Literature Evidence:**\n")
                            for e in result['evidences']['literature'][:3]:  # Top 3
                                f.write(f"- {e['reference']}: \"{e['quote']}\"\n")
                            f.write("\n")



                    # L2 capabilities
                    l2_caps = [(id, cap2) for id, cap2 in results_data['capabilities'].items()
                               if cap2['parent_id'] == cap_id]

                    if l2_caps:
                        f.write("#### Level 2 Capabilities:\n")
                        for l2_id, l2_cap in sorted(l2_caps, key=lambda x: x[1]['name']):
                            f.write(f"- **{l2_cap['name']}**")
                            if l2_id in results_data['validation_results']:
                                l2_result = results_data['validation_results'][l2_id]
                                f.write(f" ({l2_result['status']})")
                            f.write("\n")
                        f.write("\n")

    def generate_validation_report(self) -> Dict:
        """Generate a summary report of validation results"""
        # Calculate overlap statistics
        overlap_stats = {
            'total_with_overlaps': 0,
            'duplicate_pairs': 0,
            'major_overlaps': 0,
            'minor_overlaps': 0
        }

        for result in self.validation_results.values():
            if result.overlaps:
                overlap_stats['total_with_overlaps'] += 1
                for overlap in result.overlaps:
                    overlap_type = overlap.get('overlap_type', '')
                    if overlap_type == 'DUPLICATE':
                        overlap_stats['duplicate_pairs'] += 1
                    elif overlap_type == 'MAJOR':
                        overlap_stats['major_overlaps'] += 1
                    elif overlap_type == 'MINOR':
                        overlap_stats['minor_overlaps'] += 1

        report = {
            "timestamp": datetime.now().isoformat(),
            "total_capabilities": len(self.capabilities),
            "validated": sum(1 for r in self.validation_results.values()
                             if r.validation_status == "validated"),
            "needs_review": sum(1 for r in self.validation_results.values()
                                if r.validation_status == "needs_review"),
            "errors": sum(1 for r in self.validation_results.values()
                          if r.validation_status == "error"),
            "capabilities_with_overlaps": overlap_stats['total_with_overlaps'],
            "overlap_statistics": overlap_stats,
            "by_category": self._get_category_statistics(),
            "by_level": self._get_level_statistics(),
            "evidence_quality": self._get_evidence_statistics()
        }
        return report

    def _get_category_statistics(self) -> Dict[str, Dict[str, int]]:
        """Get validation statistics by capability category"""
        stats = {}
        for category in ['Core', 'Guiding', 'Enabling']:
            category_caps = [cap_id for cap_id, cap in self.capabilities.items()
                             if cap.category == category]

            stats[category] = {
                'total': len(category_caps),
                'validated': sum(1 for cap_id in category_caps
                                 if cap_id in self.validation_results and
                                 self.validation_results[cap_id].validation_status == "validated"),
                'needs_review': sum(1 for cap_id in category_caps
                                    if cap_id in self.validation_results and
                                    self.validation_results[cap_id].validation_status == "needs_review")
            }
        return stats

    def _get_level_statistics(self) -> Dict[int, Dict[str, int]]:
        """Get validation statistics by capability level"""
        stats = {}
        for level in [1, 2]:
            level_caps = [cap_id for cap_id, cap in self.capabilities.items()
                          if cap.level == level]

            stats[level] = {
                'total': len(level_caps),
                'validated': sum(1 for cap_id in level_caps
                                 if cap_id in self.validation_results and
                                 self.validation_results[cap_id].validation_status == "validated"),
                'avg_case_study_evidence': self._calculate_avg_evidence(level_caps, 'case_study'),
                'avg_literature_evidence': self._calculate_avg_evidence(level_caps, 'literature')
            }
        return stats

    def _calculate_avg_evidence(self, capability_ids: List[str], evidence_type: str) -> float:
        """Calculate average number of evidence pieces for given capabilities"""
        total_evidence = 0
        count = 0

        for cap_id in capability_ids:
            if cap_id in self.validation_results:
                result = self.validation_results[cap_id]
                if evidence_type == 'case_study':
                    total_evidence += len(result.case_study_evidences)
                else:
                    total_evidence += len(result.literature_evidences)
                count += 1

        return total_evidence / count if count > 0 else 0

    def _get_evidence_statistics(self) -> Dict[str, Any]:
        """Get statistics about evidence quality"""
        all_scores = []
        no_case_study = 0
        no_literature = 0

        for result in self.validation_results.values():
            # Collect relevance scores
            for evidence in result.case_study_evidences + result.literature_evidences:
                all_scores.append(evidence.relevance_score)

            # Count missing evidence
            if not result.case_study_evidences:
                no_case_study += 1
            if not result.literature_evidences:
                no_literature += 1

        return {
            'avg_relevance_score': sum(all_scores) / len(all_scores) if all_scores else 0,
            'capabilities_without_case_study': no_case_study,
            'capabilities_without_literature': no_literature,
            'total_evidence_pieces': len(all_scores)
        }

    # ========================================================================
    # Utility Methods
    # ========================================================================

    def display_loaded_capabilities(self) -> None:
        """Display loaded capabilities for verification"""
        logger.info("=== LOADED CAPABILITIES ===")

        for category in ['Core', 'Guiding', 'Enabling']:
            print(f"\n{category.upper()} CAPABILITIES:")

            # Get L1 capabilities for this category
            l1_caps = [(id, cap) for id, cap in self.capabilities.items()
                       if cap.level == 1 and cap.category == category]

            for cap_id, cap in sorted(l1_caps, key=lambda x: x[1].name):
                print(f"\nL1: {cap.name}")
                if cap.description:
                    # Show first 100 chars of description
                    desc_preview = cap.description[:100] + "..." if len(cap.description) > 100 else cap.description
                    print(f"    Description: {desc_preview}")

                # Show L2 capabilities
                if cap.l2_capabilities:
                    print(f"    L2 Capabilities ({len(cap.l2_capabilities)}):")
                    for l2_id in cap.l2_capabilities:
                        if l2_id in self.capabilities:
                            l2_cap = self.capabilities[l2_id]
                            print(f"      - {l2_cap.name}")

    def export_overlap_analysis(self, output_file: str = "overlap_analysis.md") -> None:
        """Export detailed overlap analysis as a separate report"""
        logger.info(f"Exporting overlap analysis to {output_file}")

        with open(output_file, 'w') as f:
            f.write("# Business Capability Overlap Analysis\n\n")
            f.write(f"Generated: {datetime.now().isoformat()}\n\n")

            # Get recommendations
            recommendations = self.generate_overlap_recommendations()

            if recommendations:
                f.write("## Executive Summary\n\n")
                f.write(f"Found {len(recommendations)} recommendations for capability optimization:\n\n")

                # Group by priority
                high_priority = [r for r in recommendations if r['priority'] == 'HIGH']
                medium_priority = [r for r in recommendations if r['priority'] == 'MEDIUM']

                if high_priority:
                    f.write("### High Priority Actions\n")
                    for rec in high_priority:
                        f.write(f"- **{rec['action']}**\n")
                        if rec.get('details'):
                            f.write(f"  - {rec['details']}\n")
                    f.write("\n")

                if medium_priority:
                    f.write("### Medium Priority Actions\n")
                    for rec in medium_priority:
                        f.write(f"- {rec['action']}\n")
                    f.write("\n")

            # Detailed analysis by capability
            f.write("## Detailed Overlap Analysis\n\n")

            capabilities_with_overlaps = [
                (cap_id, cap, self.validation_results[cap_id])
                for cap_id, cap in self.capabilities.items()
                if cap_id in self.validation_results and self.validation_results[cap_id].overlaps
            ]

            # Sort by number of overlaps (descending)
            capabilities_with_overlaps.sort(
                key=lambda x: len(x[2].overlaps),
                reverse=True
            )

            for cap_id, capability, result in capabilities_with_overlaps:
                f.write(f"### {capability.name}\n")
                f.write(f"*Category: {capability.category}, Level: {capability.level}*\n\n")

                if capability.description:
                    f.write(f"**Description:** {capability.description}\n\n")

                f.write("**Overlaps:**\n")
                for overlap in result.overlaps:
                    f.write(f"\n- **{overlap.get('capability_name', 'Unknown')}**")
                    f.write(f" - {overlap.get('overlap_type', 'Unknown')}")
                    if overlap.get('overlap_percentage'):
                        f.write(f" ({overlap.get('overlap_percentage')})")
                    f.write("\n")

                    if overlap.get('explanation'):
                        f.write(f"  - *Explanation:* {overlap.get('explanation')}\n")
                    if overlap.get('recommendation'):
                        f.write(f"  - *Recommendation:* {overlap.get('recommendation')}\n")

                f.write("\n---\n\n")

            # Summary statistics
            f.write("## Summary Statistics\n\n")
            overlap_report = self.generate_overlap_report()
            f.write(f"- Total capabilities analyzed: {len(self.capabilities)}\n")
            f.write(f"- Capabilities with overlaps: {len(overlap_report)}\n")
            f.write(f"- Total overlap relationships: {sum(len(o) for o in overlap_report.values())}\n")

            # Overlap type breakdown
            overlap_types = {}
            for overlaps in overlap_report.values():
                for overlap in overlaps:
                    overlap_type = overlap.get('overlap_type', 'Unknown')
                    overlap_types[overlap_type] = overlap_types.get(overlap_type, 0) + 1

            f.write(f"\n**Overlap Types:**\n")
            for overlap_type, count in sorted(overlap_types.items()):
                f.write(f"- {overlap_type}: {count}\n")

        logger.info(f"Overlap analysis exported to {output_file}")

    def export_loaded_capabilities(self, output_file: str = "loaded_capabilities.json") -> None:
        """Export loaded capabilities to JSON for verification"""
        export_data = {
            "metadata": {
                "total_capabilities": len(self.capabilities),
                "l1_count": sum(1 for c in self.capabilities.values() if c.level == 1),
                "l2_count": sum(1 for c in self.capabilities.values() if c.level == 2),
                "categories": {}
            },
            "capabilities": []
        }

        # Calculate category statistics
        for category in ['Core', 'Guiding', 'Enabling']:
            cat_caps = [c for c in self.capabilities.values() if c.category == category]
            export_data["metadata"]["categories"][category] = {
                "total": len(cat_caps),
                "l1": sum(1 for c in cat_caps if c.level == 1),
                "l2": sum(1 for c in cat_caps if c.level == 2)
            }

        # Export capabilities
        for cap_id, cap in self.capabilities.items():
            export_data["capabilities"].append({
                "id": cap.id,
                "name": cap.name,
                "level": cap.level,
                "category": cap.category,
                "parent_id": cap.parent_id,
                "description": cap.description,
                "l2_capabilities": cap.l2_capabilities
            })

        # Sort by category, level, then name
        export_data["capabilities"].sort(
            key=lambda x: (x["category"], x["level"], x["name"])
        )

        # Save to file
        with open(output_file, 'w') as f:
            json.dump(export_data, f, indent=2)

        logger.info(f"Exported capabilities to {output_file}")


# ============================================================================
# Test Functions
# ============================================================================

def test_case_study_validation():
    """Test the case study validation functionality

    Usage: python capability_validator.py test-case-study

    This will:
    1. Load the SpeedParcel case study
    2. Test validation for sample capabilities
    3. Show extracted quotes and relevance scores
    """
    print("\n" + "="*80)
    print("TESTING CASE STUDY VALIDATION")
    print("="*80 + "\n")
    # Set up configuration
    config = ValidatorConfig(
        llm_model="claude-3-haiku-20240307",  # Using Haiku for cost-efficient testing
        temperature=0.1,
        max_tokens=2000
    )

    # Create validator
    validator = CapabilityValidator(
        case_study_path="case_study_speed_parcel.pdf",
        literature_paths=["test.pdf"],
        config=config
    )

    # Create test capabilities
    test_capabilities = [
        Capability(
            id="L1_shipment_lifecycle_management",
            name="Shipment Lifecycle Management",
            level=1,
            category="Core",
            description="Core capability managing the end-to-end journey of parcels - fundamental to all SpeedParcel services"
        ),
        Capability(
            id="L1_human_capital_management",
            name="Human Capital Management",
            level=1,
            category="Enabling",
            description="Enables workforce capabilities across all operations"
        )
    ]

    # Test validation for each capability
    for test_capability in test_capabilities:
        try:
            logger.info(f"\nTesting case study validation for: {test_capability.name}")
            evidences = validator.validate_against_case_study(test_capability)

            print(f"\n{'='*80}")
            print(f"Validation Results for: {test_capability.name}")
            print(f"{'='*80}")
            print(f"Found {len(evidences)} pieces of evidence\n")

            for i, evidence in enumerate(evidences, 1):
                print(f"Evidence {i}:")
                print(f"  Quote: \"{evidence.quote}\"")
                print(f"  Reference: {evidence.reference}")
                print(f"  Relevance Score: {evidence.relevance_score:.2f}")
                print()

        except Exception as e:
            logger.error(f"Test failed for {test_capability.name}: {e}")
            import traceback
            traceback.print_exc()

        except Exception as e:
            logger.error(f"Test failed for {test_capability.name}: {e}")
            import traceback
            traceback.print_exc()


def test_literature_validation():
    """Test the literature validation functionality

    Usage: python capability_validator.py test-literature

    This will:
    1. Initialize ChromaDB with sample literature
    2. Test validation for sample capabilities
    3. Show retrieved evidence and relevance scores
    """
    print("\n" + "="*80)
    print("TESTING LITERATURE VALIDATION")
    print("="*80 + "\n")
    # Set up configuration
    config = ValidatorConfig(
        llm_model="claude-3-haiku-20240307",
        temperature=0.1,
        max_tokens=2000,
        chunk_size=500,
        chunk_overlap=50
    )

    # Create validator
    validator = CapabilityValidator(
        case_study_path="case_study_speed_parcel.pdf",
        literature_paths=[
            "01_Intro_and_Purpose.pdf",
            "02_BusArch_1.pdf",
            "03_BusArch_2.pdf"
        ],
        config=config
    )

    logger.info(f"Using {len(validator.literature_paths)} literature sources")
    for path in validator.literature_paths:
        logger.info(f"  - {path}")

    # Initialize literature database
    try:
        logger.info("Initializing literature database...")
        validator.initialize_literature_db()

        # Test capabilities
        test_capabilities = [
            Capability(
                id="L1_service_portfolio_management",
                name="Service Portfolio Management",
                level=1,
                category="Guiding",
                description="Guides development and optimization of logistics service offerings"
            ),
            Capability(
                id="L1_financial_management",
                name="Financial Management",
                level=1,
                category="Enabling",
                description="Enables financial control, planning, and reporting"
            )
        ]

        # Test validation for each capability
        for test_capability in test_capabilities:
            logger.info(f"\nTesting literature validation for: {test_capability.name}")
            evidences = validator.validate_against_literature(test_capability)

            print(f"\n{'='*80}")
            print(f"Literature Validation Results for: {test_capability.name}")
            print(f"{'='*80}")
            print(f"Found {len(evidences)} pieces of evidence\n")

            for i, evidence in enumerate(evidences, 1):
                print(f"Evidence {i}:")
                print(f"  Quote: \"{evidence.quote}\"")
                print(f"  Reference: {evidence.reference}")
                print(f"  Relevance Score: {evidence.relevance_score:.2f}")
                print()
    except Exception as e:
        logger.error(f"An error occurred during literature validation test: {e}")
        print(f"An error occurred: {e}")
    finally:
        logger.info("Literature validation test finished.")
        print("\nLiterature validation test finished.\n" + "="*80)


def test_description_generation():
    """Test the description generation functionality

    Usage: python capability_validator.py test-description

    This will:
    1. Load a test capability
    2. Collect evidence from case study and literature
    3. Generate a comprehensive description
    4. Show the result
    """
    print("\n" + "="*80)
    print("TESTING DESCRIPTION GENERATION")
    print("="*80 + "\n")

    # Set up configuration
    config = ValidatorConfig(
        llm_model="claude-3-haiku-20240307",
        temperature=0.3,  # Slightly higher for more creative descriptions
        max_tokens=2000
    )

    # Create validator
    validator = CapabilityValidator(
        case_study_path="case_study_speed_parcel.pdf",
        literature_paths=[
            "01_Intro_and_Purpose.pdf",
            "02_BusArch_1.pdf",
            "03_BusArch_2.pdf"
        ],
        config=config
    )

    # Initialize literature database
    try:
        logger.info("Initializing literature database...")
        validator.initialize_literature_db()
    except Exception as e:
        logger.error(f"Failed to initialize literature database: {e}")
        return

    # Test capability
    test_capability = Capability(
        id="L1_transport_network_management",
        name="Transport Network Management",
        level=1,
        category="Core",
        description="Core capability for hub-to-hub movement and sorting operations"
    )

    try:
        # Step 1: Collect case study evidence
        logger.info("Collecting case study evidence...")
        case_study_evidences = validator.validate_against_case_study(test_capability)
        print(f"Found {len(case_study_evidences)} case study evidences")

        # Step 2: Collect literature evidence
        logger.info("Collecting literature evidence...")
        literature_evidences = validator.validate_against_literature(test_capability)
        print(f"Found {len(literature_evidences)} literature evidences")

        # Step 3: Generate description
        logger.info("Generating description...")
        generated_description = validator.generate_description(
            test_capability,
            case_study_evidences,
            literature_evidences
        )

        # Display results
        print(f"\n{'='*80}")
        print(f"CAPABILITY: {test_capability.name}")
        print(f"{'='*80}")
        print(f"\nOriginal Description:")
        print(f"  {test_capability.description}")

        print(f"\nGenerated Description:")
        print(f"  {generated_description}")

        print(f"\nEvidence Summary:")
        print(f"  - Case Study Evidence: {len(case_study_evidences)} pieces")
        if case_study_evidences:
            print(f"    Average relevance: {sum(e.relevance_score for e in case_study_evidences)/len(case_study_evidences):.2f}")

        print(f"  - Literature Evidence: {len(literature_evidences)} pieces")
        if literature_evidences:
            print(f"    Average relevance: {sum(e.relevance_score for e in literature_evidences)/len(literature_evidences):.2f}")

        print(f"\nDescription Statistics:")
        print(f"  - Length: {len(generated_description)} characters")
        print(f"  - Word count: {len(generated_description.split())} words")

        # Test another capability with different characteristics
        print(f"\n{'='*80}")
        print("Testing another capability (Enabling category)...")
        print(f"{'='*80}")

        test_capability2 = Capability(
            id="L1_performance_insights_management",
            name="Performance & Insights Management",
            level=1,
            category="Enabling",
            description="Enables data-driven decision making and continuous improvement"
        )

        # Collect evidence
        case_evidences2 = validator.validate_against_case_study(test_capability2)
        lit_evidences2 = validator.validate_against_literature(test_capability2)

        # Generate description
        description2 = validator.generate_description(
            test_capability2,
            case_evidences2,
            lit_evidences2
        )

        print(f"\nCAPABILITY: {test_capability2.name}")
        print(f"\nGenerated Description:")
        print(f"  {description2}")

    except Exception as e:
        logger.error(f"Test failed: {e}")
        import traceback
        traceback.print_exc()


def test_full_validation():
    """Test the complete validation workflow for a single capability

    Usage: python capability_validator.py test-full

    This demonstrates the complete validation process:
    1. Load capability
    2. Validate against case study
    3. Validate against literature
    4. Generate description from evidence
    5. Show complete results
    """
    print("\n" + "="*80)
    print("TESTING FULL VALIDATION WORKFLOW")
    print("="*80 + "\n")

    # Set up configuration
    config = ValidatorConfig(
        llm_model="claude-3-haiku-20240307",
        temperature=0.2,
        max_tokens=2000,
        save_intermediate=True
    )

    # Create validator
    validator = CapabilityValidator(
        case_study_path="case_study_speed_parcel.pdf",
        literature_paths=[
            "01_Intro_and_Purpose.pdf",
            "02_BusArch_1.pdf",
            "03_BusArch_2.pdf"
        ],
        config=config
    )

    # Initialize literature database
    try:
        logger.info("Initializing systems...")
        validator.initialize_literature_db()
    except Exception as e:
        logger.error(f"Failed to initialize: {e}")
        return

    # Test capability
    test_capability = Capability(
        id="L1_cross_border_trade_management",
        name="Cross-border Trade Management",
        level=1,
        category="Core",
        description="Core capability for international shipping complexities"
    )

    # Add to validator's capabilities for testing
    validator.capabilities[test_capability.id] = test_capability

    try:
        print(f"VALIDATING: {test_capability.name}")
        print(f"Category: {test_capability.category}")
        print(f"Level: {test_capability.level}")
        print(f"Original Description: {test_capability.description}\n")

        # Run full validation
        result = validator.validate_single_capability(test_capability.id)

        # Display comprehensive results
        print(f"\n{'='*80}")
        print("VALIDATION RESULTS")
        print(f"{'='*80}")

        print(f"\nStatus: {result.validation_status.upper()}")
        print(f"Timestamp: {result.validation_timestamp}")

        print(f"\nCASE STUDY EVIDENCE ({len(result.case_study_evidences)} pieces):")
        for i, evidence in enumerate(result.case_study_evidences[:3], 1):
            print(f"\n  Evidence {i}:")
            print(f"    Quote: \"{evidence.quote[:100]}...\"")
            print(f"    Reference: {evidence.reference}")
            print(f"    Relevance: {evidence.relevance_score:.2f}")

        print(f"\nLITERATURE EVIDENCE ({len(result.literature_evidences)} pieces):")
        for i, evidence in enumerate(result.literature_evidences[:3], 1):
            print(f"\n  Evidence {i}:")
            print(f"    Quote: \"{evidence.quote[:100]}...\"")
            print(f"    Reference: {evidence.reference}")
            print(f"    Relevance: {evidence.relevance_score:.2f}")

        print(f"\nGENERATED DESCRIPTION:")
        print(f"  {result.generated_description}")

        # Show evidence quality metrics
        all_scores = [e.relevance_score for e in result.case_study_evidences + result.literature_evidences]
        if all_scores:
            print(f"\nEVIDENCE QUALITY METRICS:")
            print(f"  - Average relevance score: {sum(all_scores)/len(all_scores):.2f}")
            print(f"  - High-relevance evidence (>={config.min_evidence_score}): {sum(1 for s in all_scores if s >= config.min_evidence_score)}")
            print(f"  - Total evidence pieces: {len(all_scores)}")

    except Exception as e:
        logger.error(f"Test failed: {e}")
        import traceback
        traceback.print_exc()


def test_load_capabilities():
    """Test function to verify capability loading"""
    # Create a simple test validator
    validator = CapabilityValidator(
        case_study_path="case_study_speed_parcel.pdf",
        literature_paths=["test.pdf"],
        config=ValidatorConfig()
    )

    # Try to load the capabilities
    try:
        # You can modify this path to your actual file
        validator.load_capabilities("Business_Capability_Map.md")

        # Display what was loaded
        validator.display_loaded_capabilities()

        # Show some statistics
        print(f"\n=== SUMMARY ===")
        print(f"Total capabilities loaded: {len(validator.capabilities)}")
        print(f"L1 capabilities: {sum(1 for c in validator.capabilities.values() if c.level == 1)}")
        print(f"L2 capabilities: {sum(1 for c in validator.capabilities.values() if c.level == 2)}")

        # Export for detailed inspection
        validator.export_loaded_capabilities("test_loaded_capabilities.json")

    except Exception as e:
        logger.error(f"Failed to load capabilities: {e}")
        import traceback
        traceback.print_exc()


def test_all():
    """Run all test functions in sequence

    Usage: python capability_validator.py test-all

    This runs all test functions to verify the complete system works.
    """
    print("\n" + "="*80)
    print("RUNNING ALL TESTS")
    print("="*80 + "\n")

    tests = [
        ("Capability Loading", test_load_capabilities),
        ("Case Study Validation", test_case_study_validation),
        ("Literature Validation", test_literature_validation),
        ("Description Generation", test_description_generation),
        #("Overlap Detection", test_overlap_detection),
        ("Full Validation Workflow", test_full_validation)
    ]

    results = []

    for test_name, test_func in tests:
        print(f"\n{'='*60}")
        print(f"Running: {test_name}")
        print(f"{'='*60}\n")

        try:
            if test_name == "Capability Loading":
                test_func()
            else:
                # Other tests need API key check
                pass  # API key already checked in main

            test_func()
            results.append((test_name, "PASSED"))
            print(f"\n✓ {test_name} PASSED")
        except Exception as e:
            results.append((test_name, f"FAILED: {str(e)}"))
            print(f"\n✗ {test_name} FAILED: {e}")
            import traceback
            traceback.print_exc()

        # Add delay between tests to avoid rate limits
        import time
        time.sleep(1)

    # Summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80 + "\n")

    passed = sum(1 for _, status in results if status == "PASSED")
    total = len(results)

    print(f"Tests passed: {passed}/{total}\n")

    for test_name, status in results:
        if status == "PASSED":
            print(f"✓ {test_name}")
        else:
            print(f"✗ {test_name}: {status}")

    return passed == total


# ============================================================================
# Main Function
# ============================================================================

def main():
    """Main workflow for capability validation"""
    import argparse

    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Validate Business Capabilities for SpeedParcel')
    parser.add_argument('--capabilities', type=str, default='Business_Capability_Map.md',
                        help='Path to capabilities file')
    parser.add_argument('--case-study', type=str, default='case_study_speed_parcel.pdf',
                        help='Path to case study file')
    parser.add_argument('--output', type=str, default='validation_results',
                        help='Output file path (without extension)')
    parser.add_argument('--clear-cache', action='store_true',
                        help='Clear validation cache before starting')
    parser.add_argument('--rebuild-literature', action='store_true',
                        help='Force rebuild of literature database')
    parser.add_argument('--no-cache', action='store_true',
                        help='Disable caching for this run')
    parser.add_argument('--format', choices=['json', 'markdown', 'both'], default='both',
                        help='Output format')
    parser.add_argument('--verbose', action='store_true',
                        help='Enable verbose logging')
    parser.add_argument('--test-load', action='store_true',
                        help='Only test loading capabilities without validation')
    parser.add_argument('--export-overlaps', action='store_true',
                        help='Export detailed overlap analysis after validation')
    parser.add_argument('--prompts', type=str, default=None,
                        help='Path to external prompts file (JSON format)')

    args = parser.parse_args()

    # Update logging level if verbose
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Configuration
    config = ValidatorConfig(
        enable_cache=not args.no_cache,
        output_format=args.format,
        save_intermediate=True
    )

    LITERATURE_PATHS = [
        "data/literature/A Literature Study on Benefits Provided by Enterprise Architecture Management.pdf",
        "data/literature/A Longitudinal View on the Perceived Contribution of Enterprise Architecture in the Netherlands.pdf",
        "data/literature/A Method for Developing Generic Capability Maps, A Design Science Study in the Professional Sport Industry.pdf",
        "data/literature/BIZBOK Guide AppendixA Glossary.pdf",
        "data/literature/BIZBOK Guide Introduction.pdf",
        "data/literature/Business Architecture Management, Architecting the Business for Consistency and Alignment.pdf",
        "data/literature/Essential Layers, Artifacts, and Dependencies of Enterprise Architecture.pdf",
        "data/literature/Masterclass Enterprise Architecture Management.pdf",
        "data/literature/Purpose of Enterprise Architecture Management Investigating Tangible Benefits in the German Logistics Industry.pdf",
        "data/literature/The Architect Elevator Connecting IT Levels.pdf",
        "data/literature/The Business Capability Map, The _Rosetta Stone_ of Business IT Alignment.pdf",
        "data/literature/Vorgehen bei der Einführung einer Organisation für das Management von Enterprise Systems.pdf",
    ]

    # Initialize validator
    validator = CapabilityValidator(args.case_study, LITERATURE_PATHS, config)

    # Set rebuild flag if requested
    if args.rebuild_literature:
        validator.force_rebuild = True

    # Check for API key before proceeding with validation
    if not args.test_load and not check_api_key():
        return

    # Clear cache if requested
    if args.clear_cache:
        validator.clear_cache()

    # Setup phase
    logger.info("=== SETUP PHASE ===")

    # Load capabilities
    try:
        validator.load_capabilities(args.capabilities)
    except Exception as e:
        logger.error(f"Failed to load capabilities: {e}")
        return

    # If test-load mode, just display and export
    if args.test_load:
        validator.display_loaded_capabilities()
        validator.export_loaded_capabilities("loaded_capabilities_test.json")
        logger.info("Test load complete. Check loaded_capabilities_test.json for details.")
        return

    # Continue with full validation
    validator.initialize_literature_db()

    # Validation phase
    logger.info("=== VALIDATION PHASE ===")
    validator.validate_all_capabilities()

    # Reporting phase
    logger.info("=== REPORTING PHASE ===")
    report = validator.generate_validation_report()
    logger.info(f"Validation Report: {json.dumps(report, indent=2)}")

    # Save results
    validator.save_results(args.output)

    # Export overlap analysis if requested
    if args.export_overlaps:
        validator.export_overlap_analysis(f"{args.output}_overlap_analysis.md")

    # Summary of issues needing attention
    logger.info("=== CAPABILITIES NEEDING ATTENTION ===")
    needs_attention = []
    overlap_issues = []

    for cap_id, result in validator.validation_results.items():
        issues = []

        # Check validation status
        if result.validation_status == "needs_review":
            if not result.case_study_evidences:
                issues.append("no case study evidence")
            if not result.literature_evidences:
                issues.append("no literature evidence")

        # Check for overlaps
        if result.overlaps:
            issues.append(f"{len(result.overlaps)} overlaps")
            overlap_issues.append((cap_id, validator.capabilities[cap_id].name, result.overlaps))

        if issues:
            needs_attention.append((cap_id, validator.capabilities[cap_id].name, issues))

    if needs_attention:
        logger.info(f"Found {len(needs_attention)} capabilities needing review:")
        for cap_id, cap_name, issues in needs_attention[:10]:  # Show first 10
            logger.info(f"  - {cap_name}: {', '.join(issues)}")

        if len(needs_attention) > 10:
            logger.info(f"  ... and {len(needs_attention) - 10} more")

    # Report significant overlaps
    if overlap_issues:
        logger.info(f"\nFound {len(overlap_issues)} capabilities with overlaps:")
        for cap_id, cap_name, overlaps in overlap_issues[:5]:  # Show first 5
            logger.info(f"  - {cap_name}:")
            for overlap in overlaps[:2]:  # Show first 2 overlaps per capability
                logger.info(f"    • {overlap.get('overlap_type', 'Unknown')} with {overlap.get('capability_name', 'Unknown')}")

    if not needs_attention and not overlap_issues:
        logger.info("All capabilities validated successfully with no issues!")

if __name__ == "__main__":
    load_dotenv(find_dotenv())
    main()
#if __name__ == "__main__":
#    # Check if running test mode
#    import sys
#    if len(sys.argv) > 1:
#        if sys.argv[1] == "test":
#            test_load_capabilities()
#        elif sys.argv[1] in ["test-case-study", "test-literature", "test-description",
#                             "test-full", "test-overlap", "test-all"]:
#            # Check for API key
#            if sys.argv[1] != "test" and not check_api_key():
#                sys.exit(1)
#
#            if sys.argv[1] == "test-case-study":
#                test_case_study_validation()
#            elif sys.argv[1] == "test-literature":
#                test_literature_validation()
#            elif sys.argv[1] == "test-description":
#                test_description_generation()
#            elif sys.argv[1] == "test-full":
#                test_full_validation()
#            #elif sys.argv[1] == "test-overlap":
#                #test_overlap_detection()
#            elif sys.argv[1] == "test-all":
#                success = test_all()
#                sys.exit(0 if success else 1)
#    else:
#        main()
