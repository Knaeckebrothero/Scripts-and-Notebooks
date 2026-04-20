"""
This script uses the Keras OCR library to extract text from an image.
https://keras-ocr.readthedocs.io/en/latest/
"""
import keras_ocr


# Create a pipeline
pipeline = keras_ocr.pipeline.Pipeline()

# Recognize the text from the image
predictions = pipeline.recognize(["../test.png"])

# Display the text
for text in predictions:
    print(f'Detected text: {text}')
