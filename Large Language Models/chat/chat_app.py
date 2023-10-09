import guidance
from dotenv import load_dotenv, find_dotenv
import dev_tools.read as rd

# Load & pass API key
load_dotenv(find_dotenv())

# Set default language model
llm_model = guidance.llms.OpenAI("gpt-3.5-turbo") # "gpt-4" "gpt-3.5-turbo"

# Load a guidance template for generation
generated_template = guidance.Program(
    rd.text_data('structure.txt'), llm=llm_model)

# Generate text
generated_text = generated_template(
    system_prompt="You are a helpful digital assistant.",
    relationship="This is the first time the user meets with his new assistant.",
    prompt="Hello, nice to meet you!")

# Print generated text
print(generated_text)
