import openai
from dev_tools import read as rd


# Call the OpenAI API functions with the given content and description.
def call_function(content, description):
    api_call = openai.ChatCompletion.create(
        model="gpt-3.5-turbo-0613",
        messages=content,
        functions=description,
        function_call="auto"
    )
    return api_call


openai.api_key = rd.credentials('open_ai_key')

response = call_function(rd.text_data('content.txt'), rd.json_data('task.json'))
print(response)
