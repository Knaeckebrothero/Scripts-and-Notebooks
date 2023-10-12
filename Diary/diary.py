import os
import json
import streamlit as st
from datetime import time
from datetime import datetime as dt


def get_path():
    # Load directory and filename
    filename = f"diary_{st.session_state['date'].strftime('%d%m%Y')}.json"
    filepath = './entries/' + filename
    return filepath


# Load a day
def load_day():
    # Create the directory if it doesn't exist
    if not os.path.exists('./entries/'):
        os.makedirs('./entries/')

    # Load directory and filename
    filepath = get_path()

    # Create the file if it doesn't exist
    if not os.path.exists(filepath):
        with open(filepath, 'w') as f:
            json.dump({
                "date": st.session_state['date'].strftime('%d.%m.%Y'),
                "activity": [],
                "fell_asleep": "00:00",
                "woke_up": "00:00",
                "important": [],
                "focus": 5,
                "starting_mood": 5,
                "ending_mood": 5,
                "satisfaction": 5,
                "tags": [],
                "focus_comment": "",
                "start_mood_comment": "",
                "end_mood_comment": "",
                "satisfaction_comment": "",
                "comment": "",
                "important_comment": False,
                "changelog": []
            }, f)

    # Load the day
    with open(filepath, 'r') as f:
        st.session_state.today = json.load(f)


# Save a day
def save_day():
    # Save the day
    with open(get_path(), 'w') as f:
        json.dump(st.session_state.today, f)


# Config & initialisation
st.set_page_config(layout="wide", page_title="My Diary",
                   initial_sidebar_state="collapsed", page_icon=":notebook:")

# Check if the date is in session state
if 'date' not in st.session_state:
    st.session_state['date'] = dt.today()

# Check if today is in session state
if 'today' not in st.session_state or st.session_state['today'] is None:
    load_day()
    print(st.session_state['today']['date'] + " called")

# Set page title
st.title('Today is the {} welcome to your digital diary'.format(
    st.session_state['date'].strftime('%d.%m.%Y')))

# Columns for changing the date and saving the day
col1, col2, col3 = st.columns(3)

# Date input for changing the date
st.session_state.date = col1.date_input(
    "Today's Date", value=st.session_state.date)

# Timestamps for tracking sleep cycle
st.session_state.today['fell_asleep'] = col2.time_input(
    'Slept from', key='sleep', value=dt.strptime(
        st.session_state.today['fell_asleep'], '%H:%M').time()).strftime('%H:%M')
st.session_state.today['woke_up'] = col3.time_input(
    'Slept until', key='wake', value=dt.strptime(
        st.session_state.today['woke_up'], '%H:%M').time()).strftime('%H:%M')

# Split application into tabs
activities, rate, write = st.tabs(
    ["What did you do today?",
     "Rate your day!",
     "Wanna mention something?"])

# Activities section
with activities:
    if 'activity_start' not in st.session_state:
        st.session_state.activity_category_default = 2
        st.session_state.activity_description = ""
        st.session_state.activity_start = time(0, 0)
        st.session_state.activity_end = time(0, 0)
        st.session_state.activity_tags = []

    # Specify how demanding the activity was.
    st.session_state.activity_category = st.selectbox(
        'Tag based on cognitive demand',
        ['Concentration',
         'Relaxation',
         'Organizational'],
        key='activity_category_widget', index=st.session_state.activity_category_default)

    # Specify the activity name.
    st.session_state.activity_description = st.text_input(
        label='Describe your activity',
        key='activity_description_widget',
        placeholder='What did you do?',
        value=st.session_state.activity_description)

    # Specify the start and end time of the activity.
    st.session_state.activity_start = st.time_input(
        'Start time', key='activity_start_widget', value=st.session_state.activity_start)
    st.session_state.activity_end = st.time_input(
        'End time', key='activity_end_widget', value=st.session_state.activity_end)

    # Tag your activity
    st.session_state.activity_tags = st.multiselect(
        "Activity was related to",
        options=['Academic', 'Business', 'Hobbies',
                 'Breaks', 'Leisure', 'Exercise',
                 'Housekeeping', 'Personal Care', 'Administrative'],
        key='activity_tags_widget', default=st.session_state.activity_tags)

    # Add column for button and last activity
    col1, col2 = st.columns(2)

    # Button to add a new activity
    if col1.button("Add activity"):
        # Append activity to the list
        st.session_state.today["activity"].append(
            {"description": st.session_state['activity_description'],
             "start": st.session_state['activity_start'].strftime('%H:%M'),
             "end": st.session_state['activity_end'].strftime('%H:%M'),
             "category": st.session_state['activity_category'],
             "tags": st.session_state['activity_tags']})

        # Reset activity input fields
        st.session_state.activity_category_default = 2
        st.session_state.activity_description = ""
        st.session_state.activity_start = st.session_state['activity_end']
        st.session_state.activity_end = time(0, 0)
        st.session_state.activity_tags = []
        st.experimental_rerun()

    # Last activity
    with col2:
        activity_len = len(st.session_state.today["activity"])
        if activity_len > 0:
            previous_activity = 'Previous activity was: {} | ended: {}'.format(
                st.session_state.today["activity"][activity_len - 1]["description"],
                st.session_state.today["activity"][activity_len - 1]["end"])
        else:
            previous_activity = "No previous activity"
        st.text(previous_activity)

    important_habit = st.text_input(label='Keep track and test new stuff!',
                                    placeholder='Could be anything like a habit or food supplements...')
    if st.button("Mark today"):
        st.session_state.today['important'].append(important_habit)

