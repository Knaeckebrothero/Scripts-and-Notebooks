"""
In this script i will test using langchain agents.
https://python.langchain.com/v0.1/docs/modules/agents/

pip install python-dotenv
pip install langchain
pip install langchain-community
pip install langchain-openai
pip install beautifulsoup4
pip install faiss-cpu
"""
import dotenv
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_community.document_loaders import WebBaseLoader
from langchain_community.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain.tools.retriever import create_retriever_tool
from langchain_openai import ChatOpenAI
from langchain.prompts import (ChatPromptTemplate, SystemMessagePromptTemplate,
                               HumanMessagePromptTemplate, MessagesPlaceholder,
                               PromptTemplate)
from langchain.agents import create_tool_calling_agent
from langchain.agents import AgentExecutor


def format_prompt(system_prompt: str) -> ChatPromptTemplate:
    """
    Formats the system prompt as a ChatPromptTemplate for a LangChain agent.

    :param system_prompt: The system prompt to format.
    :return: The prompt formatted as a ChatPromptTemplate.
    """
    # Create the system message prompt template
    system_message = SystemMessagePromptTemplate(
        prompt=PromptTemplate(input_variables=[], template=system_prompt)
    )

    # Create the human message prompt template
    human_message = HumanMessagePromptTemplate(
        prompt=PromptTemplate(input_variables=['input'], template='{input}')
    )

    # Create the chat prompt template
    chat_prompt_template = ChatPromptTemplate.from_messages([
        system_message, MessagesPlaceholder(variable_name='chat_history'),
        human_message, MessagesPlaceholder(variable_name='agent_scratchpad')]
    )

    return chat_prompt_template


# Load the environment variables
dotenv.load_dotenv()

# Create a search object
search = TavilySearchResults()
# print(search.invoke("what is the weather in SF"))


# Load some documents from the web and store them in a vector store
loader = WebBaseLoader("https://docs.smith.langchain.com/overview")
docs = loader.load()
documents = RecursiveCharacterTextSplitter(
    chunk_size=1000, chunk_overlap=200
).split_documents(docs)
vector = FAISS.from_documents(documents, OpenAIEmbeddings())
retriever = vector.as_retriever()
# print(retriever.invoke("how to upload a dataset")[0])

# Create a retriever tool
retriever_tool = create_retriever_tool(
    retriever,
    "langsmith_search",
    "Search for information about LangSmith. For any questions about LangSmith, you must use this tool!",
)

# All the parts of the agent
tools = [search, retriever_tool]
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
prompt = format_prompt("You are a helpful assistant tha can be asked anything about LangSmith.")

# Create the agent
agent = create_tool_calling_agent(llm, tools, prompt)

# Create an agent executor
agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

# Run the agent
result = agent_executor.invoke({"input": "hi!"})
print("\n\n\n", result)
