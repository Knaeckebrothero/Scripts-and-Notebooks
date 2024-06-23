"""
This script is using Streamlit to create a user interface.
https://docs.streamlit.io/develop/quick-reference/cheat-sheet

Pdf2Image requires poppler to be installed on the system.
https://pdf2image.readthedocs.io/en/latest/installation.html
"""
import streamlit as st
import cv2
import numpy as np
from pdf2image import convert_from_bytes
from preprocessing import detect_tables, detect_rows, detect_cells
from ocr import ocr_cell


def detect_table_type(table_image):
    gray = cv2.cvtColor(table_image, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 240, 255, cv2.THRESH_BINARY_INV)

    # Detect vertical lines
    vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, table_image.shape[0] // 3))
    detect_vertical = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, vertical_kernel, iterations=2)

    # Find contours of vertical lines
    contours, _ = cv2.findContours(detect_vertical, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # If we detect more than one vertical line, it's a multi-column table
    if len(contours) > 1:
        return "multi-column"
    else:
        return "single-column"


def process_single_column_row(row_image):
    # Use the entire row as one cell
    text = ocr_cell(row_image)
    return [text]


def process_multi_column_row(row_image):
    # Split the row into two parts (left and right)
    mid = row_image.shape[1] // 2
    left_part = row_image[:, :mid]
    right_part = row_image[:, mid:]

    # OCR both parts
    left_text = ocr_cell(left_part)
    right_text = ocr_cell(right_part)

    return [left_text, right_text]


def structure_table_data(table_data):
    # Assume first row is header
    header = table_data[0]

    # Structure data
    structured_data = []
    for row in table_data[1:]:
        if len(row) == len(header):
            structured_row = dict(zip(header, row))
            structured_data.append(structured_row)

    return structured_data


if __name__ == "__main__":
    # Config & initialisation
    st.set_page_config(layout="wide",
                       page_title="ChineseSpyware.prism",
                       page_icon=":sleuth_or_spy:"
                       )

    # Set page title
    st.title("Willkommen im unsichersten programm der Bundesrepublik :smiling_imp:")

    # File upload
    pdf_document = st.file_uploader("Um ihre daten fahrlässig preiszugeben bitte hier hochladen! :smiling_imp:")

    if pdf_document:
        images = convert_from_bytes(pdf_document.read())

        for i, image in enumerate(images):
            np_image = np.array(image)
            bgr_image = cv2.cvtColor(np_image, cv2.COLOR_RGB2BGR)

            # Detect tables
            table_contours = detect_tables(bgr_image)

            # Draw table boundaries
            result_image = bgr_image.copy()
            cv2.drawContours(result_image, table_contours, -1, (0, 255, 0), 3)

            # Display original and processed images (keep your existing display code)
            st.image(image, caption=f"Original - Page {i + 1}", use_column_width=False, width=500)
            st.image(cv2.cvtColor(result_image, cv2.COLOR_BGR2RGB),
                     caption=f"Detected Tables - Page {i + 1}",
                     use_column_width=False, width=500)

            st.write(f"Number of tables detected on page {i + 1}: {len(table_contours)}")

            # Process each detected table
            for j, contour in enumerate(table_contours):
                x, y, w, h = cv2.boundingRect(contour)
                table_roi = bgr_image[y:y + h, x:x + w]

                st.write(f"Table {j + 1} on Page {i + 1}")
                st.image(cv2.cvtColor(table_roi, cv2.COLOR_BGR2RGB),
                         caption=f"Table {j + 1}",
                         use_column_width=False, width=500)

                # Detect rows in the table
                rows = detect_rows(table_roi)

                st.write(f"Number of rows detected: {len(rows)}")

                table_data = []

                # Process each detected row
                for k, (y1, y2) in enumerate(rows):
                    row_image = table_roi[y1:y2, :]
                    st.image(cv2.cvtColor(row_image, cv2.COLOR_BGR2RGB),
                             caption=f"Row {k + 1}",
                             use_column_width=False)

                    # Detect cells in the row
                    cells = detect_cells(row_image)

                    row_data = []
                    for m, (x1, x2) in enumerate(cells):
                        cell_image = row_image[:, x1:x2]
                        cell_text = ocr_cell(cell_image)
                        row_data.append(cell_text)

                    table_data.append(row_data)

                # Display extracted data in a Streamlit table
                st.write("Extracted Table Data:")
                st.table(table_data)

                st.write("---")  # Separator between tables
