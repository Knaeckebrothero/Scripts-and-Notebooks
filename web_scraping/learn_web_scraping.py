import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions
from development_functions import read_config

# Initialize the WebDriver and load login page.
driver = webdriver.Chrome()
driver.get(read_config('loginLink'))

# Locate the username and password input fields and enter the credentials.
username_field = driver.find_element(By.NAME, "wps.portlets.userid")
username_field.send_keys(read_config('username'))
password_field = driver.find_element(By.NAME, "password")
password_field.send_keys(read_config('password'))

# Locate the login button and click it.
login_button = driver.find_element(
    By.XPATH, '//a[contains(@href, "javascript:document.LoginForm.submit()")]')
login_button.click()


# Wait for the login process to complete
time.sleep(2)
"""
wait = WebDriverWait(driver, 20)
wait.until(expected_conditions.url_changes(driver.current_url))
"""

# Load page containing information to be scrapped.
driver.get(read_config('pageLink'))

# Find the table containing the information by the specified id.
table = driver.find_element(By.ID, "productTable")
print("Table found")

# Find all rows with class "tableTagRowWithDocumentFunctions"
rows = table.find_elements(
    By.XPATH, ".//tr[contains(@class, 'tableTagRowWithDocumentFunctions')]")
print(f"Found {len(rows)} rows")

# Iterate over the rows
rows_count = len(rows)
for index in range(rows_count):
    # Re-locate the table and rows
    table = driver.find_element(By.ID, "productTable")
    rows = table.find_elements(
        By.XPATH, ".//tr[contains(@class, 'tableTagRowWithDocumentFunctions')]"
    )
    row = rows[index]

    # Find the link within the row
    link = row.find_element(By.CSS_SELECTOR, "a[href*='javascript:setAttribute_PC']")

    # Click the link
    link.click()

    # Wait for the new page to load
    time.sleep(2)
    """
    wait = WebDriverWait(driver, 20)
    wait.until(expected_conditions.url_changes(driver.current_url))
    """

    try:
        # Find the element with the specified class
        product_detail_item = WebDriverWait(driver, 10).until(
            expected_conditions.presence_of_element_located((By.CLASS_NAME, "productdetailitem"))
        )

        if product_detail_item:
            # Find the span element inside the product detail item
            span_element = product_detail_item.find_element(By.TAG_NAME, "span")

            if span_element:
                # Get the text content of the span element
                text_content = span_element.text

                # Print the text content
                print('Found')
            else:
                print("Span element not found.")
        else:
            print("Product detail item not found.")
    except Exception as e:
        print(f"Error: {str(e)}")

    # Go back to the previous page
    driver.back()

    # Wait for the previous page to load
    time.sleep(2)
    """
    wait.until(expected_conditions.url_changes(driver.current_url))
    """
