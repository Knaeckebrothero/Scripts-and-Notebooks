import io
import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime


# Page configuration
st.set_page_config(
    page_title="Financial Audit Sample Testing",
    page_icon="📊",
    layout="wide"
)

# Title
st.title("🏦 Financial Audit Sample Testing - Advanced Version")
st.markdown("---")

# Initialize session state
if 'data' not in st.session_state:
    st.session_state.data = None
if 'filtered_data' not in st.session_state:
    st.session_state.filtered_data = None

# Sidebar for file upload
with st.sidebar:
    st.header("📁 Data Import")

    # File upload
    uploaded_file = st.file_uploader(
        "Choose a file",
        type=['csv', 'xlsx', 'xls'],
        help="Upload CSV or Excel file containing financial data"
    )

    if uploaded_file is not None:
        try:
            # Read file based on extension
            if uploaded_file.name.endswith('.csv'):
                # Read CSV with European format
                st.session_state.data = pd.read_csv(
                    uploaded_file,
                    sep=';',
                    decimal=',',
                    parse_dates=['date'],
                    dayfirst=True
                )
            else:
                # Read Excel
                st.session_state.data = pd.read_excel(
                    uploaded_file,
                    parse_dates=['date']
                )
                # Convert to European decimal format if needed
                if 'value' in st.session_state.data.columns:
                    st.session_state.data['value'] = pd.to_numeric(
                        st.session_state.data['value'].astype(str).str.replace(',', '.'),
                        errors='coerce'
                    )

            st.success(f"✅ Loaded {len(st.session_state.data)} records")
            st.session_state.filtered_data = st.session_state.data.copy()

        except Exception as e:
            st.error(f"❌ Error loading file: {str(e)}")

    # Display data info
    if st.session_state.data is not None:
        st.markdown("---")
        st.subheader("📊 Data Overview")
        st.write(f"Total records: {len(st.session_state.data)}")
        st.write(f"Date range: {st.session_state.data['date'].min().strftime('%d-%m-%Y')} to {st.session_state.data['date'].max().strftime('%d-%m-%Y')}")

        # Quick stats
        total_debit = st.session_state.data[st.session_state.data['key figure'].str.endswith('DT')]['value'].sum()
        total_credit = abs(st.session_state.data[st.session_state.data['key figure'].str.endswith('CT')]['value'].sum())
        st.write(f"Total Debit: €{total_debit:,.2f}")
        st.write(f"Total Credit: €{total_credit:,.2f}")

