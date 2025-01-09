from assessment.basic import InitialAssessment, TypeAssessment
from assessment.advanced import SummaryAssessment, TakeawayAssessment
from langchain.prompts import PromptTemplate
from langchain.output_parsers import PydanticOutputParser
import logging
from typing import Optional, Dict, Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def _load_prompt_template(file_path: str) -> str:
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

class PaperAssessment:
    def __init__(self, model, prompt_path):
        self.model = model
        self.prompt_path = prompt_path
        self._setup_parsers()

    def _setup_parsers(self):
        """Initialize all parsers and templates"""
        self.initial_parser = PydanticOutputParser(pydantic_object=InitialAssessment)
        self.type_parser = PydanticOutputParser(pydantic_object=TypeAssessment)
        self.summary_parser = PydanticOutputParser(pydantic_object=SummaryAssessment)
        self.takeaway_parser = PydanticOutputParser(pydantic_object=TakeawayAssessment)

        template = _load_prompt_template(self.prompt_path)

        self.initial_template = PromptTemplate(
            template=template,
            input_variables=["paper_content"],
            partial_variables={"format_instructions": self.initial_parser.get_format_instructions()}
        )
        self.type_template = PromptTemplate(
            template=template,
            input_variables=["paper_content"],
            partial_variables={"format_instructions": self.type_parser.get_format_instructions()}
        )
        self.summary_template = PromptTemplate(
            template=template,
            input_variables=["paper_content"],
            partial_variables={"format_instructions": self.summary_parser.get_format_instructions()}
        )
        self.takeaway_template = PromptTemplate(
            template=template,
            input_variables=["paper_content"],
            partial_variables={"format_instructions": self.takeaway_parser.get_format_instructions()}
        )

    def _get_llm_response(self, template: PromptTemplate, content: str) -> Optional[str]:
        """Get raw response from LLM"""
        try:
            prompt = template.format(paper_content=content)
            response = self.model.invoke(prompt)

            # Handle different response types
            if isinstance(response, str):
                return response
            elif isinstance(response, dict) and 'text' in response:
                return response['text']
            elif hasattr(response, 'content'):
                return response.content
            else:
                logger.error(f"Unexpected response type from LLM: {type(response)}")
                return None
        except Exception as e:
            logger.error(f"Error getting LLM response: {str(e)}")
            return None

    def _parse_response(self, response: Optional[str], parser: PydanticOutputParser, default_obj: Any) -> Any:
        """Parse LLM response with error handling"""
        if not response:
            return default_obj

        try:
            return parser.parse(response)
        except Exception as e:
            logger.error(f"Error parsing response: {str(e)}")
            return default_obj

    def _check_viability(self, content: str) -> InitialAssessment:
        """Check if paper is about neurosymbolic AI"""
        response = self._get_llm_response(self.initial_template, content)
        return self._parse_response(
            response,
            self.initial_parser,
            InitialAssessment(is_neurosymbolic=False, is_development=False)
        )

    def _assess_type(self, content: str) -> TypeAssessment:
        """Assess paper type"""
        response = self._get_llm_response(self.type_template, content)
        return self._parse_response(
            response,
            self.type_parser,
            TypeAssessment(paper_type="other")
        )

    def _summarize(self, content: str) -> SummaryAssessment:
        """Generate paper summary"""
        response = self._get_llm_response(self.summary_template, content)
        return self._parse_response(
            response,
            self.summary_parser,
            SummaryAssessment(paper_summary="Summary generation failed")
        )

    def _extract_takeaways(self, content: str) -> TakeawayAssessment:
        """Extract key takeaways"""
        response = self._get_llm_response(self.takeaway_template, content)
        return self._parse_response(
            response,
            self.takeaway_parser,
            TakeawayAssessment(takeaways="Takeaway extraction failed")
        )

    def assess_paper(self, content: str) -> Optional[Dict[str, Any]]:
        """Main method to assess a paper"""
        try:
            # Initial assessment
            initial_assessment = self._check_viability(content)
            if not initial_assessment.is_neurosymbolic:
                logger.info("Paper is not primarily about neurosymbolic AI")
                return None

            # Full assessment
            logger.info("Paper is about neurosymbolic AI, proceeding with full assessment")
            type_assessment = self._assess_type(content)
            summary_assessment = self._summarize(content)
            takeaway_assessment = self._extract_takeaways(content)

            return {
                "is_neurosymbolic": initial_assessment.is_neurosymbolic,
                "is_development": initial_assessment.is_development,
                "paper_type": type_assessment.paper_type,
                "summary": summary_assessment.paper_summary,
                "takeaways": takeaway_assessment.takeaways
            }

        except Exception as e:
            logger.error(f"Error in paper assessment: {str(e)}")
            return None
