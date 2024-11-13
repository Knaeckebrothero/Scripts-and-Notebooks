"""
This module contains functions for OCR (Optical Character Recognition) of images.
"""
import cv2
import pytesseract
from PIL import Image


def ocr_cell(cell_image):
    # Convert to PIL Image for Tesseract
    pil_image = Image.fromarray(cv2.cvtColor(cell_image, cv2.COLOR_BGR2RGB))
    # Perform OCR
    text = pytesseract.image_to_string(pil_image)  # , config='--psm 6'
    return text.strip()
