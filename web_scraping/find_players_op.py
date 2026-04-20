"""
This script aims to find and retrieve player names,
from the following video game analysis page:

https://www.op.gg/leaderboards/champions/fiddlesticks?region=na
https://www.op.gg/robots.txt
"""
import time
from selenium import webdriver
from selenium.webdriver.common.by import By

# Get champion name
#champ = input('Please enter a champion name: ')
champ = 'leesin'
servers = ['na', 'euw', 'eune', 'kr', 'jp',
           'br', 'oce', 'tr', 'lan', 'las', 'ru']

# Initialize the WebDriver and load login page.
driver = webdriver.Chrome()

# Get players from op.gg and loop through each server
for server in servers:
    summoner_names = []

    # Load page
    driver.get(
        f'https://www.op.gg/leaderboards/champions/{champ}?region={server}')
    time.sleep(3)

    # Get top three players
    elements = driver.find_elements(
        by=By.CSS_SELECTOR, value='.css-xhvjro.ecvvxrg1')
    for element in elements:
        summoner_names.append(
            element.find_element(by=By.CLASS_NAME, value='text-group')
            .find_element(by=By.TAG_NAME, value='a').text)

    # Get rest of players
    elements = driver.find_elements(
        by=By.CSS_SELECTOR, value='.css-1vdhwjr.e1g3wlsd8')
    for element in elements:
        summoner_names.append(
            element.find_element(by=By.TAG_NAME, value='a').text)

    # Print out results
    print(f'For server {server}, the top players are: ')
    print(summoner_names, '\n')
