"""
This script uses the easyocr library to extract text from an image.
https://github.com/jaidedai/easyocr
"""
import easyocr


# Create a reader object
reader = easyocr.Reader(['en'])

# Read the text from the image
results = reader.readtext('../test.png')

# Print the text
for result in results:
    print(result[1])
