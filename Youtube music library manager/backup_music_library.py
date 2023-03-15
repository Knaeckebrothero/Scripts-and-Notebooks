"""
https://ytmusicapi.readthedocs.io/

This is a short script that helps you back up your YouTube music library.

Create files containing you songs and playlists
"""
from ytmusicapi import YTMusic
import json

# Pretends to be a browser by using a copied request.
with YTMusic('new_auth.json') as yt:
    # Retries songs and playlists.
    songs = yt.get_library_songs(limit=9999)
    playlists = yt.get_library_playlists(limit=9999)

    # Writes songs into a file.
    with open('LibrarySongs.json', 'w', newline="") as f:
        for song in songs:
            f.write(json.dumps(song) + "\n")
        f.close()

    # Iterates over the playlists and saves everyone in an individual file.
    for lib_pl in playlists:
        pl = yt.get_playlist(lib_pl['playlistId'])
        with open(pl['title'].replace(" ", "_")+'.json', 'w') as f:
            f.write(json.dumps(pl))
            f.close()

    # Prints the results
    print(len(songs), "songs and", len(playlists), "playlists have been saved!")
