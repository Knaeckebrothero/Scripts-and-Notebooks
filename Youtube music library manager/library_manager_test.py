"""
https://ytmusicapi.readthedocs.io/

This is a short script that helps you migrate your library from one account onto another.

songs
liked songs
playlists
albums

uploaded songs
uploaded liked songs
uploaded playlists
uploaded albums

tasteprofile
"""
import json

from ytmusicapi import YTMusic


# 1 To authenticate the script go and read https://ytmusicapi.readthedocs.io/en/stable/setup.html
def create_auth():
    headers = """
    paste here
    """
    YTMusic.setup(filepath="old_auth.json", headers_raw=headers)

# 2 Retrieve your songs and saves them in a json file
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
"""

def retrieve_songs(credentials: str):
    with YTMusic(credentials) as youtube:
        songs = youtube.get_library_songs(limit=999)
        print("Your library contains: ", len(songs), " songs.")

        with open('LibrarySongs.json', 'a', newline="") as f:
            for song in songs:
                f.write(json.dumps(song) + "\n")
            f.close()


def retrieve_uploaded_songs(credentials: str):
    with YTMusic(credentials) as youtube:
        songs = youtube.get_library_upload_songs(limit=999)
        print("Your library contains: ", len(songs), " uploaded songs.")

        with open('LibraryUploadedSongs.json', 'a', newline="") as f:
            for song in songs:
                f.write(json.dumps(song) + "\n")
            f.close()


# 2 Retrieve your playlists and saves them in a json file
def retrieve_playlists(credentials: str):
    with YTMusic(credentials) as youtube:
        playlists = youtube.get_library_playlists()

        with open('LibrarySongs.json', 'a', newline="") as f:
            for playlist in playlists:
                f.write(json.dumps(playlist) + "\n")
            f.close()


# 4 Insert your library contents
def insert_songs():
    with YTMusic('headers_new.json') as yt:
        pass


retrieve_songs('old_auth.json')
retrieve_uploaded_songs('old_auth.json')

"""
