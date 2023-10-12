from tinydb import TinyDB
from datetime import datetime
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from llm_session import Session


def package(text: str, is_user: bool = False, time: datetime = datetime.now()) -> dict:
    return {'text': text, 'isUser': is_user, 'time': int(time.timestamp())}


# Initialize the database
# db = TinyDB('history_' + str(datetime.now()) + '.json')

# Initialize the session
conversation = Session(
    llm_model="gpt-3.5-turbo",
    assistant_description="You are a helpful assistant.")


# Define the app
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
    answer = conversation.generate(message['text'])

    # Package and return the response
    return package(answer)
