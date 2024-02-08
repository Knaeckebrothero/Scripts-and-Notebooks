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

def create_fact_table(cur, table_name, fieldnames):
    # Updated SQL statement with the specified attributes
    sql_statement = f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
        Quantile_25 REAL,
        Quantile_50 REAL,
        Quantile_75 REAL,
        Average REAL,
        """ + ', '.join([f"{name} Integer" for name in fieldnames]) 
    + ")"

    # Execute the SQL statement
    cur.execute(sql_statement)


# Load filter options
filter_options = load_filter_options('put path here!')

# Open db connection
con = sqlite3.connect('cube.db')
cur = con.cursor()

# Call the function to create the fact table with the specified attributes
create_fact_table(cur, 'FactTable', filter_options.keys())

# Commit the changes and close the connection
con.commit()

# Generate all combinations of filter options
for combination in itertools.product(*filter_options.values()):
    cur
    .update(dict(zip(filter_options.keys(), combination)))

con.close()
