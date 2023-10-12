import matplotlib.pyplot as plt
import streamlit as st


def activities_page() -> dict:
    # Sliders and diagram
    col1, col2 = st.columns(2)

    # Sliders used to specify the amount of time spent on each activity.
    with col1:
        # Subtitle and description
        st.header("How did you spend your day?")

        # Sliders
        sleep_hours = st.slider("Sleep", 0, 24, 8)
        study_hours = st.slider("Study", 0, 24, 4)
        work_hours = st.slider("Work", 0, 24, 4)
        free_time_hours = st.slider("Free time", 0, 24, 2)
        left_hours = 24 - sleep_hours - study_hours - work_hours - free_time_hours

        # Check if the user has entered more than 24 hours.
        if left_hours < 0:
            st.error("You have entered more than 24 hours!")
            st.stop()

        # Define the data for the pie chart.
        activities = ['Work', 'Study', 'Free time', 'Other', 'Sleep']
        hours = work_hours, study_hours, free_time_hours, left_hours, sleep_hours
        # https://matplotlib.org/stable/gallery/color/named_colors.html
        colors = ['lime', 'magenta', 'orange', 'dimgray', 'midnightblue']

    # Create the pie chart in the second column.
    with col2:
        fig1, ax1 = plt.subplots(figsize=(4, 2))

        # Match streamlits dark theme.
        fig1.patch.set_facecolor('#0F1018')

        ax1.pie(hours, labels=activities, colors=colors, autopct='%1.f%%')
        ax1.axis('equal')

        # Adjust the color of the text to match the dark theme.
        for text in ax1.texts:
            text.set_color('white')

        st.pyplot(fig1)

    # Subtitle and description
    st.header("Tag your day using these!")
    # Checkboxes for tagging the day.
    tags = st.multiselect(
        "Tag your day!", label_visibility='collapsed', help="Select all that apply",
        options=['productive', 'relaxed',
                 'meeting', 'university',
                 'friends', 'colleagues', 'family', 'partner',
                 'happy', 'sad', 'exited', 'tired', 'depressed',
                 'junk food', 'busy',
                 'hot', 'cold', 'rainy'], key=1)
