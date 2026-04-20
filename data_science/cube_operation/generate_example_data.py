import numpy as np
import pandas as pd

# Define the range for each key
keyfigures = [1, 2, 3]
states = [1, 2, 3]
years = [1, 2, 3]

# Generate all combinations of keyfigures, states, and years
combinations = [(keyfigure, state, year) for keyfigure in keyfigures for state in states for year in years]

# Generate random keyfigure_values for each combination
keyfigure_values = np.random.randint(10, 100, size=len(combinations))

# Create the DataFrame
data_df = pd.DataFrame(combinations, columns=['keyfigure', 'state', 'year'])
data_df['keyfigure_value'] = keyfigure_values

# Save the DataFrame to a CSV file
data_df.to_csv('example_data.csv', index=False)

print("Data saved to 'example_data.csv'.")
