import cv2
import pytesseract

# Read the image
img = cv2.imread('test_image.png')

# Convert the image to grayscale
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

# Use Tesseract to do OCR on the image
text = pytesseract.image_to_string(gray)

print("Extracted Text:")
print(text)
