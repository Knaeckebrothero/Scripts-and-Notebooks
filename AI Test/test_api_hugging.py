import streamlit as st
import requests


# Function to call Hugging Face API
def call_huggingface_api(text):
    api_url = "https://go.get.your-own-link.cloud"
    headers = {"Authorization": "Bearer IknowYouWouldLoveMeToShareMyKeyButNo"}

    payload = {
        "inputs": text,
        "parameters": {
            "max_new_tokens": 100,
            "return_full_text": True,
            "temperature": 0.8,
            "top_k": 50,
            "top_p": 0.9,
            "repetition_penalty": 1.3,
            "do_sample": True
        }
    }

    response = requests.post(api_url, headers=headers, json=payload)
    return response.json()


# Streamlit interface
st.title('Hugging Face API Interface')

# Text input
text = st.text_input('Enter your text here')

# Button to send the text to the API
if st.button('Generate Text'):
    result = call_huggingface_api(text)
    st.write(result)
