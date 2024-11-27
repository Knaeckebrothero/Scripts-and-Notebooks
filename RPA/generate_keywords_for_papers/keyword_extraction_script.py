"""
This script will generate search terms / keywords for a research paper.
"""
import json
from dotenv import load_dotenv, find_dotenv
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain.prompts import PromptTemplate
from langchain.output_parsers import PydanticOutputParser
from typing import List
from langchain_community.document_loaders import PDFPlumberLoader


# Data model representing a single keyword with a description.
class Keyword(BaseModel):
    keyword: str = Field(description="The keyword or search term it self")
    description: str = Field(description="A brief description of the keyword or search term")

    # Convert the Keyword instance to a dictionary.
    def to_dict(self):
        return {
            "keyword": self.keyword,
            "description": self.description
        }


# Data model representing the structured tags for a document.
class PageKeywords(BaseModel):
    keywords: List[Keyword] = Field(description="List of relevant keywords for the document")

    # Convert the PageKeywords instance to a dictionary.
    def to_dict(self):
        return {
            "tags": [tag.to_dict() for tag in self.tags]
        }


# Declare and load variables
load_dotenv(find_dotenv())
model_name = "gpt-4o-mini"
keywords = []

# Initialize the model
llm_model = ChatOpenAI(
    model=model_name,
    temperature=0,
    seed=42,
    n=1,
)

with open("prompt.txt", "r") as f:
    template = f.read()

# Initialize the output parser
parser = PydanticOutputParser(pydantic_object=PageKeywords)

# Assemble the prompt
prompt = PromptTemplate(
    template=template,
    input_variables=["page_content"],
    partial_variables={"format_instructions": parser.get_format_instructions()}
)

# Create the chain
chain = prompt | llm_model | parser

loader = PDFPlumberLoader("./papers/Neuro-SymbolicArtificialIntelligence.pdf")
docs = loader.load()

# Process the document and generate keywords for each page
for i, page_content in enumerate(docs):
    # Run the chain
    result = chain.invoke({"page_content": page_content})

    # Convert the result to a dictionary and append to keywords
    page_data = {
        "page": i + 1,
        "keywords": [keyword.to_dict() for keyword in result.keywords]
    }
    keywords.append(page_data)

    print(f"Keywords generated for page {i + 1}")

# Save the updated keywords to the file
try:
    with open("papers/keywords.json", "w", encoding='utf-8') as f:
        json.dump(keywords, f, indent=2, ensure_ascii=False)
    print("Keywords successfully saved to keywords.json")
except Exception as e:
    print(f"Error saving keywords: {str(e)}")
