import os
import dotenv
from guidance import models, gen, instruction

dotenv.load_dotenv()
os.environ['OPENAI_API_KEY'] = os.getenv('OPENAI_API_KEY')

# Load the model
gpt3 = models.OpenAI('gpt-3.5-turbo-instruct')

lm = gpt3

with instruction():
    lm += "What is a popular flavor?"

lm += gen('flavor', max_tokens=10, stop=".")

print(lm)
