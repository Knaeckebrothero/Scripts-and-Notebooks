import os
import streamlit as st
import pandas as pd
from datetime import datetime

# Title
st.title('{} Diary'.format(datetime.today().strftime('%d.%m.%Y')))

# Sliders for rating the day
st.header('Please rate your day on a scale of 1 to 10')
overall_rating = st.slider('Overall aspect:', 1, 10, 5, key='1')
emotional_rating = st.slider('Emotional aspect:', 1, 10, 5, key='2')
mental_rating = st.slider('Mental aspect:', 1, 10, 5, key='3')

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
