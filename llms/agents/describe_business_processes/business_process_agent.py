"""
Business Process Agent for SpeedParcel
Generates descriptions, KPIs, and academic references for business processes
Uses ChromaDB for literature search and LangChain for LLM integration
"""
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

import pypdf
from dotenv import load_dotenv, find_dotenv
from pydantic import BaseModel, Field
from tqdm import tqdm
from langchain_anthropic import ChatAnthropic
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_core.prompts import ChatPromptTemplate
from langchain.output_parsers import PydanticOutputParser
from langchain_community.vectorstores import Chroma
from langchain_community.embeddings.sentence_transformer import SentenceTransformerEmbeddings

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# ============================================================================
# Data Models
# ============================================================================

@dataclass
class BusinessProcess:
    """Represents a business process"""
    id: str
    name: str
    category: str  # Core, Supporting, Management
    department: Optional[str] = None
    description: Optional[str] = None
    input_description: Optional[str] = None  # What the process receives
    output_description: Optional[str] = None  # What the process produces


@dataclass
class ProcessKPI:
    """Key Performance Indicator for a process"""
    name: str
    abbreviation: str
    description: str
    objective: str
    formula: str
    unit: str
    target_value: str
    measuring_frequency: str
    evaluation_frequency: str
    data_sources: List[str] = field(default_factory=list)
    related_processes: List[str] = field(default_factory=list)


@dataclass
class LiteratureReference:
    """Academic reference from literature"""
    quote: str
    source: str
    relevance_score: float
    context: str


@dataclass
class ProcessAnalysisResult:
    """Complete analysis result for a business process"""
    process_id: str
    generated_description: str
    kpis: List[ProcessKPI] = field(default_factory=list)
    literature_references: List[LiteratureReference] = field(default_factory=list)
    analysis_timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


# Pydantic models for structured LLM output
class KPIOutput(BaseModel):
    """Structured KPI output from LLM"""
    name: str = Field(description="Full name of the KPI")
    abbreviation: str = Field(description="Short abbreviation (e.g., OTD)")
    description: str = Field(description="Brief description of what the KPI measures")
    objective: str = Field(description="Business objective this KPI supports")
    formula: str = Field(description="Calculation formula")
    unit: str = Field(description="Unit of measurement (%, days, count, etc.)")
    target_value: str = Field(description="Target or benchmark value")
    measuring_frequency: str = Field(description="How often data is collected")
    evaluation_frequency: str = Field(description="How often KPI is reviewed")
    data_sources: List[str] = Field(description="Data sources required")


class ProcessKPIGeneration(BaseModel):
    """Structured output for process KPI generation"""
    kpis: List[KPIOutput] = Field(description="List of relevant KPIs for the process")
    rationale: str = Field(description="Explanation of why these KPIs are relevant")


# ============================================================================
# Configuration
# ============================================================================

@dataclass
class AgentConfig:
    """Configuration for the Business Process Agent"""
    # LLM settings
    llm_model: str = "claude-3-haiku-20240307"
    temperature: float = 0.2
    max_tokens: int = 2000

    # ChromaDB settings
    chunk_size: int = 500
    chunk_overlap: int = 50
    embedding_model: str = "all-MiniLM-L6-v2"
    collection_name: str = "process_literature"
    chroma_persist_directory: str = ".chroma_db"

    # Analysis settings
    top_k_literature: int = 5  # Number of literature results to retrieve
    min_relevance_score: float = 0.7

    # Output settings
    output_format: str = "json"  # json or markdown
    save_intermediate: bool = True

    # Prompts settings
    prompts_dir: str = "./prompts"


# ============================================================================
# Main Agent Class
# ============================================================================

