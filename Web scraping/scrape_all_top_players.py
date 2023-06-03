"""
This script aims to find and retrieve player names,
from the following video game analysis pages:

https://www.op.gg/leaderboards/champions/fiddlesticks?region=na
https://u.gg/lol/champion-leaderboards/leesin?region=na1

Technically this script violates the u.gg robots.txt,
but is only for private purposes and to learn webscraping,
so it might be okay for now...
https://www.op.gg/robots.txt
https://u.gg/robots.txt
"""
import csv
import time
from selenium import webdriver
from selenium.webdriver.common.by import By


def get_players(champ, servers, link):
    # Initialize the WebDriver and matrix.
    driver = webdriver.Chrome()
    player_matrix = []

    # Get players from the website and loop through each server.
    for lol_server in servers:
        summoner_names = []

        # Load page
        driver.get(link + f'{champ}?region={lol_server}')
        time.sleep(3)

        # Get the player objects
        elements = driver.find_elements(by=By.CLASS_NAME, value='summoner-name')

        # Extract the summonernames and add them to the list
        summoner_names.extend(elem.text for elem in elements if elem.text)

        # Add the summoner names to the player matrix
        player_matrix.append(summoner_names)

    driver.quit()
    return player_matrix


def merge_players(op_player_list, u_player_list):
    merged_players = []
    for op, u in zip(op_player_list, u_player_list):
        merged = list(set(op) | set(u))
        merged_players.append(merged)
    return merged_players


# Declare variables
champ_name = input('Please enter a champion name: ')
op_servers = ['na', 'euw', 'eune', 'oce', 'kr', 'jp', 'br', 'tr', 'lan',
              'las', 'ru', 'sg', 'ph', 'tw', 'vn', 'th']
u_servers = ['na1', 'euw1', 'eun1', 'oc1', 'kr', 'jp1', 'br1', 'tr1', 'la1',
             'la2', 'ru', 'sg2', 'ph2', 'tw2', 'vn2', 'th2']

# Get the players from the websites
op_players = get_players(
    champ_name, op_servers, 'https://www.op.gg/leaderboards/champions/')

u_players = get_players(
    champ_name, u_servers, 'https://u.gg/lol/champion-leaderboards/')

# Iterate over both lists and combine them
players = merge_players(op_players, u_players)

# Write the players to a csv file
i = 0
for server in players:
    with open(f'{champ_name}_{op_servers[i]}_players.csv',
              'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        for name in server:
            writer.writerow([name])
    i += 1
