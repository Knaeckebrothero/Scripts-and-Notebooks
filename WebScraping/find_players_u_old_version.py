"""
This script aims to find and retrieve player names,
from the following video game analysis page:

https://u.gg/lol/champion-leaderboards/leesin?region=na1
https://u.gg/robots.txt
"""

import asyncio
from pyppeteer import launch
from bs4 import BeautifulSoup

"""
async def get_page_html(page_url):
    browser = await launch()
    page = await browser.newPage()
    await page.setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                            'AppleWebKit/537.36 (KHTML, like Gecko) '
                            'Chrome/58.0.3029.110 Safari/537.3')
    await page.goto(page_url)
    await page.waitForSelector('.summoner-name')
    page_html = await page.content()
    await browser.close()
    return page_html

"""

async def get_page_html(page_url):
    browser = await launch(headless=False)  # Set headless=False
    page = await browser.newPage()
    await page.setViewport({'width': 1280, 'height': 800})
    await page.evaluate('window.scrollBy(0, 1000)')
    await page.setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3')
    await page.goto(page_url)
    await page.waitForSelector('.summoner-name')
    page_html = await page.content()
    await browser.close()
    return page_html


# Set champion name and servers
champ = 'leesin'
servers = ['na', 'euw', 'eune', 'kr', 'jp',
           'br', 'oce', 'tr', 'lan', 'las', 'ru']

# Get players from u.gg and loop through each server
for server in servers:
    summoner_names = []

    # Load the page
    url = f'https://u.gg/lol/champion-leaderboards/{champ}?region={server}1'
    html = asyncio.get_event_loop().run_until_complete(get_page_html(url))
    soup = BeautifulSoup(html, 'html.parser')

    print(soup.prettify())

    # Find all the summonernames
    elements = soup.find_all(class_='summoner-name')

    print(len(elements))

    # Extract the summonernames and add them to the list
    summoner_names.extend(elem.text for elem in elements)

    # Print the summonernames
    print(summoner_names)
