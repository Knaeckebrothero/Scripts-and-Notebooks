from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain.output_parsers import CommaSeparatedListOutputParser
from langchain_core.output_parsers import StrOutputParser


# Function to run the chat chain
def run_chat_chain(question):
    response = chat_chain.invoke({"question": question})
    return response


# Load environment variables
load_dotenv()

# Initialize the ChatOpenAI model
chat_model = ChatOpenAI(model="gpt-3.5-turbo-0125")

# Create a chat prompt template
template = """
You are a helpful assistant that provides information on various topics.
Please answer the following question:
{question}

If the question asks for a list, please provide the answer as a comma-separated list.
"""

chat_prompt = ChatPromptTemplate.from_template(template)

# Create an output parser
output_parser = StrOutputParser()

# Create the chat chain
chat_chain = chat_prompt | chat_model | output_parser

# Example usage
if __name__ == "__main__":
    while True:
        user_input = input("Ask a question (or type 'quit' to exit): ")
        if user_input.lower() == 'quit':
            break

        response = run_chat_chain(user_input)
        print("Assistant:", response)
        print()
