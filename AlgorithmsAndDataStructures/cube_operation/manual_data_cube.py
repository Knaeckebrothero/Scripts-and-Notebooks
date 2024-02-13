import pandas as pd
import itertools


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


# Sample data keys
data_keys = {
    'keyfigure': [None, 1, 2],
    'state': [None, 1, 2],
    'year': [None, 1, 2]
}

# Sample data
data = {
    'keyfigure': [1, 2, 1, 2, 1, 2, 1, 2, 1, 2],
    'year': [1, 2, 1, 2, 1, 2, 1, 2, 1, 2],
    'state': [1, 2, 1, 2, 1, 2, 1, 2, 1, 2],
    'value': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
}

# Convert to a pandas DataFrame
data_df = pd.DataFrame(data)

# Generate all possible combinations of filter options
combinations = list(itertools.product(*data_keys.values()))

# Convert combinations to DataFrame
filter_df = pd.DataFrame(combinations, columns=data_keys.keys())

print(filter_df)

# Initialize a DataFrame to store results
results_df = pd.DataFrame()

# Iterate over each combination
for index, row in filter_df.iterrows():

    # Apply filters based on the combination
    filtered_df = data_df

    for column in filter_df.columns:
        # Only apply filter if not None
        if pd.notna(row[column]):
            filtered_df = filtered_df[filtered_df[column] == row[column]]

    # Skip empty filtered data or has less than 5 rows
    if filtered_df.empty or len(filtered_df) < 5:
        continue

    # Calculate stats for the filtered data
    stats = calculate_stats(filtered_df['value'])

    # Exclude None from the update
    stats.update({k: v for k, v in row.to_dict().items() if pd.notna(v)})
    # Convert stats dict to a DataFrame
    stats_df = pd.DataFrame([stats])
    results_df = pd.concat([results_df, stats_df], ignore_index=True)

# Display final results
print(results_df)
