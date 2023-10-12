import streamlit as st
import json
import os
from datetime import datetime as dt, time

# Check if session state exists
if 'today' not in st.session_state:
    # Check if today's file exists
    filename = f"diary_{dt.today().strftime('%d%m%Y')}.json"
    filepath = './entries/' + filename
    if os.path.exists(filepath):
        # Load the 'today' dictionary from the JSON file
        with open(filepath, 'r') as f:
            st.session_state.today = json.load(f)
    else:
        # Initialize 'today' dictionary
        st.session_state.today = {"date": dt.today().strftime('%d-%m-%Y'),
                                  "changelog": [],
                                  "activity": [],
                                  "rating": {},
                                  "comment": {},
                                  }

# Config & initialisation
st.set_page_config(layout="wide", page_title="My Diary")

# Main title
st.title(
    'Today is the {} welcome to your digital diary'.format(
        dt.strptime(st.session_state.today["date"],
                    '%d-%m-%Y').strftime('%d.%m.%Y')))

# Split application into tabs
activities, rate, write, organize = st.tabs(
    ["What did you do today?",
     "Rate your day!",
     "Wanna mention something?",
     "Manage your diary's..."])

# Import tabs content/functionalities
with activities:
    # Specify how demanding the activity was.
    activity_category = st.selectbox('Tag based on cognitive demand',
                                     ['Concentration',
                                      'Relaxation',
                                      'Organizational'],
                                     index=2, key='activity_category')

    # Enter information about the activity
    activity_description = st.text_input(label='Describe your activity',
                                         key='activity_description',
                                         value="",
                                         placeholder='What did you do?')
    activity_start = st.time_input(
        'Start time', key='activity_start', value=time(0, 0))
    activity_end = st.time_input(
        'End time', key='activity_end', value=time(0, 0))

    # Tag your activity
    activity_tags = st.multiselect(
        "Activity was related to",
        options=['Academic', 'Business', 'Hobbies',
                 'Breaks', 'Leisure', 'Exercise',
                 'Housekeeping', 'Personal Care', 'Administrative'],
        key='activity_tags')

    # Spacing to make it look nicer
    st.markdown('<br/>', unsafe_allow_html=True)

    # Button to add a new activity
    if st.button("Add activity"):
        st.session_state.today["activity"].append(
            {"description": activity_description,
             "start": activity_start.strftime('%H:%M:%S'),
             "end": activity_end.strftime('%H:%M:%S'),
             "category": activity_category,
             "tags": activity_tags})
        st.success("Activity added successfully!")

with rate:
    # Sliders and diagram
    col1, col2 = st.columns(2)

    # Sliders used to judge different activities
    with col1:
        # Subtitle and description
        st.header("How would you judge your...")

        # Sliders
        st.session_state.today["rating"]["focus"] = st.slider(
            "Ability to stay focused throughout the day.", 1, 5, 3)
        st.session_state.today["rating"]["starting_mood"] = st.slider(
            "Mood at the start of the day.", 1, 5, 3)
        st.session_state.today["rating"]["ending_mood"] = st.slider(
            "Mood at the end of the day.", 1, 5, 3)
        st.session_state.today["rating"]["satisfaction"] = st.slider(
            "Satisfaction with what you have achieved today.", 1, 5, 3)

    # Create the pie chart in the second column.
    with col2:
        # Subtitle and description
        st.header("Anything you wanna mention?")

        st.session_state.today["rating"]["focus_comment"] = st.text_input(
            "Did you feel stressed?", key='focus_comment')

        st.markdown('<br/>', unsafe_allow_html=True)
        st.session_state.today["rating"]["start_mood_comment"] = st.text_input(
            "What was responsible for this?", key='start_mood_comment')
        st.session_state.today["rating"]["end_mood_comment"] = st.text_input(
            "What should I write here?", key='end_mood_comment',
            label_visibility='hidden')

        st.markdown('<br/>', unsafe_allow_html=True)
        st.session_state.today["rating"]["satisfaction_comment"] = st.text_input(
            "What would others say?", key='satisfaction_comment')

    # Subtitle and description
    st.header("Tag your day using these!")

    # Checkboxes for tagging the day.
    st.session_state.today["rating"]["tags"] = st.multiselect(
        "Tag your day!", label_visibility='collapsed', help="Select all that apply",
        options=['productive', 'relaxed', 'stressful', 'fun',
                 'friends', 'colleagues', 'family', 'partner',
                 'happy', 'sad', 'excited', 'tired', 'depressed',
                 'junk-food', 'drugs', 'insomnia', 'dispute',
                 'hot', 'cold', 'rainy'], key='tags')

with write:
    st.header("Is there anything else you wanna mention or comment on?")
    st.session_state.today['comment'] = st.text_area(
        label="Write here!", label_visibility='hidden',
        height=500, max_chars=1000, value="Heute habe ich")

with (organize):
    # Save the day
    if st.button("Save the day!"):
        # Add a new entry to the changelog
        st.session_state.today["changelog"].append(
            f"Day saved on {dt.now().strftime('%d-%m-%Y %H:%M:%S')}")

        # Create the directory if it doesn't exist
        if not os.path.exists('./entries/'):
            os.makedirs('./entries/')

        # Save the 'today' dictionary as a JSON file
        filename = f"diary_{st.session_state.today['date'].replace('-', '')}.json"
        with open('./entries/' + filename, 'w') as f:
            json.dump(st.session_state.today, f)

        st.success(f"Day saved as {filename}!")

    # Select date for diary entry
    st.session_state.today['date'] = st.date_input(
        'If you want to change the date please do so, if not simply continue...',
        value=dt.strptime(st.session_state.today['date'],
                          '%d-%m-%Y')).strftime('%d-%m-%Y')
