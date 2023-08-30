"""
A trashy class to run a test model on your local machine with auto-gptq.
https://huggingface.co/TheBloke/Llama-2-7b-Chat-GPTQ
https://github.com/PanQiWei/AutoGPTQ
"""
import os
from dotenv import load_dotenv
from transformers import AutoTokenizer
from auto_gptq import AutoGPTQForCausalLM


# Model runner
class Model:
    def __init__(self):
        load_dotenv()
        model_name_or_path = os.getenv('LLAMA2_GPTQ_PATH')
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_name_or_path, use_fast=True)
        self.model = AutoGPTQForCausalLM.from_quantized(
            model_name_or_path,
            model_basename="gptq_model-4bit-128g",
            use_safetensors=True,
            trust_remote_code=True,
            device="cuda:0",
            use_triton=False,
            quantize_config=None)
        print("Model loaded")

    def generate(self, prompt, temperature=0.6, max_new_tokens=100):
        input_ids = self.tokenizer(prompt, return_tensors='pt').input_ids.cuda()
        output = self.model.generate(
            inputs=input_ids,
            temperature=temperature,
            max_new_tokens=max_new_tokens)
        return self.tokenizer.decode(output[0])


"""
# Load the model path
load_dotenv()
model_name_or_path = os.getenv('LLAMA2_GPTQ_PATH')

tokenizer = AutoTokenizer.from_pretrained(model_name_or_path, use_fast=True)

model = AutoGPTQForCausalLM.from_quantized(model_name_or_path,
                                           model_basename="gptq_model-4bit-128g",
                                           use_safetensors=True,
                                           trust_remote_code=True,
                                           device="cuda:0",
                                           use_triton=False,
                                           quantize_config=None)

input_ids = tokenizer(prompt, return_tensors='pt').input_ids.cuda()
output = model.generate(inputs=input_ids, temperature=0.6, max_new_tokens=100)
tokenizer.decode(output[0])
"""
