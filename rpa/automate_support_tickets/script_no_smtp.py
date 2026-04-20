import imaplib
import email
import re
import os
import json
import requests
from dotenv import load_dotenv, find_dotenv
from email.header import decode_header
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def get_access_token():
    token_url = AUTH_ENDPOINT.format(tenant_id=TENANT_ID)
    token_data = {
        'grant_type': 'client_credentials',
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'scope': ' '.join(SCOPES)
    }
    token_r = requests.post(token_url, data=token_data)
    token_r.raise_for_status()
    return token_r.json()['access_token']

def connect_inbox():
    # For IMAP, we'll still use imaplib as before
    mail = imaplib.IMAP4_SSL("outlook.office365.com")
    mail.login(EMAIL, "your_password")  # Consider using OAuth for IMAP as well in the future
    mail.select("inbox")
    return mail

def decode_email_subject(subject):
    decoded_subject, encoding = decode_header(subject)[0]
    if isinstance(decoded_subject, bytes):
        decoded_subject = decoded_subject.decode(encoding or 'utf-8')
    return decoded_subject

def fetch_specific_emails(mail):
    _, search_data = mail.search(None, "ALL")
    email_ids = search_data[0].split()
    matching_emails = []

    for email_id in email_ids:
        _, msg_data = mail.fetch(email_id, "(RFC822)")
        email_body = msg_data[0][1]
        msg = email.message_from_bytes(email_body)
        subject = decode_email_subject(msg['Subject'])

        if subject and re.match(r'^\d{6}_', subject):
            matching_emails.append((email_id, msg, subject))

    return matching_emails

def parse_email_and_download_attachments(msg, decoded_subject):
    from_email = msg['From']
    six_digit_number = decoded_subject[:6]

    attachments = []

    for part in msg.walk():
        if part.get_content_maintype() == 'multipart':
            continue
        if part.get('Content-Disposition') is None:
            continue

        filename = part.get_filename()
        if filename:
            filename, encoding = decode_header(filename)[0]
            if isinstance(filename, bytes):
                filename = filename.decode(encoding or 'utf-8')

            if not os.path.exists(ATTACHMENT_DIR):
                os.makedirs(ATTACHMENT_DIR)

            filepath = os.path.join(ATTACHMENT_DIR, f"{six_digit_number}_{filename}")
            with open(filepath, 'wb') as f:
                f.write(part.get_payload(decode=True))
            attachments.append(filepath)

    return from_email, decoded_subject, six_digit_number, attachments

def send_response_graph(access_token, to_email, subject, six_digit_number, attachments):
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }

    body = f"Thank you for your email regarding case number {six_digit_number}. We have received it and will respond as soon as possible."
    if attachments:
        body += f"\n\nWe have successfully downloaded {len(attachments)} attachment(s)."

    email_data = {
        'message': {
            'subject': f'Re: {subject}',
            'body': {
                'contentType': 'Text',
                'content': body
            },
            'toRecipients': [
                {
                    'emailAddress': {
                        'address': to_email
                    }
                }
            ]
        },
        'saveToSentItems': 'true'
    }

    response = requests.post(
        f'{GRAPH_ENDPOINT}/users/{EMAIL}/sendMail',
        headers=headers,
        json=email_data
    )

    if response.status_code == 202:
        print(f"Response sent to {to_email}")
    else:
        print(f"Failed to send response. Status: {response.status_code}, Response: {response.text}")


# Load environment variables
load_dotenv(find_dotenv())

# Microsoft Graph API endpoints
AUTH_ENDPOINT = 'https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token'
GRAPH_ENDPOINT = 'https://graph.microsoft.com/v1.0'

# Your Azure AD app registration details
CLIENT_ID = 'your_client_id'
CLIENT_SECRET = 'your_client_secret'
TENANT_ID = 'your_tenant_id'
SCOPES = ['https://graph.microsoft.com/.default']

# Email account credentials
EMAIL = ""
PASSWORD = ""
IMAP_SERVER = ""
EWS_SERVER = ""
SMTP_SERVER = ""
SMTP_PORT = ""
ATTACHMENT_DIR = "RPA/automate_support_tickets/attachments"

access_token = get_access_token()
mail = connect_inbox()
matching_emails = fetch_specific_emails(mail)

for email_id, msg, decoded_subject in matching_emails:
    from_email, subject, six_digit_number, attachments = parse_email_and_download_attachments(msg, decoded_subject)
    send_response_graph(access_token, from_email, subject, six_digit_number, attachments)
    print(f"Processed email for case number {six_digit_number}")
    if attachments:
        print(f"Downloaded {len(attachments)} attachment(s) for case {six_digit_number}")

    # Mark the email as read
    mail.store(email_id, '+FLAGS', '\\Seen')

mail.close()
mail.logout()
