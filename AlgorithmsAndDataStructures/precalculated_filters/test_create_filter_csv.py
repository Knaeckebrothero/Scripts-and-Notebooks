import csv
import itertools
import json


def load_filter_options(json_file):
    with open(json_file, 'r') as file:
        data = json.load(file)

    # Dynamically prepend None to each filter option list
    for key in data.keys():
        data[key].insert(0, None)

    return data


def create_csv_from_filters(json_file, csv_file):
    filter_options = load_filter_options(json_file)

    # Determine field names dynamically from the JSON keys plus an 'index' field
    fieldnames = ['index'] + list(filter_options.keys())

    with open(csv_file, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        primary_key = 0

        # Generate all combinations of filter options
        for combination in itertools.product(*filter_options.values()):
            row = {'index': primary_key}
            row.update(dict(zip(filter_options.keys(), combination)))
            writer.writerow(row)
            primary_key += 1


# Specify the path
json_file_path = 'filter_options.json'
csv_file_name = 'filter_table.csv'

# Create the CSV file
create_csv_from_filters(json_file_path, csv_file_name)

"""
import csv
import itertools

#  Define the field names for the CSV file
fieldnames = ['index', 'keyFigure', 'state', 'year', 'branch']

# Arrays for the filter options (None is used as the no filter option)
keyFigures = [None, 1, 2]
states = [None, 'test1', 'test2']
years = [None, 1942, 2042]
branches = [None, 'test1', 'test2']

# Open a new CSV file for writing
with open('test_filter_table.csv', 'w', newline='') as csvfile:
    # Create a CSV writer object
    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

    # Write the header to the CSV file
    writer.writeheader()

    # Start pk at 0
    primary_key = 0

    # Create all possible combinations and loop through them
    for combination in itertools.product(keyFigures, states, years, branches):
        keyFigure, state, year, branch = combination

        # Write each combination to the CSV file
        writer.writerow({'index': primary_key, 'keyFigure': keyFigure, 'state': state, 'year': year, 'branch': branch})

        # Increment the primary key
        primary_key += 1
"""