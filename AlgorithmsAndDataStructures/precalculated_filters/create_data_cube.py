import csv
import itertools
import json
import sqlite3


# Load filters
def load_filter_options(json_file):
    with open(json_file, 'r') as file:
        data = json.load(file)

    # Dynamically prepend None to each filter option list
    for key in data.keys():
        data[key].insert(0, None)

    return data

# Open db connection
con = sqlite3.connect('cube.db')
cur = con.cursor()

# Load filter options
filter_options = load_filter_options('put path here!')

# Determine field names dynamically from the JSON keys plus an 'index' field
fieldnames = list(filter_options.keys())


writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
writer.writeheader()
primary_key = 0

# Generate all combinations of filter options
for combination in itertools.product(*filter_options.values()):
    row = {'index': primary_key}
    row.update(dict(zip(filter_options.keys(), combination)))
    writer.writerow(row)