# Main content area
if st.session_state.data is not None:
    # Create tabs
    tab1, tab2, tab3 = st.tabs(["🔍 Filter & Sample", "📋 Results", "📊 Analysis"])

    with tab1:
        col1, col2 = st.columns(2)

        with col1:
            st.subheader("🔍 Filter Options")

            # Date filter
            st.write("**Date Filter**")
            date_filter_option = st.radio(
                "Filter by date?",
                ["No filter", "Before specific date", "Date range"],
                horizontal=True
            )

            if date_filter_option == "Before specific date":
                max_date = st.date_input(
                    "Select maximum date",
                    value=st.session_state.data['date'].max(),
                    max_value=st.session_state.data['date'].max(),
                    min_value=st.session_state.data['date'].min(),
                    format="DD/MM/YYYY"
                )
            elif date_filter_option == "Date range":
                date_range = st.date_input(
                    "Select date range",
                    value=(st.session_state.data['date'].min(), st.session_state.data['date'].max()),
                    max_value=st.session_state.data['date'].max(),
                    min_value=st.session_state.data['date'].min(),
                    format="DD/MM/YYYY"
                )

            # Value filter
            st.write("**Value Filter**")
            value_filter = st.checkbox("Filter by value range")
            if value_filter:
                min_val, max_val = st.slider(
                    "Select value range",
                    min_value=float(st.session_state.data['value'].min()),
                    max_value=float(st.session_state.data['value'].max()),
                    value=(float(st.session_state.data['value'].min()),
                           float(st.session_state.data['value'].max())),
                    format="€%.2f"
                )

            # Account filter
            st.write("**Account Filter**")
            account_filter = st.checkbox("Filter by account type")
            if account_filter:
                account_types = st.multiselect(
                    "Select account types",
                    ["Debit (DT)", "Credit (CT)"],
                    default=["Debit (DT)", "Credit (CT)"]
                )

            # Apply filters button
            if st.button("🔄 Apply Filters", type="primary"):
                filtered = st.session_state.data.copy()

                # Apply date filter
                if date_filter_option == "Before specific date":
                    filtered = filtered[filtered['date'] <= pd.Timestamp(max_date)]
                elif date_filter_option == "Date range":
                    filtered = filtered[
                        (filtered['date'] >= pd.Timestamp(date_range[0])) &
                        (filtered['date'] <= pd.Timestamp(date_range[1]))
                        ]

                # Apply value filter
                if value_filter:
                    filtered = filtered[
                        (filtered['value'] >= min_val) &
                        (filtered['value'] <= max_val)
                        ]

                # Apply account filter
                if account_filter:
                    account_conditions = []
                    if "Debit (DT)" in account_types:
                        account_conditions.append(filtered['key figure'].str.endswith('DT'))
                    if "Credit (CT)" in account_types:
                        account_conditions.append(filtered['key figure'].str.endswith('CT'))

                    if account_conditions:
                        filtered = filtered[pd.concat(account_conditions, axis=1).any(axis=1)]

                st.session_state.filtered_data = filtered
                st.success(f"✅ Filters applied! {len(filtered)} records match criteria")

        with col2:
            st.subheader("🎲 Sample Selection")

            if st.session_state.filtered_data is not None:
                st.info(f"📊 Available records for sampling: {len(st.session_state.filtered_data)}")

                # Sample size selection
                sample_method = st.radio(
                    "Sample size method",
                    ["Fixed number", "Percentage"],
                    horizontal=True
                )

                if sample_method == "Fixed number":
                    sample_size = st.number_input(
                        "Number of samples",
                        min_value=1,
                        max_value=len(st.session_state.filtered_data),
                        value=min(5, len(st.session_state.filtered_data)),
                        step=1
                    )
                else:
                    sample_percent = st.slider(
                        "Percentage of samples",
                        min_value=1,
                        max_value=100,
                        value=10,
                        step=1
                    )
                    sample_size = max(1, int(len(st.session_state.filtered_data) * sample_percent / 100))
                    st.write(f"This will select {sample_size} records")

                # Random seed for reproducibility
                use_seed = st.checkbox("Use fixed random seed (for reproducibility)")
                if use_seed:
                    seed = st.number_input("Random seed", value=42, step=1)
                else:
                    seed = None

                # Generate sample button
                if st.button("🎯 Generate Random Sample", type="primary"):
                    if seed is not None:
                        sample = st.session_state.filtered_data.sample(n=sample_size, random_state=seed)
                    else:
                        sample = st.session_state.filtered_data.sample(n=sample_size)

                    st.session_state.sample = sample
                    st.success(f"✅ Generated {len(sample)} random samples!")
                    st.balloons()

    with tab2:
        st.subheader("📋 Sample Results")

        if 'sample' in st.session_state and st.session_state.sample is not None:
            # Display options
            col1, col2, col3 = st.columns(3)
            with col1:
                show_index = st.checkbox("Show index", value=False)
            with col2:
                highlight_negatives = st.checkbox("Highlight negative values", value=True)
            with col3:
                # Export options
                export_format = st.selectbox("Export format", ["CSV", "Excel", "JSON"])

            # Format the dataframe for display
            display_df = st.session_state.sample.copy()
            display_df['value'] = display_df['value'].apply(lambda x: f"€{x:,.2f}")
            display_df['date'] = display_df['date'].dt.strftime('%d-%m-%Y')

            # Apply highlighting if requested
            def highlight_negative(val):
                if isinstance(val, str) and val.startswith('€-'):
                    return 'color: red'
                return ''

            if highlight_negatives:
                styled_df = display_df.style.applymap(highlight_negative, subset=['value'])
                st.dataframe(styled_df, use_container_width=True, hide_index=not show_index)
            else:
                st.dataframe(display_df, use_container_width=True, hide_index=not show_index)

            # Export functionality
            st.markdown("---")
            col1, col2 = st.columns([3, 1])
            with col1:
                st.write("**Export Sample Results**")
            with col2:
                if export_format == "CSV":
                    csv = st.session_state.sample.to_csv(index=False, sep=';', decimal=',')
                    st.download_button(
                        label="📥 Download CSV",
                        data=csv,
                        file_name=f"audit_sample_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                        mime="text/csv"
                    )
                elif export_format == "Excel":
                    buffer = io.BytesIO()
                    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                        st.session_state.sample.to_excel(writer, index=False, sheet_name='Audit Sample')
                    buffer.seek(0)
                    st.download_button(
                        label="📥 Download Excel",
                        data=buffer,
                        file_name=f"audit_sample_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                elif export_format == "JSON":
                    json_str = st.session_state.sample.to_json(orient='records', date_format='iso', indent=2)
                    st.download_button(
                        label="📥 Download JSON",
                        data=json_str,
                        file_name=f"audit_sample_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                        mime="application/json"
                    )
        else:
            st.info("👆 Please generate a sample first using the 'Filter & Sample' tab")

    with tab3:
        st.subheader("📊 Sample Analysis")

        if 'sample' in st.session_state and st.session_state.sample is not None:
            # Basic statistics
            col1, col2 = st.columns(2)

            with col1:
                st.write("**Sample Statistics**")
                st.write(f"Sample size: {len(st.session_state.sample)}")
                st.write(f"Average value: €{st.session_state.sample['value'].mean():,.2f}")
                st.write(f"Median value: €{st.session_state.sample['value'].median():,.2f}")
                st.write(f"Standard deviation: €{st.session_state.sample['value'].std():,.2f}")

                # Account type breakdown
                dt_count = len(st.session_state.sample[st.session_state.sample['key figure'].str.endswith('DT')])
                ct_count = len(st.session_state.sample[st.session_state.sample['key figure'].str.endswith('CT')])
                st.write(f"Debit entries: {dt_count}")
                st.write(f"Credit entries: {ct_count}")

            with col2:
                st.write("**Value Distribution**")
                # Create bins for histogram
                fig = pd.DataFrame({
                    'Value': st.session_state.sample['value'],
                    'Type': st.session_state.sample['key figure'].str[-2:]
                })

                # Simple bar chart of value ranges
                bins = pd.cut(st.session_state.sample['value'], bins=5)
                bin_counts = bins.value_counts().sort_index()

                chart_data = pd.DataFrame({
                    'Range': [str(interval) for interval in bin_counts.index],
                    'Count': bin_counts.values
                })

                st.bar_chart(chart_data.set_index('Range'))

            # Monthly distribution
            st.write("**Monthly Distribution**")
            monthly = st.session_state.sample.groupby(st.session_state.sample['date'].dt.to_period('M')).size()
            monthly_df = pd.DataFrame({
                'Month': monthly.index.astype(str),
                'Count': monthly.values
            })
            st.bar_chart(monthly_df.set_index('Month'))

        else:
            st.info("👆 Please generate a sample first using the 'Filter & Sample' tab")

else:
    # Landing page when no data is loaded
    st.info("👈 Please upload a CSV or Excel file using the sidebar to get started")

    # Instructions
    with st.expander("📖 How to use this application"):
        st.markdown("""
        1. **Upload your data file** (CSV or Excel) using the sidebar
        2. **Apply filters** to narrow down the data based on date, value, or account type
        3. **Generate a random sample** by specifying the number of records or percentage
        4. **View and export** your sample results in various formats
        5. **Analyze** the sample with built-in statistics and visualizations
        
        **File format requirements:**
        - CSV files should use semicolon (;) as delimiter
        - Values should use comma (,) as decimal separator
        - Date format should be DD-MM-YYYY
        - Required columns: id, key figure, value, date
        """)

    # Sample data preview
    st.subheader("📝 Expected Data Format")
    sample_data = pd.DataFrame({
        'id': ['436436', '436437', '436438'],
        'key figure': ['100.515.100.00.DT', '100.515.100.00.CT', '200.120.050.00.DT'],
        'value': ['600000,00', '-450000,00', '125750,50'],
        'date': ['31-12-2024', '31-12-2024', '31-12-2024']
    })
    st.dataframe(sample_data, use_container_width=True)
