from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# Login
def login(username: str, password: str, url: str, webdriver: webdriver.Chrome) -> None:
    webdriver.get(url)

    # Locate the username and password input fields and enter your credentials
    username_field = webdriver.find_element(By.NAME, "wps.portlets.userid")
    username_field.send_keys(username)
    password_field = webdriver.find_element(By.NAME, "password")
    password_field.send_keys(password)

    # Locate the login button and click it
    login_button = webdriver.find_element(
        By.XPATH, '//a[contains(@href, "javascript:document.LoginForm.submit()")]')
    login_button.click()

    # Wait for the login process to complete
    wait = WebDriverWait(webdriver, 10)
    wait.until(EC.url_changes(webdriver.current_url))
    return


# Save descriptions
def retrieve_descriptions(webdriver: webdriver.Chrome()):
    # Find the table with the specified id
    table = webdriver.find_element(By.ID, "productTable")

    # Find all rows with class "tableTagRowWithDocumentFunctions"
    rows = table.find_elements(
        By.XPATH, ".//tr[contains(@class, 'tableTagRowWithDocumentFunctions')]")

    # Iterate over the rows
    for row in rows:
        # Find the link within the row
        link = row.find_element(By.XPATH, ".//a[contains(@href, 'javascript:setAttribute_PC')]")

        # Click the link
        link.click()

        # Wait for the new page to load
        wait = WebDriverWait(driver, 10)
        wait.until(EC.url_changes(driver.current_url))

        try:
            # Find the element with the specified class
            product_detail_item = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".productdetailitem"))
            )

            if product_detail_item:
                # Find the span element inside the product detail item
                span_element = product_detail_item.find_element_by_css_selector("span")

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
        wait.until(EC.url_changes(driver.current_url))



# Input credentials
usr = input('Bitte ihre GENO User-ID eingeben.')
pw = input('Bitte ihr Kennwort eingeben.')

# Initialize the WebDriver
driver = webdriver.Chrome()

# Login
print("Loging in...")
login(
    username=usr,
    password=pw,
    url='https://www.vr-infoforum.fiducia.de/wps/portal/!ut/p/z1/04_Sj9CPykssy0xPLMnMz0vMAfIjo'
        '8zind0dPUzMfQwM3P2CnA08TZxMjDzcnYwMfA31wwkpiAJKG-AAjgb6BbmhigALNqjY/dz/d5/'
        'L2dBISEvZ0FBIS9nQSEh/',
    webdriver=driver
)

# Start scraping process
print('Login erfolgreich, starte script...')

driver.get('https://www.vr-infoforum.fiducia.de/wps/myportal/!ut/p/z1/04_Sj9CPykssy0xPLMnMz0v'
           'MAfIjo8ziDfwNvJ3cAwwt3QxMDF19fA29nQwgQD8cogAHcDTQjyJKP5oCS1O4ArL0O3o6h7p'
           '4uhn7GpOnH0lBFH7vRQL1m2PoN3L2MYTqj8hJTU9MrtQPL8tMLdcvyA0NDY2oLAcArc-0Ww'
           '!!/dz/d5/L2dJQSEvUUt3QS80TmxFL1o2XzBPMEtCR1AxOUYwNDFFTE0xS0IwMDAwMDAw/')

retrieve_descriptions(driver)
