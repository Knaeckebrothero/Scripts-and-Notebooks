from flask import Flask, request, jsonify
from transformers import GPT2LMHeadModel, GPT2Tokenizer

# Create the flask app
app = Flask(__name__)

# Load the model and tokenizer
tokenizer = GPT2Tokenizer.from_pretrained("gpt2")
model = GPT2LMHeadModel.from_pretrained("gpt2")


# Function for handling the api request.
@app.route('/generate', methods=['POST'])
def generate():
    # Check for API key
    if request.headers.get('X-API-Key') != 'Sd%7Rz#cdd2c65Uv@%Z':
        return jsonify({'message': 'Invalid API key'}), 403

    # Get the input data from the request
    data = request.get_json()
    prompt = data.get('prompt')

    # Generate the output
    inputs = tokenizer.encode(prompt, return_tensors='pt')
    outputs = model.generate(inputs, max_length=150, num_return_sequences=1)
    text = tokenizer.decode(outputs[0], skip_special_tokens=True)

    # Return the result
    return jsonify({'result': text})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)