import streamlit as st


def rate_page():
    # Sliders and diagram
    col1, col2 = st.columns(2)

    # Sliders used to judge different activities
    with col1:
        # Subtitle and description
        st.header("How would you judge your...")

        # Sliders
        focus = st.slider("ability to stay focused during study or work.", 1, 5, 3)
        starting_mood = st.slider("mood at the start of the day.", 1, 5, 3)
        ending_mood = st.slider("mood at the end of the day.", 1, 5, 3)
        satisfaction = st.slider("satisfaction with what you have achieved today.", 1, 5, 3)

    # Create the pie chart in the second column.
    with col2:
        focus_comment = st.text_input("Anything you wanna mention?")

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
                 'hot', 'cold', 'rainy'], key=2)
