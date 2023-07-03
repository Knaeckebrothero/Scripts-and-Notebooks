import os
import streamlit as st
import pandas as pd
from datetime import datetime

# Set config to always start with wide layout
st.set_page_config(layout="wide")

# Main title
st.title('Welcome to your digital diary!')

# Select date for diary entry
today = st.text_input(
    'If you want to change the date please do so, if not simply continue...',
    value=datetime.today().strftime('%d.%m.%Y'),
    max_chars=None, key=None, type='default')

# Start of by rating the day on a scale from 1 to 10
st.header('How would you rate your day in terms of the following?')

# Overall rating
overall_rating = st.slider('Overall:', 1, 10, 5, key='or')
overall_notes = st.text_area('Add notes as needed.', max_chars=200, key='on')

# Emotional rating
emotional_rating = st.slider('Emotional:', 1, 10, 5, key='er')
emotional_notes = st.text_area('Add notes as needed.', max_chars=200, key='en')

# Mental rating
mental_rating = st.slider('Mental:', 1, 10, 5, key='mr')
mental_notes = st.text_area('Add notes as needed.', max_chars=200, key='mn')

# Number of activities
st.header('Please state the number of hours you spent on the following activities.')
sleep_hours = st.number_input('Sleep:', min_value=0, max_value=24, value=0)
work_hours = st.number_input('Work:', min_value=0, max_value=24, value=0)
study_hours = st.number_input('Study:', min_value=0, max_value=24, value=0)
free_time_hours = st.number_input('Free time:', min_value=0, max_value=24, value=0)

# Text area for writing about the day
st.header('Was there something else you wanna mention?')
notes = st.text_area('Write anything about your day here...', max_chars=500)

# Save Button
st.header('Save and print your day!')
if st.button('Save Diary'):
    diary_data = {
        'Date': [datetime.today().strftime('%Y-%m-%d')],
        'Overall Rating': [overall_rating],
        'Emotional Rating': [emotional_rating],
        'Mental Rating': [mental_rating],
        'Sleep Hours': [sleep_hours],
        'Work Hours': [work_hours],
        'Study Hours': [study_hours],
        'Free Time Hours': [free_time_hours],
        'Notes': [notes]
    }

    # Create DataFrame
    df = pd.DataFrame(diary_data)

    # Append DataFrame to CSV, without the header if the file already exists
    dir_path = os.path.dirname(os.path.realpath(__file__))
    with open(os.path.join(dir_path, 'diary.csv'), 'a') as f:
        df.to_csv(f, header=f.tell() == 0, index=False)
