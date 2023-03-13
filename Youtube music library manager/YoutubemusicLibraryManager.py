"""
https://ytmusicapi.readthedocs.io/

This is a short script that helps you migrate your library from one account onto another.
"""
import json

from ytmusicapi import YTMusic
from marshmallow import Schema, fields

# 1 To authenticate the script go and read https://ytmusicapi.readthedocs.io/en/stable/setup.html
headers = "paste content here"
# YTMusic.setup(filepath="headers_auth.json", headers_raw=headers)

# 2 Retrieve your library contents
with YTMusic('headers_auth.json') as yt:
    songs = yt.get_library_songs(limit=999)
    print(len(songs))
    """
    Song dic structure:
    videoId				        str
    title				                str
    artists				            list
    album				            list
    likeStatus			        str
    thumbnails  		        list
    isAvailable 		        bool
    isExplicit  		            bool
    videoType   		        str
    duration    		            str
    duration_seconds	    int
    feedbackTokens        list
    """

    with open('LibrarySongs.json', 'a', newline="") as f:
        for song in songs:
            f.write(json.dumps(song)+"\n")
        f.close()

# 3 Insert your library contents
with YTMusic('headers_new.json') as yt:
    pass
