import os
import dotenv
import mysql.connector
import datetime as dt
import pandas as pd


# Function to convert the JSON data into a DataFrame that matches the database schema
def convert_to_db_formate(data: pd.DataFrame) -> pd.DataFrame:
    result = pd.DataFrame()

    parsed_date = dt.datetime.strptime(data['date'], '%d.%m.%Y')
    result['diary_day']['today_date'] = parsed_date.strftime('%Y-%m-%d')
    result['diary_day']['fell_asleep'] = data['fell_asleep']
    result['diary_day']['woke_up'] = data['woke_up']

    return result


# Load environment variables
dotenv.load_dotenv(dotenv.find_dotenv())

# Establish a database connection
conn = mysql.connector.connect(
    host=os.getenv('DATABASE_HOST'),
    user=os.getenv('DATABASE_USER'),
    password=os.getenv('DATABASE_PASSWORD'),
    database=os.getenv('DATABASE_NAME')
)

# Create a cursor
cursor = conn.cursor()
path = os.getenv('JSON_PATH')

# Load the JSON data
for file in os.listdir(path):
    json_data = pd.read_json(path + file)

# Transform the data (if necessary)
# This step will depend on the structure of your JSON data and your database schema

# Load the data into MySQL
for index, row in data.iterrows():
    # Construct the SQL query
    query = """
        INSERT INTO diary_day (today_date, fell_asleep, woke_up, focus, start_mood, end_mood, satisfaction)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """
    # Execute the query with the data from the current row
    cursor.execute(query, tuple(row))

# Commit the transactions
conn.commit()

# Close the database connection
cursor.close()
conn.close()
