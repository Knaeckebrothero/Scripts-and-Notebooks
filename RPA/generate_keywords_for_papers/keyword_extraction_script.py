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
    keyword: str = Field(description="The keyword or search term it self.")
    synonym: str = Field(description="The keyword or search term this is a synonym for (can be the same as keyword for new keywords).")
    broad_or_related: bool = Field(description="Whether the keyword is a broad term or directly related to the synonym.")
    description: str = Field(description="A brief description of the keyword or search term.")

    # Convert the Keyword instance to a dictionary.
    def to_dict(self):
        return {
            "keyword": self.keyword,
            "synonym": self.synonym,
            "broad_or_related": self.broad_or_related,
            "description": self.description
        }


# Data model representing the structured tags for a document.
class PageKeywords(BaseModel):
    keywords: List[Keyword] = Field(description="List of relevant keywords for the document")

    # ConverPage instance to a dictionary.
    def to_dict(self):
        return {
            "keywords": [tag.to_dict() for tag in self.tags]
        }


# Declare and load variables
load_dotenv(find_dotenv())
model_name = "gpt-4o-mini"
keywords = []

# Initialize the model
llm_model = ChatOpenAI(
    model=model_name,
    temperature=0,
    seed=1234567890,
    n=1,
)

with open("prompt.txt", "r") as f:
    template = f.read()

# Initialize the output parser
parser = PydanticOutputParser(pydantic_oPage)

# Assemble the prompt
prompt = PromptTemplate(
    template=template,
    input_variables=["metadata", "page_content"],
    partial_variables={"format_instructions": parser.get_format_instructions()}
)

# Create the chain
chain = prompt | llm_model | parser

loader = PDFPlumberLoader("./papers/Neuro-SymbolicArtificialIntelligence.pdf")
docs = loader.load()

# TODO: Find a way to extract the metadata
metadata = ""

# Process the document and generate keywords for each page
for i, page_content in enumerate(docs):
    # Run the chain
    result = chain.invoke({"metadata": metadata, "page_content": page_content})

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

# TODO: Store the keywords in a database instead