class BusinessProcessAgent:
    """Agent for analyzing business processes"""

    def __init__(self, literature_paths: List[str], config: Optional[AgentConfig] = None):
        self.literature_paths = literature_paths
        self.config = config or AgentConfig()
        self.processes: Dict[str, BusinessProcess] = {}
        self.analysis_results: Dict[str, ProcessAnalysisResult] = {}
        self.vector_store = None
        self.llm = None
        self.prompts = {}

        # Initialize components
        self._initialize_llm()
        self._load_prompts()

    def _initialize_llm(self) -> None:
        """Initialize the LLM"""
        try:
            self.llm = ChatAnthropic(
                model=self.config.llm_model,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens
            )
            logger.info(f"Initialized LLM: {self.config.llm_model}")
        except Exception as e:
            logger.error(f"Failed to initialize LLM: {e}")
            raise

    def _load_prompts(self) -> None:
        """Load prompts from JSON files"""
        prompts_path = Path(self.config.prompts_dir)

        if not prompts_path.exists():
            logger.error(f"Prompts directory not found: {self.config.prompts_dir}")
            raise FileNotFoundError(f"Prompts directory not found: {self.config.prompts_dir}")

        # Define required prompt files
        required_prompts = {
            'generate_process_description': 'generate_process_description.json',
            'generate_process_kpis': 'generate_process_kpis.json'
        }

        # Load each prompt file
        for prompt_key, filename in required_prompts.items():
            prompt_file = prompts_path / filename
            if not prompt_file.exists():
                logger.error(f"Required prompt file not found: {prompt_file}")
                raise FileNotFoundError(f"Required prompt file not found: {prompt_file}")

            try:
                with open(prompt_file, 'r', encoding='utf-8') as f:
                    self.prompts[prompt_key] = json.load(f)
                logger.debug(f"Loaded prompt: {filename}")
            except Exception as e:
                logger.error(f"Failed to load prompt {filename}: {e}")
                raise

        logger.info(f"Loaded {len(self.prompts)} prompt templates from {self.config.prompts_dir}")

    def initialize_literature_db(self) -> None:
        """Initialize or load the ChromaDB vector store"""
        logger.info("Initializing ChromaDB vector store...")

        persist_directory = self.config.chroma_persist_directory
        embedding_function = SentenceTransformerEmbeddings(model_name=self.config.embedding_model)

        # Check if database exists and not forcing rebuild
        if Path(persist_directory).exists() and not getattr(self, 'force_rebuild', False):
            logger.info(f"Loading existing vector store from: {persist_directory}")
            try:
                self.vector_store = Chroma(
                    persist_directory=persist_directory,
                    embedding_function=embedding_function,
                    collection_name=self.config.collection_name
                )
                logger.info("Vector store loaded successfully")
                return
            except Exception as e:
                logger.warning(f"Failed to load existing vector store: {e}")
                logger.info("Will create new vector store")

        # Create new database
        logger.info("Creating new vector store...")
        all_docs = []
        valid_paths = []

        # Check which literature files actually exist
        for lit_path in self.literature_paths:
            if Path(lit_path).exists():
                valid_paths.append(lit_path)
            else:
                logger.warning(f"Literature file not found: {lit_path}")

        if not valid_paths:
            logger.warning("No literature files found. Creating empty vector store.")
            logger.info("Please add PDF files to the configured paths or update the literature paths.")
        else:
            # Load valid PDFs
            for lit_path in tqdm(valid_paths, desc="Loading literature"):
                content = self._load_pdf(lit_path)
                if content:
                    from langchain_core.documents import Document
                    doc = Document(page_content=content, metadata={"source": lit_path})
                    all_docs.append(doc)

        # Create vector store even if empty
        if all_docs:
            # Split documents
            text_splitter = RecursiveCharacterTextSplitter(
                chunk_size=self.config.chunk_size,
                chunk_overlap=self.config.chunk_overlap
            )
            chunks = text_splitter.split_documents(all_docs)
            logger.info(f"Split {len(all_docs)} documents into {len(chunks)} chunks")

            # Create vector store with documents
            self.vector_store = Chroma.from_documents(
                documents=chunks,
                embedding=embedding_function,
                persist_directory=persist_directory,
                collection_name=self.config.collection_name
            )
        else:
            # Create empty vector store
            self.vector_store = Chroma(
                persist_directory=persist_directory,
                embedding_function=embedding_function,
                collection_name=self.config.collection_name
            )
            logger.warning("Created empty vector store. No documents were loaded.")

        logger.info("Vector store initialization complete")

    def _load_pdf(self, file_path: str) -> str:
        """Load content from PDF file"""
        try:
            path = Path(file_path)
            if not path.exists():
                logger.error(f"File not found: {file_path}")
                return ""

            with open(path, 'rb') as f:
                reader = pypdf.PdfReader(f)
                content = ""
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        content += page_text + "\n"
                return content
        except Exception as e:
            logger.error(f"Failed to read PDF {file_path}: {e}")
            return ""

    def load_processes(self, processes_file: str) -> None:
        """Load business processes from JSON file"""
        logger.info(f"Loading processes from {processes_file}")

        with open(processes_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Handle different JSON structures
        if isinstance(data, list):
            processes_data = data
        elif isinstance(data, dict) and 'processes' in data:
            processes_data = data['processes']
        else:
            processes_data = [data]

        # Create process objects
        for proc_data in processes_data:
            process = BusinessProcess(
                id=proc_data.get('id', proc_data['name'].lower().replace(' ', '_')),
                name=proc_data['name'],
                category=proc_data.get('category', 'Core'),
                department=proc_data.get('department'),
                description=proc_data.get('description'),
                input_description=proc_data.get('input'),
                output_description=proc_data.get('output')
            )
            self.processes[process.id] = process

        logger.info(f"Loaded {len(self.processes)} processes")

    def generate_process_description(self, process: BusinessProcess) -> str:
        """Generate a comprehensive description for a business process"""
        logger.info(f"Generating description for process: {process.name}")

        prompt_config = self.prompts['generate_process_description']

        prompt = ChatPromptTemplate.from_messages([
            ("system", prompt_config['system']),
            ("human", prompt_config['human'])
        ])

        try:
            response = self.llm.invoke(prompt.format(
                process_name=process.name,
                category=process.category,
                department=process.department or "Not specified",
                current_description=process.description or "None provided",
                input_desc=process.input_description or "Not specified",
                output_desc=process.output_description or "Not specified"
            ))

            return response.content.strip()
        except Exception as e:
            logger.error(f"Error generating description: {e}")
            return f"Error generating description for {process.name}"

    def generate_process_kpis(self, process: BusinessProcess, description: str) -> List[ProcessKPI]:
        """Generate relevant KPIs for a business process"""
        logger.info(f"Generating KPIs for process: {process.name}")

        prompt_config = self.prompts['generate_process_kpis']

        # Set up structured output parser
        parser = PydanticOutputParser(pydantic_object=ProcessKPIGeneration)

        prompt = ChatPromptTemplate.from_messages([
            ("system", prompt_config['system']),
            ("human", prompt_config['human'])
        ])

        try:
            response = self.llm.invoke(prompt.format(
                process_name=process.name,
                category=process.category,
                description=description,
                department=process.department or "Not specified",
                format_instructions=parser.get_format_instructions()
            ))

            # Parse the structured output
            result = parser.parse(response.content)

            # Convert to ProcessKPI objects
            kpis = []
            for kpi_data in result.kpis:
                kpi = ProcessKPI(
                    name=kpi_data.name,
                    abbreviation=kpi_data.abbreviation,
                    description=kpi_data.description,
                    objective=kpi_data.objective,
                    formula=kpi_data.formula,
                    unit=kpi_data.unit,
                    target_value=kpi_data.target_value,
                    measuring_frequency=kpi_data.measuring_frequency,
                    evaluation_frequency=kpi_data.evaluation_frequency,
                    data_sources=kpi_data.data_sources,
                    related_processes=[process.name]
                )
                kpis.append(kpi)

            logger.info(f"Generated {len(kpis)} KPIs for {process.name}")
            return kpis

        except Exception as e:
            logger.error(f"Error generating KPIs: {e}")
            # Return a default KPI as fallback
            return [ProcessKPI(
                name=f"{process.name} Efficiency",
                abbreviation="EFF",
                description=f"Measures efficiency of {process.name}",
                objective="Improve process efficiency",
                formula="Actual output / Expected output",
                unit="%",
                target_value="95%",
                measuring_frequency="Daily",
                evaluation_frequency="Weekly",
                data_sources=["Process logs"],
                related_processes=[process.name]
            )]

    def search_literature_references(self, process: BusinessProcess, description: str) -> List[LiteratureReference]:
        """Search for academic references related to the process"""
        logger.info(f"Searching literature for process: {process.name}")

        if self.vector_store is None:
            logger.warning("Vector store not initialized")
            return []

        try:
            # Create search query
            query = f"{process.name} {process.category} business process logistics"
            if description:
                query += f" {description[:200]}"

            # Search vector store
            results = self.vector_store.similarity_search_with_relevance_scores(
                query=query,
                k=self.config.top_k_literature
            )

            if not results:
                logger.debug(f"No literature found for {process.name}")
                return []

            # Process results
            references = []
            for doc, score in results:
                if score >= self.config.min_relevance_score:
                    ref = LiteratureReference(
                        quote=doc.page_content[:300] + "...",
                        source=Path(doc.metadata.get('source', 'Unknown')).stem,
                        relevance_score=score,
                        context=f"Related to {process.name}"
                    )
                    references.append(ref)

            if references:
                logger.info(f"Found {len(references)} relevant literature references")
            else:
                logger.debug(f"No references met the minimum relevance score of {self.config.min_relevance_score}")

            return references

        except Exception as e:
            logger.error(f"Error searching literature: {e}")
            return []

    def analyze_process(self, process_id: str) -> ProcessAnalysisResult:
        """Analyze a single business process"""
        process = self.processes[process_id]
        logger.info(f"Analyzing process: {process.name}")

        # Generate description
        description = self.generate_process_description(process)

        # Generate KPIs
        kpis = self.generate_process_kpis(process, description)

        # Search literature
        references = self.search_literature_references(process, description)

        # Create result
        result = ProcessAnalysisResult(
            process_id=process_id,
            generated_description=description,
            kpis=kpis,
            literature_references=references
        )

        # Save result
        self.analysis_results[process_id] = result

        # Save intermediate if configured
        if self.config.save_intermediate:
            self._save_intermediate_result(process, result)

        return result

    def analyze_all_processes(self) -> None:
        """Analyze all loaded processes"""
        logger.info(f"Starting analysis of {len(self.processes)} processes")

        for process_id in tqdm(self.processes.keys(), desc="Analyzing processes"):
            self.analyze_process(process_id)

        logger.info("Analysis complete!")

    def _save_intermediate_result(self, process: BusinessProcess, result: ProcessAnalysisResult) -> None:
        """Save intermediate results for a process"""
        output_dir = Path("process_analysis_results")
        output_dir.mkdir(exist_ok=True)

        filename = f"{process.category}_{process.name.replace(' ', '_')}.json"

        with open(output_dir / filename, 'w') as f:
            json.dump({
                'process': {
                    'id': process.id,
                    'name': process.name,
                    'category': process.category,
                    'department': process.department
                },
                'analysis': {
                    'description': result.generated_description,
                    'kpis': [
                        {
                            'name': kpi.name,
                            'abbreviation': kpi.abbreviation,
                            'description': kpi.description,
                            'objective': kpi.objective,
                            'formula': kpi.formula,
                            'unit': kpi.unit,
                            'target_value': kpi.target_value,
                            'measuring_frequency': kpi.measuring_frequency,
                            'evaluation_frequency': kpi.evaluation_frequency,
                            'data_sources': kpi.data_sources
                        } for kpi in result.kpis
                    ],
                    'literature_references': [
                        {
                            'quote': ref.quote,
                            'source': ref.source,
                            'relevance_score': ref.relevance_score
                        } for ref in result.literature_references
                    ],
                    'timestamp': result.analysis_timestamp
                }
            }, f, indent=2)

    def save_results(self, output_file: str) -> None:
        """Save all analysis results"""
        logger.info(f"Saving results to {output_file}")

        output_path = Path(output_file)

        # Prepare results data
        results_data = {
            'metadata': {
                'timestamp': datetime.now().isoformat(),
                'total_processes': len(self.processes),
                'analyzed_processes': len(self.analysis_results)
            },
            'processes': {},
            'analysis_results': {}
        }

        # Add process and analysis data
        for proc_id, process in self.processes.items():
            results_data['processes'][proc_id] = {
                'name': process.name,
                'category': process.category,
                'department': process.department,
                'original_description': process.description
            }

            if proc_id in self.analysis_results:
                result = self.analysis_results[proc_id]
                results_data['analysis_results'][proc_id] = {
                    'generated_description': result.generated_description,
                    'kpis': [
                        {
                            'name': kpi.name,
                            'abbreviation': kpi.abbreviation,
                            'formula': kpi.formula,
                            'target_value': kpi.target_value,
                            'unit': kpi.unit
                        } for kpi in result.kpis
                    ],
                    'literature_references': [
                        {
                            'source': ref.source,
                            'relevance_score': ref.relevance_score,
                            'quote_preview': ref.quote[:100] + "..."
                        } for ref in result.literature_references
                    ],
                    'timestamp': result.analysis_timestamp
                }

        # Save based on format
        if self.config.output_format == 'json':
            with open(output_path.with_suffix('.json'), 'w') as f:
                json.dump(results_data, f, indent=2)
        else:  # markdown
            self._save_markdown_report(output_path.with_suffix('.md'), results_data)

    def _save_markdown_report(self, output_path: Path, results_data: Dict[str, Any]) -> None:
        """Save results as markdown report"""
        with open(output_path, 'w') as f:
            f.write("# Business Process Analysis Report\n\n")
            f.write(f"Generated: {results_data['metadata']['timestamp']}\n\n")
            f.write(f"Total Processes: {results_data['metadata']['total_processes']}\n")
            f.write(f"Analyzed: {results_data['metadata']['analyzed_processes']}\n\n")

            # Group by category
            for category in ['Core', 'Supporting', 'Management']:
                f.write(f"## {category} Processes\n\n")

                category_processes = [
                    (pid, p) for pid, p in results_data['processes'].items()
                    if p['category'] == category
                ]

                for proc_id, process in sorted(category_processes, key=lambda x: x[1]['name']):
                    f.write(f"### {process['name']}\n\n")

                    if proc_id in results_data['analysis_results']:
                        analysis = results_data['analysis_results'][proc_id]

                        f.write(f"**Description:** {analysis['generated_description']}\n\n")

                        if analysis['kpis']:
                            f.write("**Key Performance Indicators:**\n\n")
                            for kpi in analysis['kpis']:
                                f.write(f"- **{kpi['name']} ({kpi['abbreviation']})**\n")
                                f.write(f"  - Formula: {kpi['formula']}\n")
                                f.write(f"  - Target: {kpi['target_value']} {kpi['unit']}\n")
                            f.write("\n")

                        if analysis['literature_references']:
                            f.write("**Academic References:**\n\n")
                            for ref in analysis['literature_references'][:3]:  # Top 3
                                f.write(f"- {ref['source']} (relevance: {ref['relevance_score']:.2f})\n")
                            f.write("\n")

                    f.write("---\n\n")


# ============================================================================
# Main Function
# ============================================================================

def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description='Analyze Business Processes')
    parser.add_argument('--processes', type=str, required=True,
                        help='Path to processes JSON file')
    parser.add_argument('--output', type=str, default='process_analysis',
                        help='Output file path (without extension)')
    parser.add_argument('--format', choices=['json', 'markdown'], default='markdown',
                        help='Output format')
    parser.add_argument('--rebuild-db', action='store_true',
                        help='Force rebuild of literature database')
    parser.add_argument('--literature-dir', type=str, default='data/literature',
                        help='Directory containing literature PDF files')

    args = parser.parse_args()

    # Check API key
    if not os.environ.get('ANTHROPIC_API_KEY'):
        logger.error("Please set ANTHROPIC_API_KEY environment variable")
        return

    # Find literature files
    literature_dir = Path(args.literature_dir)
    LITERATURE_PATHS = []

    if literature_dir.exists():
        # Find all PDF files in the directory
        LITERATURE_PATHS = [str(p) for p in literature_dir.glob('*.pdf')]
        logger.info(f"Found {len(LITERATURE_PATHS)} PDF files in {literature_dir}")
    else:
        logger.warning(f"Literature directory not found: {literature_dir}")
        logger.info("Creating empty literature directory...")
        literature_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Add PDF files to this directory and run with --rebuild-db")

    # Configuration
    config = AgentConfig(
        output_format=args.format,
        save_intermediate=True
    )

    # Create agent
    agent = BusinessProcessAgent(LITERATURE_PATHS, config)

    # Set rebuild flag if specified
    if args.rebuild_db:
        agent.force_rebuild = True
        if Path(config.chroma_persist_directory).exists():
            import shutil
            logger.info(f"Removing existing database: {config.chroma_persist_directory}")
            shutil.rmtree(config.chroma_persist_directory)

    # Initialize literature database
    agent.initialize_literature_db()

    # Load processes
    agent.load_processes(args.processes)

    # Analyze all processes
    agent.analyze_all_processes()

    # Save results
    agent.save_results(args.output)

    logger.info(f"Analysis complete! Results saved to {args.output}.{args.format}")


# Example usage
if __name__ == "__main__":
    load_dotenv(find_dotenv())
    main()
