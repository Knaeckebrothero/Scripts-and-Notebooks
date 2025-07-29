import streamlit as st
import pandas as pd
from datetime import datetime

# Page configuration
st.set_page_config(
    page_title="Auditor Analytics Cockpit",
    page_icon="📊",
    layout="wide"
)

# Custom CSS for better styling
st.markdown("""
    <style>
    .main-header {
        font-size: 3rem;
        color: #1e3a8a;
        text-align: center;
        margin-bottom: 1rem;
    }
    .sub-header {
        font-size: 1.2rem;
        color: #64748b;
        text-align: center;
        margin-bottom: 2rem;
    }
    .report-category {
        background-color: #f8fafc;
        padding: 0.5rem;
        border-radius: 0.5rem;
        margin-bottom: 0.5rem;
    }
    </style>
""", unsafe_allow_html=True)

# Header
st.markdown('<h1 class="main-header">🏛️ Auditor Analytics Cockpit</h1>', unsafe_allow_html=True)
st.markdown('<p class="sub-header">Your centralized hub for accessing all audit reports, dashboards, and analytics tools</p>', unsafe_allow_html=True)

# Introduction
with st.container():
    st.info("""
    **Welcome to the Auditor Analytics Cockpit!** This application serves as your one-stop portal for accessing all reports and dashboards
    created by the Data Management & Science Department. Simply browse through the available tools below or use the search function to quickly
    find what you need. Click on any report to expand its details and access the link. This platform demonstrates our transition from
    traditional VBA solutions to modern Python-based analytics.
    """)

# Sample data for reports/dashboards
reports_data = {
    "Financial Reports": [
        {
            "name": "Quarterly Financial Analysis Dashboard",
            "description": "Interactive Power BI dashboard showing quarterly financial trends, variance analysis, and key performance indicators.",
            "link": "https://app.powerbi.com/view?r=example_quarterly_financial",
            "type": "Power BI",
            "last_updated": "2024-12-15",
            "owner": "Finance Analytics Team"
        },
        {
            "name": "Revenue Recognition Report",
            "description": "Automated report for revenue recognition compliance checking and anomaly detection.",
            "link": "https://app.powerbi.com/view?r=example_revenue_recognition",
            "type": "Power BI",
            "last_updated": "2024-12-20",
            "owner": "Compliance Team"
        }
    ],
    "Risk Analytics": [
        {
            "name": "Risk Assessment Matrix",
            "description": "Comprehensive risk scoring and assessment tool with heat maps and trend analysis.",
            "link": "https://app.powerbi.com/view?r=example_risk_matrix",
            "type": "Power BI",
            "last_updated": "2024-12-18",
            "owner": "Risk Management"
        },
        {
            "name": "Fraud Detection Analytics",
            "description": "ML-powered fraud detection dashboard with real-time alerts and pattern recognition.",
            "link": "https://app.powerbi.com/view?r=example_fraud_detection",
            "type": "Python Dashboard",
            "last_updated": "2024-12-22",
            "owner": "Data Science Team"
        }
    ],
    "Operational Analytics": [
        {
            "name": "Audit Progress Tracker",
            "description": "Real-time tracking of ongoing audits, resource allocation, and timeline management.",
            "link": "https://app.powerbi.com/view?r=example_audit_tracker",
            "type": "Power BI",
            "last_updated": "2024-12-19",
            "owner": "Operations Team"
        },
        {
            "name": "Client Portfolio Analysis",
            "description": "Detailed analysis of client portfolios, engagement history, and profitability metrics.",
            "link": "https://app.powerbi.com/view?r=example_client_portfolio",
            "type": "Tableau",
            "last_updated": "2024-12-17",
            "owner": "Client Services"
        }
    ],
    "Compliance & Regulatory": [
        {
            "name": "SOX Compliance Dashboard",
            "description": "Sarbanes-Oxley compliance tracking with control testing results and remediation status.",
            "link": "https://app.powerbi.com/view?r=example_sox_compliance",
            "type": "Power BI",
            "last_updated": "2024-12-21",
            "owner": "Compliance Team"
        },
        {
            "name": "Regulatory Change Tracker",
            "description": "Monitor and analyze regulatory changes affecting audit procedures and requirements.",
            "link": "https://app.powerbi.com/view?r=example_regulatory_tracker",
            "type": "Python App",
            "last_updated": "2024-12-16",
            "owner": "Legal & Compliance"
        }
    ]
}

