import cv2
import pandas as pd
import matplotlib.pyplot as plt
import os

# Get the directory of the current script
script_dir = os.path.dirname(os.path.realpath(__file__)) + '\\'

# Load the image
img = cv2.imread(script_dir + 'map.png')

# Load the CSV data
data = {
    'Station': ['START', 'STATION 1', 'STATION 2', 'STATION 3', 'STATION 4', 'STATION 5', 'STATION 6', 'ENDE'],
    'Position': ['Bushaltestelle Nibelungenallee', 'Ecke Friedberger Landstraße / Schwarzburgstraße', 'Ecke Schwarzburgstraße / Lenaustraße', 'Ecke Lenaustraße / Nordendstraße', 'Ecke Nordendstraße / Spohrstraße', 'Ecke Spohrstraße / Schwarzburgstraße', 'Ecke Friedberger Landstraße / Schwarzburgstraße', 'Bushaltestelle Nibelungenallee'],
    'Herzfrequenz': [None, 75.0, 80.0, 90.0, 90.0, 120.0, 120.0, 130.0],
    'Geschwindigkeit auf letztem Wegstück': [None, '4 km/h', '4 km/h', '4 km/h', '4 km/h', '6 km/h', '6 km/h', '12 km/h'],
    'x': [100, 200, 300, 400, 500, 600, 700, 800],  # Replace with actual x coordinates
    'y': [100, 200, 300, 400, 500, 600, 700, 800]   # Replace with actual y coordinates
}
df = pd.DataFrame(data)

# Loop through the data and overlay it on the image
for index, row in df.iterrows():
    # Draw a circle at the data point
    cv2.circle(img, (int(row['x']), int(row['y'])), radius=5, color=(0, 255, 0), thickness=-1)
    
    # Put the data as text next to the point
    cv2.putText(img, str(row['Herzfrequenz']), (int(row['x']) + 10, int(row['y']) + 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

# Display the image with data overlay
plt.imshow(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
plt.show()
