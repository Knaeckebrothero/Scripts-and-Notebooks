from dotenv import load_dotenv
import imaplib
import email
import re
import os
import base64
import json
from email.header import decode_header
from http.client import HTTPSConnection
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


# Load environment variables
load_dotenv()

# Email account credentials
EMAIL = os.getenv("EMAIL_ADDRESS")
PASSWORD = os.getenv("EMAIL_PASSWORD")
IMAP_SERVER = os.getenv("IMAP_SERVER_ADDRESS")  # + ":" + os.getenv("IMAP_SERVER_PORT")
EWS_SERVER = os.getenv("IMAP_SERVER_ADDRESS")
# Directory to save attachments
ATTACHMENT_DIR = "attachments"

# Connect to the inbox
def connect_inbox():
    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(EMAIL, PASSWORD)
    mail.select("inbox")
    return mail

# Decode email subject
def decode_email_subject(subject):
    decoded_subject, encoding = decode_header(subject)[0]
    if isinstance(decoded_subject, bytes):
        decoded_subject = decoded_subject.decode(encoding or 'utf-8')
    return decoded_subject

# Fetch emails with specific subject pattern
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

# Parse email and download attachments
def parse_email_and_download_attachments(msg, decoded_subject):
    from_email = msg['From']
    six_digit_number = decoded_subject[:6]

    attachments = []

    # Walk through email parts
    for part in msg.walk():
        if part.get_content_maintype() == 'multipart':
            continue
        if part.get('Content-Disposition') is None:
            continue

        filename = part.get_filename()
        if filename:
            # Decode filename if it's encoded
            filename, encoding = decode_header(filename)[0]
            if isinstance(filename, bytes):
                filename = filename.decode(encoding or 'utf-8')

            # Create attachment directory if it doesn't exist
            if not os.path.exists(ATTACHMENT_DIR):
                os.makedirs(ATTACHMENT_DIR)

            filepath = os.path.join(ATTACHMENT_DIR, f"{six_digit_number}_{filename}")
            with open(filepath, 'wb') as f:
                f.write(part.get_payload(decode=True))
            attachments.append(filepath)

    return from_email, decoded_subject, six_digit_number, attachments

# Send response using EWS
def send_response_ews(to_email, subject, six_digit_number, attachments):
    body = f"Thank you for your email regarding case number {six_digit_number}. We have received it and will respond as soon as possible."
    if attachments:
        body += f"\n\nWe have successfully downloaded {len(attachments)} attachment(s)."

    # Prepare the SOAP envelope
    soap_body = f"""
    <?xml version="1.0" encoding="utf-8"?>
    <soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" 
                   xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages" 
                   xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types" 
                   xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
      <soap:Header>
        <t:RequestServerVersion Version="Exchange2013" />
      </soap:Header>
      <soap:Body>
        <m:CreateItem MessageDisposition="SendAndSaveCopy">
          <m:SavedItemFolderId>
            <t:DistinguishedFolderId Id="sentitems" />
          </m:SavedItemFolderId>
          <m:Items>
            <t:Message>
              <t:Subject>Re: {subject}</t:Subject>
              <t:Body BodyType="Text">{body}</t:Body>
              <t:ToRecipients>
                <t:Mailbox>
                  <t:EmailAddress>{to_email}</t:EmailAddress>
                </t:Mailbox>
              </t:ToRecipients>
            </t:Message>
          </m:Items>
        </m:CreateItem>
      </soap:Body>
    </soap:Envelope>
    """

    # Set up the connection
    conn = HTTPSConnection(EWS_SERVER)

    # Encode credentials
    auth = base64.b64encode(f"{EMAIL}:{PASSWORD}".encode()).decode()

    # Set headers
    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "Authorization": f"Basic {auth}",
    }

    # Send the request
    conn.request("POST", "/EWS/Exchange.asmx", soap_body, headers)

    # Get the response
    response = conn.getresponse()

    # Check if the email was sent successfully
    if response.status == 200:
        print(f"Response sent to {to_email}")
    else:
        print(f"Failed to send response. Status: {response.status}, Reason: {response.reason}")

    conn.close()

# Main function
def main():
    mail = connect_inbox()
    matching_emails = fetch_specific_emails(mail)

    for email_id, msg, decoded_subject in matching_emails:
        from_email, subject, six_digit_number, attachments = parse_email_and_download_attachments(msg, decoded_subject)
        send_response_ews(from_email, subject, six_digit_number, attachments)
        print(f"Processed email for case number {six_digit_number}")
        if attachments:
            print(f"Downloaded {len(attachments)} attachment(s) for case {six_digit_number}")

        # Mark the email as read
        mail.store(email_id, '+FLAGS', '\\Seen')

    mail.close()
    mail.logout()

if __name__ == "__main__":
    main()
