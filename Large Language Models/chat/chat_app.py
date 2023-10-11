import guidance
from tinydb import TinyDB
from datetime import datetime
from fastapi import FastAPI
from dotenv import load_dotenv, find_dotenv
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


def package(text: str, is_user: bool = False, time: datetime = datetime.now()) -> dict:
    return {'text': text, 'isUser': is_user, 'time': int(time.timestamp())}


def generate(generate_prompt: str, generate_template: guidance.Program) -> str:
    return generate_template(
        system_prompt="You are a helpful digital assistant.",
        relationship="This is the first time the user meets with his new assistant.",
        prompt=generate_prompt)


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


@app.post("/api/chat/")
async def read_chat(message: dict):
    # Generate a response
    answer = generate(message['text'], template).variables().get('answer')

    # Package and return the response
    return package(answer)


# Load API key
load_dotenv(find_dotenv())

db = TinyDB(memory=True)

# Set default language model & template
llm_model = guidance.llms.OpenAI("gpt-3.5-turbo")  # "gpt-4" "gpt-3.5-turbo"
template = guidance.Program(text_data('structure.txt'), llm=llm_model)
