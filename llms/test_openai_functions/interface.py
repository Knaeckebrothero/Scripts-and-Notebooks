import streamlit as st
from streamlit_chat import message
import requests
import json

llm_instructions = """[SYSTEM INSTRUCTIONS: This is a conversation with your Assistant. It is a computer program 
designed to help you with various tasks such as answering questions, providing recommendations, and helping with 
decision making. You can ask it anything you want and it will do its best to give you accurate and relevant 
information. It can also take notes and save them in a database, if the user tells the model to take a note or 
remember something the model would take the exact text that the use said it should remember and add it before its 
answer in the following format: {NOTE: "<PUT USER NOTE HERE>"}. This applies to everything the user might ask you to 
remember or write down or other simular tasks. Do always do this, never act like you can remember anything. Example: 
User "Hello please remember that i need to go get groceries when im at home" Assistent: "{NOTE: "go get groceries 
when at home"} Sure i have saved that for you!] """

st.title("Simple Chat App")

# Initialize chat history
if "messages" not in st.session_state:
    st.session_state.messages = []

# Display chat messages from history on app rerun
if "messages" in st.session_state:
    for i in range(len(st.session_state['messages'])):
        message(st.session_state['messages'][i]['content'],
                is_user=st.session_state['messages'][i]['role'] == 'user',
                key=f"{i}_user")

# Accept user input
if prompt := st.text_input("Type your message here..."):
    # Add user message to chat history
    st.session_state.messages.append({"role": "user", "content": prompt})
    # Display user message in chat message container
    message(prompt, is_user=True)

    # Prepare the request data
    request_data = {
        "inputs": llm_instructions + " " + prompt,
        "parameters": {
            "min_new_tokens": 0,
            "max_new_tokens": 4096,
            "early_stopping": False,
            "temperature": 1.5,
            "top_k": 30,
            "top_p": 0.6,
            "repetition_penalty": 1.1,
            "do_sample": True
        }
    }

    # Send user message to API
    response = requests.post(
        "localhost:5000/api",
        data=json.dumps(request_data),
    )
    response_message = response.json()["prompt"]

    # Display API response in chat message container
    message(response_message, is_user=False)
    # Add API response to chat history
    st.session_state.messages.append(
        {"role": "assistant", "content": response_message})
