"""
This script aims to find and retrieve player names,
from the following video game analysis page:

https://u.gg/lol/champion-leaderboards/leesin?region=na1
https://u.gg/robots.txt
"""

import time
from selenium import webdriver
from selenium.webdriver.common.by import By

# Get champion name
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
        f'https://u.gg/lol/champion-leaderboards/{champ}?region={server}1')
    time.sleep(3)

    # Get top three players
    elements = driver.find_elements(
        by=By.CLASS_NAME, value='summoner-name')

    # Extract the summonernames and add them to the list
    summoner_names.extend(elem.text for elem in elements if elem.text)

    # Print out results
    print(f'For server {server}, the top players are: ')
    print(summoner_names, '\n')
