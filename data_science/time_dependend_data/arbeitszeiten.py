import pandas as pd
import os
import seaborn as sns
import matplotlib.pyplot as plt
import numpy as np

# Get the directory of the current script
script_dir = os.path.dirname(os.path.realpath(__file__))

# Create the path of the file & load the data
file_path = os.path.join(script_dir, 'Arbeitszeiten.xlsx')
xl = pd.ExcelFile(file_path)

# Load a sheet into a DataFrame by its name
df1 = pd.read_excel(
    file_path, 
    sheet_name='Arbeitszeiten', 
    header=0, 
    index_col=0, 
    parse_dates=True)

# Sort the DataFrame by the index
df1.sort_index(inplace=True)

# Assuming df1 is a DataFrame and the first column is non-numerical
numerical_df = df1.select_dtypes(include=[np.number])
# sns.heatmap(numerical_df.T, annot=False, cmap='coolwarm')
sns.heatmap(numerical_df, annot=False, cmap='coolwarm')
plt.show()

'''
# Load a sheet into a DataFrame by its name
df1 = pd.read_excel(
    file_path, 
    sheet_name='Arbeitszeiten', 
    header=0, 
    index_col=0, 
    parse_dates=True)

# Sort the DataFrame by the index
df1.sort_index(inplace=True)

# Assuming df1 is a DataFrame and the first column is non-numerical
numerical_df = df1.select_dtypes(include=[np.number])

# Create the heatmap
ax = sns.heatmap(numerical_df, annot=False, cmap='coolwarm')

# Create a date range with a frequency of 30 days
date_range = pd.date_range(start=df1.index.min(), end=df1.index.max(), freq='30D')

# Format the dates in the date range to match the format of the DataFrame's index
formatted_dates = date_range.strftime('%Y-%m-%d')

# Set the x-tick labels to the formatted dates
ax.set_xticklabels(formatted_dates)

plt.show()
'''