# Rate section
with rate:
    # Sliders and diagram
    col1, col2 = st.columns(2)

    # Sliders used to judge different activities
    with col1:
        # Subtitle and description
        st.header("How would you judge your...")

        # Sliders
        st.session_state.today["focus"] = st.slider(
            "Ability to stay focused throughout the day.", 1, 10,
            value=st.session_state.today["focus"])
        st.session_state.today["starting_mood"] = st.slider(
            "Mood at the start of the day.", 1, 10,
            value=st.session_state.today["starting_mood"])
        st.session_state.today["ending_mood"] = st.slider(
            "Mood at the end of the day.", 1, 10,
            value=st.session_state.today["ending_mood"])
        st.session_state.today["satisfaction"] = st.slider(
            "Satisfaction with what you have achieved today.", 1, 10,
            value=st.session_state.today["satisfaction"])

    with col2:
        # Subtitle and description
        st.header("Anything you wanna mention?")

        st.session_state.today["focus_comment"] = st.text_input(
            "Did you feel stressed?", key='focus_comment',
            value=st.session_state.today["focus_comment"])

        st.markdown('<br/>', unsafe_allow_html=True)
        st.session_state.today["start_mood_comment"] = st.text_input(
            "What was responsible for this?", key='start_mood_comment',
            value=st.session_state.today["start_mood_comment"])
        st.session_state.today["end_mood_comment"] = st.text_input(
            "What should I write here?", key='end_mood_comment',
            label_visibility='hidden', value=st.session_state.today["end_mood_comment"])

        st.markdown('<br/>', unsafe_allow_html=True)
        st.session_state.today["satisfaction_comment"] = st.text_input(
            "What would others say?", key='satisfaction_comment',
            value=st.session_state.today["satisfaction_comment"])

    # Subtitle and description
    st.header("Tag your day using these!")

    # Checkboxes for tagging the day.
    st.session_state.today["tags"] = st.multiselect(
        "Tag your day!", label_visibility='collapsed', help="Select all that apply",
        options=['productive', 'relaxed', 'stressful', 'fun',
                 'friends', 'colleagues', 'family', 'partner',
                 'happy', 'sad', 'excited', 'tired', 'depressed',
                 'junk-food', 'drugs', 'insomnia', 'dispute',
                 'hot', 'cold', 'rainy'], key='tags',
        default=st.session_state.today["tags"])

# Write section
with write:
    st.header("Is there anything else you wanna mention or comment on?")
    st.session_state.today['comment'] = st.text_area(
        label="Write here!", label_visibility='hidden',
        height=400, max_chars=1000, placeholder="Heute habe ich...",
        value=st.session_state.today['comment'])

    # Checkboxes for tagging today's comment as important.
    if st.checkbox("Contains important information",
                   value=st.session_state.today['important_comment']):
        st.session_state.today['important_comment'] = True
    else:
        st.session_state.today['important_comment'] = False

if st.session_state['date'].strftime('%d.%m.%Y') != st.session_state.today['date']:
    # Delete today from st.session_state and rerun script to create or load the new day
    load_day()
    st.experimental_rerun()
else:
    # Save changes to file on script rerun
    save_day()
