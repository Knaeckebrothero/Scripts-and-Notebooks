"""
In this script i will test using langchain agents.
https://python.langchain.com/v0.1/docs/modules/agents/

pip install python-dotenv
pip install langchain
pip install langchain-community
pip install langchain-openai
pip install beautifulsoup4
pip install faiss-cpu

pip install -U langchain-chroma
"""
import os
import dotenv
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_community.document_loaders import WebBaseLoader
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain.tools.retriever import create_retriever_tool
from langchain_openai import ChatOpenAI
from langchain.prompts import ChatPromptTemplate
from langchain.agents import create_tool_calling_agent
from langchain.agents import AgentExecutor
from langchain.tools import Tool


def format_prompt_simple(system_prompt: str) -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("placeholder", "{chat_history}"),
        ("human", "{input}"),
        ("placeholder", "{agent_scratchpad}"),
    ])


# Load the environment variables
dotenv.load_dotenv()

# Set a custom User-Agent
os.environ['USER_AGENT'] = 'MyCustomAgent/1.0'

# Initialize Chroma DB
embeddings = OpenAIEmbeddings()
chroma_db = Chroma(embedding_function=embeddings)  # persist_directory="./chroma_db",

# Create a search object
search = TavilySearchResults()

# Load some documents from the web and store them in Chroma DB
loader = WebBaseLoader("https://docs.smith.langchain.com/overview")
docs = loader.load()
documents = RecursiveCharacterTextSplitter(
    chunk_size=1000, chunk_overlap=200
).split_documents(docs)
chroma_db.add_documents(documents)

# Create a retriever tool
retriever = chroma_db.as_retriever()
retriever_tool = create_retriever_tool(
    retriever,
    "langsmith_search",
    "Search for information about LangSmith. For any questions about LangSmith, you must use this tool!",
)


def store_data(key: str, value: str) -> str:
    """
    Function to store data in Chroma DB.

    :param key: The key to store the data.
    :param value: The value to store.
    :return: The response message.
    """
    chroma_db.add_texts([value], metadatas=[{"key": key}])
    return f"Stored '{key}': '{value}' in the database."


def retrieve_data(key: str) -> str:
    """
    Function to retrieve data from Chroma DB.

    :param key: The key to retrieve the data.
    :return: The response message.
    """
    results = chroma_db.similarity_search(key, k=1)
    if results:
        return f"Retrieved for '{key}': {results[0].page_content}"
    return f"No data found for '{key}'."


# Create tools for storing and retrieving data
store_tool = Tool(
    name="store_data",
    func=store_data,
    description="Store a key-value pair in the database. Input should be in the format 'key: value'."
)

retrieve_tool = Tool(
    name="retrieve_data",
    func=retrieve_data,
    description="Retrieve data from the database using a key."
)

# All the parts of the agent
tools = [search, retriever_tool, store_tool, retrieve_tool]
llm = ChatOpenAI(model="gpt-4-1106-preview", temperature=0)
prompt = format_prompt_simple("You are a helpful assistant that can be asked anything about LangSmith. You can also "
                              "store and retrieve data from a database.")

# Create the agent
agent = create_tool_calling_agent(llm, tools, prompt)

# Create an agent executor
agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

# Run the agent
result = agent_executor.invoke({
    "input": "Store 'Master of Science: A postgraduate academic master's degree awarded by universities or colleges "
             "upon completion of a course of study demonstrating mastery or a high-order overview of a specific "
             "field of study or area of professional practice.' Then retrieve the stored information for 'Master of "
             "Science'."})
print("\n", result)

# Another example
result = agent_executor.invoke({
    "input": "What's in the LangSmith documentation? Also, what information do we have stored about 'Master of "
             "Science'?"})
print("\n", result)
