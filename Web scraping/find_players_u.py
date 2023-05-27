"""
This script aims to find and retrieve player names,
from the following video game analysis page:

https://u.gg/lol/champion-leaderboards/fiddlesticks?region=na1
"""
import requests
from bs4 import BeautifulSoup

# Get champion name
#champ = input('Please enter a champion name: ')
champ = 'leesin'
servers = ['na', 'euw', 'eune', 'kr', 'jp',
           'br', 'oce', 'tr', 'lan', 'las', 'ru']

# Get players from op.gg and loop through each server
for server in servers:
    summoner_names = []

    # Load page
    response = requests.get(
        f'https://u.gg/lol/champion-leaderboards/{champ}?region={server}').text

    soup = BeautifulSoup(response, 'html.parser')

    # Check if the request was successful
    if response.status_code == 200:
        html_content = response.text
    else:
        print(f"Error: {response.status_code}")

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
