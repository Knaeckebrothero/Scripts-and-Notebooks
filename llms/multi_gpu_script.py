import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, AutoConfig, BitsAndBytesConfig
import argparse
import logging
import time
import os
from typing import Dict, Any, Optional
from huggingface_hub import login


# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_gpu_memory() -> Dict[int, int]:
    """
    Get available memory for each GPU in MB
    """
    available_gpus = {}
    for i in range(torch.cuda.device_count()):
        free_mem = torch.cuda.get_device_properties(i).total_memory - torch.cuda.memory_allocated(i)
        available_gpus[i] = free_mem // (1024 * 1024)  # Convert to MB
    return available_gpus

def create_device_map(model_name: str, num_gpus: int, token: Optional[str] = None) -> Dict[str, int]:
    """
    Create a custom device map to distribute the model across GPUs
    """
    if num_gpus <= 0:
        return "cpu"
    elif num_gpus == 1:
        return "cuda:0"

    # Get GPU memory information
    gpu_memory = get_gpu_memory()
    logger.info(f"Available GPU memory (MB): {gpu_memory}")

    # Load model config to understand its architecture
    logger.info(f"Loading config for {model_name} with authentication")
    config = AutoConfig.from_pretrained(
        model_name,
        token=token,
        trust_remote_code=True
    )

    # For Llama models
    if hasattr(config, "num_hidden_layers"):
        num_layers = config.num_hidden_layers
        logger.info(f"Model has {num_layers} layers")

        # Create a device map
        device_map = {}

        # Map embeddings to first GPU
        device_map["model.embed_tokens"] = 0

        # Distribute layers across GPUs
        layers_per_gpu = num_layers // num_gpus
        for i in range(num_layers):
            gpu_idx = min(i // layers_per_gpu, num_gpus - 1)
            device_map[f"model.layers.{i}"] = gpu_idx

        # Map final norm and output layer to last GPU
        device_map["model.norm"] = num_gpus - 1
        device_map["lm_head"] = num_gpus - 1

        return device_map
    else:
        # For other model architectures or fallback
        logger.warning("Could not determine model architecture. Using 'auto' device map.")
        return "auto"

def setup_model(
        model_name: str,
        num_gpus: int = 3,
        precision: str = "half",
        token: Optional[str] = None
) -> tuple:
    """
    Load the model and tokenizer with appropriate device mapping and precision

    Args:
        model_name: Name of the model from Hugging Face or local path
        num_gpus: Number of GPUs to use
        precision: Model precision ("half", "float", "int8" or "int4")

    Returns:
        Tuple of (model, tokenizer)
    """
    logger.info(f"Loading model {model_name} with {num_gpus} GPUs and {precision} precision")

    # Determine appropriate dtype based on precision argument
    # Determine appropriate dtype based on precision argument
    if precision == "half":
        dtype = torch.float16
    elif precision == "float":
        dtype = torch.float32
    elif precision == "bf16":
        dtype = torch.bfloat16
    else:
        # For int8 and int4 quantization, we still need to use a floating point dtype
        # The quantization happens through the load_in_8bit or load_in_4bit parameters
        logger.info(f"For quantized mode {precision}, using bfloat16 as base dtype")
        dtype = torch.bfloat16

    # Check for available GPUs
    available_gpus = torch.cuda.device_count()
    if available_gpus == 0:
        logger.warning("No GPUs available, running on CPU")
        num_gpus = 0
    elif available_gpus < num_gpus:
        logger.warning(f"Requested {num_gpus} GPUs but only {available_gpus} available. Using {available_gpus} GPUs.")
        num_gpus = available_gpus

    # Create device map with token
    device_map = create_device_map(model_name, num_gpus, token)
    logger.info(f"Using device map: {device_map}")

    # Use the token for authentication if provided
    if token:
        logger.info("Using provided Hugging Face token for authentication")
        # Set environment variable for token
        os.environ["HUGGING_FACE_HUB_TOKEN"] = token
        # Login explicitly with the token
        login(token)

    # Load tokenizer with explicit token and configure padding
    tokenizer = AutoTokenizer.from_pretrained(model_name, token=token)

    # Fix tokenizer padding configuration for Llama models
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # This is critical - for Llama models, we need to configure padding behavior
    tokenizer.padding_side = "left"  # Set padding to left side to avoid affecting output

    # Set up quantization config if needed
    load_kwargs = {
        "device_map": device_map,
        "torch_dtype": dtype,
        "trust_remote_code": True,
    }

    if precision == "int4":
        try:
            import bitsandbytes as bnb
            logger.info("Using 4-bit quantization with bitsandbytes")
            # Create a BitsAndBytesConfig object for 4-bit quantization
            quantization_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
            )
            load_kwargs["quantization_config"] = quantization_config
        except ImportError:
            logger.warning("bitsandbytes not installed. To use 4-bit quantization, install it with: pip install bitsandbytes")
            logger.warning("Falling back to bfloat16 precision")
    elif precision == "int8":
        try:
            import bitsandbytes as bnb
            logger.info("Using 8-bit quantization with bitsandbytes")
            # Create a BitsAndBytesConfig object for 8-bit quantization
            quantization_config = BitsAndBytesConfig(
                load_in_8bit=True,
                bnb_8bit_compute_dtype=torch.bfloat16,
            )
            load_kwargs["quantization_config"] = quantization_config
        except ImportError:
            logger.warning("bitsandbytes not installed. To use 8-bit quantization, install it with: pip install bitsandbytes")
            logger.warning("Falling back to bfloat16 precision")

    # Load model with token if provided
    start_time = time.time()
    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        token=token,
        **load_kwargs
    )
    load_time = time.time() - start_time
    logger.info(f"Model loaded in {load_time:.2f} seconds")

    # Print model distribution information
    if hasattr(model, 'hf_device_map'):
        logger.info("Model distribution across devices:")
        devices = set(model.hf_device_map.values())
        for device in sorted(devices):
            num_layers = list(model.hf_device_map.values()).count(device)
            logger.info(f"  Device {device}: {num_layers} layers")

    return model, tokenizer

