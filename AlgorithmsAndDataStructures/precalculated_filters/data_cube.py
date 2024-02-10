import pandas as pd
import numpy as np
import itertools


# Function to calculate stats
def calculate_stats(filter_result):
    return {
        'Average': round(filter_result.mean(), 2),
        'Quantile_25': round(filter_result.quantile(0.25), 2),
        'Quantile_50': round(filter_result.quantile(0.50), 2),
        'Quantile_75': round(filter_result.quantile(0.75), 2)
    }


# Sample data keys
data_keys = {
    'keyfigure': [None, 1, 2, 3],
    'state': [None, 1, 2, 3],
    'year': [None, 1, 2, 3]
}

# Sample data
data = {
    'keyfigure': [1, 1, 1, 2, 2, 2, 3, 3, 3],
    'year': [1, 2, 3, 1, 2, 3, 1, 2, 3],
    'state': [1, 2, 3, 1, 2, 3, 1, 2, 3],
    'keyfigure_value': np.random.randint(10, 100, size=9)
}

# Convert your sample data to DataFrame
data_df = pd.DataFrame(data)

# Generate all combinations of filter options
combinations = list(itertools.product(*data_keys.values()))

# Convert combinations to DataFrame
filter_df = pd.DataFrame(combinations, columns=data_keys.keys())

# Initialize a DataFrame to store results
results_df = pd.DataFrame()

i = 1

# Iterate over each combination
for index, row in filter_df.iterrows():
    # Apply filters based on the combination
    filtered_df = data_df

    for column in filter_df.columns:
        # Only apply filter if not None
        if pd.notna(row[column]):
            filtered_df = filtered_df[filtered_df[column] == row[column]]

    # Skip empty filtered data
    if filtered_df.empty:
        continue

    # Calculate stats for the filtered data
    stats = calculate_stats(filtered_df['keyfigure_value'])
    # Exclude None from the update
    stats.update({k: v for k, v in row.to_dict().items() if pd.notna(v)})
    # Convert stats dict to a DataFrame
    stats_df = pd.DataFrame([stats])
    results_df = pd.concat([results_df, stats_df], ignore_index=True)

    print(i)
    i += 1

# Display final results
print(results_df)
