import csv
import itertools
import random

#  Define the field names for the CSV file
fieldnames = ['index', 'keyFigure', 'state', 'year', 'branch', 'value']

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
        writer.writerow({
            'index': primary_key,
            'keyFigure': keyFigure,
            'state': state,
            'year': year,
            'branch': branch,
            'value': random.randint(25, 100)})

        # Increment the primary key
        primary_key += 1

"""
import csv
import itertools
import json
import pandas as pd


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


# Function to calculate stats
def calculate_stats(filter_result):
    q1 = filter_result.quantile(0.25)
    q3 = filter_result.quantile(0.75)
    iqr = q3 - q1

    # Calculating the lower and upper whiskers
    lower_whisker = q1 - 1.5 * iqr
    upper_whisker = q3 + 1.5 * iqr

    return {
        'Average': round(filter_result.mean()),
        'Quantile_25': round(q1),
        'Quantile_50': round(filter_result.quantile(0.50)),
        'Quantile_75': round(q3),
        'Lower_Whisker': round(lower_whisker),
        'Upper_Whisker': round(upper_whisker)
    }


# Function to apply the stats calculation to each group
def calculate_group_stats(group):
    stats = calculate_stats(group['value'])
    stats.update({
        'keyfigure': group.name[0],
        'state': group.name[1],
        'year': group.name[2]
    })
    return pd.Series(stats)


# Sample data
data = {
    'keyfigure': [1, 2, 1, 2, 1, 2, 1, 2, 1, 2],
    'year': [1, 2, 1, 2, 1, 2, 1, 2, 1, 2],
    'state': [1, 2, 1, 2, 1, 2, 1, 2, 1, 2],
    'value': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
}

# Convert to a pandas DataFrame
data_df = pd.DataFrame(data)

# Group data by the desired columns
grouped_df = data_df.groupby(['keyfigure', 'state', 'year'])

# Apply the function to each group and reset index to flatten the DataFrame
results_df = grouped_df.apply(calculate_group_stats).reset_index(drop=True)

# Display final results
print(results_df)

# Specify the path
json_file_path = 'filter_options.json'
csv_file_name = 'test_cube.csv'

# Create the CSV file
create_csv_from_filters(json_file_path, csv_file_name)
"""