def format_prompt(user_input: str, model_name: str, system_prompt: Optional[str] = None) -> str:
    """
    Format the prompt according to the model's expected chat format

    Args:
        user_input: The user's input message
        model_name: Name of the model to determine appropriate chat format
        system_prompt: Optional system prompt to include

    Returns:
        Formatted prompt string
    """
    model_name_lower = model_name.lower()

    # Llama 2 Chat format
    if "llama-2" in model_name_lower and "chat" in model_name_lower:
        if system_prompt:
            return f"<s>[INST] <<SYS>>\n{system_prompt}\n<</SYS>>\n\n{user_input} [/INST]"
        else:
            return f"<s>[INST] {user_input} [/INST]"

    # Llama 3.3 and Llama 3 Chat format
    elif "llama-3.3" in model_name_lower or "llama-3" in model_name_lower:
        if system_prompt:
            return f"<|begin_of_text|><|system|>\n{system_prompt}<|end_of_turn|>\n<|user|>\n{user_input}<|end_of_turn|>\n<|assistant|>\n"
        else:
            return f"<|begin_of_text|><|user|>\n{user_input}<|end_of_turn|>\n<|assistant|>\n"

    # Mistral Instruct format
    elif "mistral" in model_name_lower and ("instruct" in model_name_lower or "chat" in model_name_lower):
        if system_prompt:
            return f"<s>[INST] {system_prompt}\n{user_input} [/INST]"
        else:
            return f"<s>[INST] {user_input} [/INST]"

    # CodeLlama format
    elif "codellama" in model_name_lower:
        if system_prompt:
            return f"<s>[INST] <<SYS>>\n{system_prompt}\n<</SYS>>\n\n{user_input} [/INST]"
        else:
            return f"<s>[INST] {user_input} [/INST]"

    # Generic format (for base models)
    else:
        return user_input