# Search functionality
col1, col2, col3 = st.columns([2, 1, 1])
with col1:
    search_term = st.text_input("🔍 Search for reports, dashboards, or tools:", placeholder="e.g., financial, risk, compliance...")
with col2:
    filter_type = st.selectbox("Filter by type:", ["All", "Power BI", "Python Dashboard", "Python App", "Tableau"])
with col3:
    sort_by = st.selectbox("Sort by:", ["Name", "Last Updated", "Category"])

# Filter function
def filter_reports(reports_dict, search_term, filter_type):
    filtered_dict = {}
    for category, reports in reports_dict.items():
        filtered_reports = []
        for report in reports:
            # Check if search term matches
            if search_term.lower() in report["name"].lower() or search_term.lower() in report["description"].lower():
                # Check if type filter matches
                if filter_type == "All" or report["type"] == filter_type:
                    filtered_reports.append(report)
        if filtered_reports:
            filtered_dict[category] = filtered_reports
    return filtered_dict

# Apply filters
filtered_reports = filter_reports(reports_data, search_term, filter_type)

# Sort reports if needed
if sort_by == "Last Updated":
    for category in filtered_reports:
        filtered_reports[category].sort(key=lambda x: x["last_updated"], reverse=True)
elif sort_by == "Name":
    for category in filtered_reports:
        filtered_reports[category].sort(key=lambda x: x["name"])

# Display reports
if filtered_reports:
    for category, reports in filtered_reports.items():
        st.markdown(f"### 📁 {category}")

        for report in reports:
            with st.expander(f"**{report['name']}** - {report['type']}"):
                col1, col2 = st.columns([3, 1])

                with col1:
                    st.markdown(f"**Description:** {report['description']}")
                    st.markdown(f"**Owner:** {report['owner']}")
                    st.markdown(f"**Last Updated:** {report['last_updated']}")

                with col2:
                    st.markdown("**Access Report:**")
                    st.markdown(f"[🔗 Open in {report['type']}]({report['link']})")

                # Additional actions
                st.markdown("---")
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.button("📊 View Metadata", key=f"meta_{report['name']}")
                with col2:
                    st.button("📧 Subscribe to Updates", key=f"sub_{report['name']}")
                with col3:
                    st.button("❓ Get Help", key=f"help_{report['name']}")
else:
    st.warning("No reports found matching your search criteria. Try adjusting your filters.")

# Footer with additional information
st.markdown("---")
with st.container():
    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown("### 📚 Quick Links")
        st.markdown("- [Data Governance Policies](#)")
        st.markdown("- [Python Migration Guide](#)")
        st.markdown("- [Report Request Form](#)")

    with col2:
        st.markdown("### 🛠️ Need Help?")
        st.markdown("- Email: data.team@company.com")
        st.markdown("- Teams: Data Analytics Channel")
        st.markdown("- Extension: 1234")

    with col3:
        st.markdown("### 📈 Stats")
        st.metric("Total Reports Available", sum(len(reports) for reports in reports_data.values()))
        st.metric("Active Users This Month", "147")

# Sidebar for additional features
with st.sidebar:
    st.markdown("### 🎯 Quick Actions")
    st.button("➕ Request New Report")
    st.button("🐛 Report an Issue")
    st.button("💡 Suggest Improvement")

    st.markdown("---")
    st.markdown("### 📊 Your Recent Reports")
    st.markdown("- Quarterly Financial Analysis")
    st.markdown("- Risk Assessment Matrix")
    st.markdown("- Client Portfolio Analysis")

    st.markdown("---")
    st.markdown("### 🔔 Notifications")
    st.info("New fraud detection model deployed - Check out the updated dashboard!")
