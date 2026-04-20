import streamlit as st
import subpages as ui
from datetime import datetime

if __name__ == '__main__':
    # Set config to always start with wide layout
    st.set_page_config(layout="wide", page_title="My Diary")

    # Main title
    st.title('Today is the {} welcome to your digital diary!'
             .format(datetime.today().strftime('%d.%m.%Y')))

    # Split application into tabs
    activities, rate, write = st.tabs(
        ["Specify your activities", "Rate your day", "Wanna mention something else?"])

    # Import tabs content/functionalities
    with activities:
        # Holds information about the daily activities
        ui.activities_page()

    with rate:
        # Used to judge the day
        ui.rate_page()

    with write:
        # Used to specify additional information
        ui.write_page()