def generate_response(
        prompt: str,
        model: Any,
        tokenizer: Any,
        max_new_tokens: int = 512,
        temperature: float = 0.7,
        top_p: float = 0.9,
        repetition_penalty: float = 1.1
) -> str:
    """
    Generate a response from the model

    Args:
        prompt: The formatted prompt to send to the model
        model: The loaded model
        tokenizer: The tokenizer
        max_new_tokens: Maximum number of new tokens to generate
        temperature: Temperature for sampling (higher = more random)
        top_p: Top-p sampling parameter (1.0 = no top-p sampling)
        repetition_penalty: Penalty for repeating tokens

    Returns:
        Generated response text
    """
    # Encode the prompt with padding and create attention masks properly
    encoded_input = tokenizer(
        prompt,
        return_tensors="pt",
        padding=True,  # Enable padding
        truncation=True,  # Enable truncation if needed
        max_length=tokenizer.model_max_length  # Use model's max length
    )

    # Move input tensors to the appropriate device
    if hasattr(model, 'hf_device_map'):
        # Find the device of the first module
        first_device = next(iter(model.hf_device_map.values()))
        input_device = f"cuda:{first_device}" if isinstance(first_device, int) else first_device
        encoded_input = {k: v.to(input_device) for k, v in encoded_input.items()}
    else:
        # If no device map is available, use the model's device
        encoded_input = {k: v.to(model.device) for k, v in encoded_input.items()}

    # Generate
    start_time = time.time()
    with torch.no_grad():
        outputs = model.generate(
            encoded_input["input_ids"],
            attention_mask=encoded_input["attention_mask"],  # Explicitly pass attention mask
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            top_p=top_p,
            repetition_penalty=repetition_penalty,
            do_sample=temperature > 0,
            pad_token_id=tokenizer.pad_token_id,
            eos_token_id=tokenizer.eos_token_id
        )
    gen_time = time.time() - start_time

    # Calculate tokens per second
    num_tokens = outputs.shape[1] - encoded_input["input_ids"].shape[1]
    tokens_per_second = num_tokens / gen_time
    logger.info(f"Generated {num_tokens} tokens in {gen_time:.2f}s ({tokens_per_second:.2f} tokens/sec")

    # Decode output
    full_output = tokenizer.decode(outputs[0], skip_special_tokens=True)

    # Extract just the assistant's response
    input_text = tokenizer.decode(encoded_input["input_ids"][0], skip_special_tokens=True)
    response = full_output[len(input_text):].strip()
    response = full_output[len(input_text):].strip()

    # Handle specific model formats
    model_name_lower = model.config._name_or_path.lower() if hasattr(model.config, "_name_or_path") else ""

    # For Llama-3 or Llama-3.3 models, extract content after assistant marker
    if "llama-3" in model_name_lower:
        if "<|assistant|>" in full_output:
            response = full_output.split("<|assistant|>")[1].strip()
            # Remove end of turn marker if present
            if "<|end_of_turn|>" in response:
                response = response.split("<|end_of_turn|>")[0].strip()

    # Clean up any alternative responses (separated by | in some models)
    if "|" in response:
        # Take just the first response before any | marker
        response = response.split("|")[0].strip()

    return response

