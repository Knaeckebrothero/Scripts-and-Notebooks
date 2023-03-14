"""
https://ytmusicapi.readthedocs.io/

This is a short script that helps you migrate your library from one account onto another.

songs
liked songs
playlists (need to be public)
albums (does not work properly)
"""
from ytmusicapi import YTMusic


# Function to get the feedbackTokens from all songs of the account library.
# It has to be mentioned that songs who are part of the library due to the album
# and have not been added individually, do not return feedbackTokens.
def get_lib_songs(path: str):
    tempsongs = []
    with YTMusic(path) as yt:
        for song in yt.get_library_songs(limit=9999):
            try:
                tempsongs.append(song['feedbackTokens']['add'])
            except:
                print("Could not get song ID from: \n", song, "\n")
    yt.__exit__()
    return tempsongs


# Function to get the ids of songs contained by the titles I liked playlist.
def get_lib_liked_songs(path: str):
    tempsongs = []
    with YTMusic(path) as yt:
        for lsong in yt.get_liked_songs(limit=9999)['tracks']:
            try:
                tempsongs.append(lsong['videoId'])
            except:
                print("Could not get song ID from liked song: \n", lsong, "\n")
    yt.__exit__()
    return tempsongs


# Function to get the ids of all playlists from the users library.
def get_lib_playlists(path: str):
    with YTMusic(path) as yt:
        try:
            return yt.get_library_playlists(limit=9999)
        except:
            print("Could not get playlists...\n")
    yt.__exit__()


# Divides a list into n number of chunks.
def divide_chunks(l, n):
    for i in range(0, len(l), n):
        yield l[i:i + n]


# Retrieve old accounts library content´s by using the functions defined above.
print("Retriving your old library...\nSongs")
songs = list(divide_chunks(get_lib_songs('old_auth.json'), 25))
print("Liked songs")
liked_songs = get_lib_liked_songs('old_auth.json')
print("Playlists")
playlists = get_lib_playlists('old_auth.json')

# Add the retrieved contents to the new account.
with YTMusic('new_auth.json') as newtube:
    print("Adding your library contents to the new account...\nSongs")

    for chunk in songs:
        newtube.edit_song_library_status(chunk)

    print("Liked songs")
    i = 0
    for song in liked_songs:
        try:
            newtube.rate_song(song, 'LIKE')
        except:
            i = i + 1
    if i > 0:
        print("Failed to like: ", i, " songs...")

    """
    print("Playlists")
    for playlist in playlists:
        try:
            newtube.create_playlist(title=playlist['title'], description=playlists['description'],
                                    source_playlist=playlist['playlistId'])
        except:
            print("Error when creating playlist...\n", playlist['title'], " ", playlist['playlistId'])
    """
    newtube.__exit__()

# Compares both accounts and prints the results.
print("\n\n")
print("Your old account had a total of: ", len(get_lib_songs('old_auth.json')), " retrievable songs.")
print("Your new account has a total of: ", len(get_lib_songs('new_auth.json')), " songs.")
print("\n\n")
print("Your old account had a total of: ", len(get_lib_liked_songs('old_auth.json')), " songs liked.")
print("Your new account has a total of: ", len(get_lib_liked_songs('new_auth.json')), " songs liked.")

"""
# Sadly albums are implemented very poorly and would requre the retrieve the albums,
# to get the album id, to then retrieve them again,
# because the library albums do not come with a list of titles, which is required to get the song id,
# in order to add the songs to the library.

# I tried to add the albums by their feedbackTokens, but this did only work for some and
# my library did not contain many albums anyway.
# I also recommend to swap the albums manually and just add the songs you like.

albums = []
for album in oldtube.get_library_albums(limit=9999):
    try:
        for a in newtube.get_album(album['browseId'], limit=9999)['tracks']:
            albums.append(a['feedbackTokens']['add'])
    except:
        print("Could not get album ID from: \n", album, "\n")

.edit_song_library_status(albums)
"""
