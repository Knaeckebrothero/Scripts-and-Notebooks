import torch
import os
from dotenv import load_dotenv
from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline


# Load and set environment variables
load_dotenv()
model_id = os.getenv("MODEL_PATH")
# os.environ["TF_ENABLE_ONEDNN_OPTS"] = '0'
# os.environ["PYTORCH_ENABLE_ONEDNN_OPTS"] = '0'

# Set the seed
torch.random.manual_seed(0)

# Load the model and tokenizer
model = AutoModelForCausalLM.from_pretrained(
    model_id,
    device_map="cuda",
    torch_dtype="auto",
    trust_remote_code=True
)
tokenizer = AutoTokenizer.from_pretrained(model_id)

messages = [
    {"role": "user", "content": ""},
    {"role": "assistant", "content": ""},
]

pipe = pipeline(
    "text-generation",
    model=model,
    tokenizer=tokenizer,
)

generation_args = {
    "max_new_tokens": 1500,
    "return_full_text": False,
    "temperature": 0.7,
    "do_sample": False,
}

output = pipe(messages, **generation_args)
print("Assistant: ", output[0]['generated_text'])

while True:
    user_input = input("Ask a question (or type 'quit' to exit): ")
    if user_input.lower() == 'quit':
        break

    output = pipe([{"role": "user", "content": user_input}], **generation_args)
    print("Assistant: ", output[0]['generated_text'])
    print()