def main():
    parser = argparse.ArgumentParser(description='Run a Llama model on multiple GPUs')
    parser.add_argument('--model', type=str, default="meta-llama/Llama-3.3-70B-Instruct",
                        help='Model name or path (default: meta-llama/Llama-3.3-70B-Instruct)')
    parser.add_argument('--num_gpus', type=int, default=3,
                        help='Number of GPUs to use (default: 3)')
    parser.add_argument('--precision', type=str, choices=["half", "float", "int8", "int4", "bf16"], default="bf16",
                        help='Model precision (default: bf16)')
    parser.add_argument('--max_new_tokens', type=int, default=512,
                        help='Maximum number of new tokens to generate (default: 512)')
    parser.add_argument('--temperature', type=float, default=0.7,
                        help='Temperature for generation (default: 0.7)')
    parser.add_argument('--system_prompt', type=str, default=None,
                        help='Optional system prompt to use')
    parser.add_argument('--top_p', type=float, default=0.9,
                        help='Top-p sampling parameter (default: 0.9)')
    parser.add_argument('--repetition_penalty', type=float, default=1.1,
                        help='Repetition penalty (default: 1.1)')
    parser.add_argument('--hf_token', type=str, required=True,
                        help='HuggingFace token for accessing gated models')
    args = parser.parse_args()

    # Clean up and set environment variables for HuggingFace authentication
    if args.hf_token:
        # If HF_TOKEN is already set, we'll clear it to avoid conflicts
        if "HF_TOKEN" in os.environ:
            logger.info("Clearing existing HF_TOKEN environment variable to avoid conflicts")
            os.environ.pop("HF_TOKEN", None)

        # Set the standard environment variable
        os.environ["HUGGING_FACE_HUB_TOKEN"] = args.hf_token

        # Explicitly perform login
        logger.info("Logging in to HuggingFace with token")
        login(args.hf_token)

    # Check for available GPUs
    if torch.cuda.is_available():
        num_gpus = torch.cuda.device_count()
        logger.info(f"Found {num_gpus} GPU(s)")
        for i in range(num_gpus):
            logger.info(f"  GPU {i}: {torch.cuda.get_device_name(i)}")
            free_mem = torch.cuda.get_device_properties(i).total_memory - torch.cuda.memory_allocated(i)
            logger.info(f"    Available memory: {free_mem // (1024 * 1024)} MB")
    else:
        logger.warning("No GPUs found. Running on CPU.")
        args.num_gpus = 0

    # Check if trying to use quantized precision
    if args.precision in ["int8", "int4"]:
        try:
            import bitsandbytes
            logger.info(f"Found bitsandbytes version {bitsandbytes.__version__}")
        except ImportError:
            logger.error("bitsandbytes not installed. Cannot use quantization.")
            logger.error("Please install bitsandbytes: pip install bitsandbytes")
            logger.info("Falling back to bf16 precision instead")
            args.precision = "bf16"

    # Check for available GPUs
    if torch.cuda.is_available():
        num_gpus = torch.cuda.device_count()
        logger.info(f"Found {num_gpus} GPU(s)")
        for i in range(num_gpus):
            logger.info(f"  GPU {i}: {torch.cuda.get_device_name(i)}")
            free_mem = torch.cuda.get_device_properties(i).total_memory - torch.cuda.memory_allocated(i)
            logger.info(f"    Available memory: {free_mem // (1024 * 1024)} MB")
    else:
        logger.warning("No GPUs found. Running on CPU.")
        args.num_gpus = 0

    # Load model and tokenizer
    model, tokenizer = setup_model(args.model, args.num_gpus, args.precision, args.hf_token)

    logger.info("Model loaded successfully!")
    logger.info(f"Model: {args.model}")
    logger.info(f"GPUs: {args.num_gpus}")
    logger.info(f"Precision: {args.precision}")

    # Interactive chat loop
    print("\n======= Multi-GPU LLM Chat =======")
    print(f"Model: {args.model}")
    print(f"Type your message (or 'exit' to quit, 'clear' to start a new chat)")

    chat_history = []

    while True:
        user_input = input("\nYou: ")
        if user_input.lower() == 'exit':
            break
        elif user_input.lower() == 'clear':
            chat_history = []
            print("Chat history cleared.")
            continue

        # Format the input as expected by the model
        formatted_prompt = format_prompt(user_input, args.model, args.system_prompt)

        # Generate response
        response = generate_response(
            formatted_prompt,
            model,
            tokenizer,
            max_new_tokens=args.max_new_tokens,
            temperature=args.temperature,
            top_p=args.top_p,
            repetition_penalty=args.repetition_penalty
        )

        # Add to chat history
        chat_history.append((user_input, response))

        # Print response
        print(f"\nChatbot: {response}")

if __name__ == "__main__":
    main()
