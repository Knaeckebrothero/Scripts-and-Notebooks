import guidance
from dotenv import load_dotenv, find_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


# Retrieve a text from a .txt file.
def text_data(path: str = './content.txt') -> str:
    """
    This function reads data from a text file.

    Args:
        path (str): Path to the text file.

    Returns:
        text (str): Text content of the file.
    """
    with open(path, 'r', encoding='utf-8') as file:
        text_contents = file.read()
    return text_contents


app = FastAPI()

# CORS settings
origins = [
    "http://localhost:4200",  # Angular app
    # add any other origins that need to access the API
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def generate(generate_prompt: str, generate_template: guidance.Program) -> str:
    return generate_template(
        system_prompt="You are a helpful digital assistant.",
        relationship="This is the first time the user meets with his new assistant.",
        prompt=generate_prompt)


@app.post("/api/chat/")
async def read_chat(content: dict):
    # Generate a response
    answer = {"content": generate(content['content'], template).variables().get('answer')}
    return answer


# Load API key
load_dotenv(find_dotenv())

# Set default language model & template
llm_model = guidance.llms.OpenAI("gpt-3.5-turbo")  # "gpt-4" "gpt-3.5-turbo"
template = guidance.Program(text_data('structure.txt'), llm=llm_model)
