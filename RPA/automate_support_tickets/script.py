import imaplib
import email
import smtplib
import re
import os
import logging
from dotenv import load_dotenv, find_dotenv
from email.header import decode_header
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart

# Connect to the inbox
def connect_inbox(inbox:str = "inbox"):
    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(EMAIL, PASSWORD)
    mail.select(inbox)
    return mail

# Decode email subject
def decode_email_subject(subject):
    decoded_subject, encoding = decode_header(subject)[0]
    if isinstance(decoded_subject, bytes):
        decoded_subject = decoded_subject.decode(encoding or 'utf-8')
    return decoded_subject

# Fetch emails with specific subject pattern
def fetch_specific_emails(mail, pattern=r'^\d{6}_'):
    _, search_data = mail.search(None, "ALL")
    email_ids = search_data[0].split()
    matching_emails = []

    for email_id in email_ids:
        _, msg_data = mail.fetch(email_id, "(RFC822)")
        email_body = msg_data[0][1]
        msg = email.message_from_bytes(email_body)
        subject = decode_email_subject(msg['Subject'])

        if subject and re.match(pattern, subject):
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
        if filename.split('.')[-1] == 'pdf':
            # Decode filename if it's encoded
            filename, encoding = decode_header(filename)[0]
            if isinstance(filename, bytes):
                filename = filename.decode(encoding or 'utf-8')

            # Create attachment directory if it doesn't exist
            if not os.path.exists(ATTACHMENT_DIR + f"/{six_digit_number}/"):
                os.makedirs(ATTACHMENT_DIR + f"/{six_digit_number}/")

            filepath = os.path.join(ATTACHMENT_DIR + f"/{six_digit_number}/{filename}")
            with open(filepath, 'wb') as f:
                f.write(part.get_payload(decode=True))
            attachments.append(filepath)

    return from_email, decoded_subject, six_digit_number, attachments

# Read HTML template
def read_html_template(file_path):
    with open(file_path, 'r', encoding='utf-8') as file:
        return file.read()

# Send response
def send_response(to_email, subject):
    msg = MIMEMultipart('related')
    msg['From'] = EMAIL
    msg['To'] = to_email
    msg['Subject'] = f"Re: {subject}"

    # Read the HTML template
    html_template = read_html_template('response_template.html')

    """
    # Replace placeholders in the template
    attachment_count_message = f"<p>We have successfully downloaded {len(attachments)} attachment(s).</p>" if attachments else ""
    html_content = html_template.format(
        case_number=six_digit_number,
        attachment_count_message=attachment_count_message
    )
    """

    # Attach HTML content
    msg.attach(MIMEText(html_template, 'html'))

    # Attach the logo image
    with open('logo.png', 'rb') as logo_file:
        logo_image = MIMEImage(logo_file.read())
        logo_image.add_header('Content-ID', '<company_logo>')
        msg.attach(logo_image)

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(EMAIL, PASSWORD)
        server.send_message(msg)


# Set up logging
logging.basicConfig(level=logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler = logging.FileHandler("log.txt")
handler.setFormatter(formatter)
logging.getLogger().addHandler(handler)
logging.debug("Script started")

# Load environment variables
load_dotenv(find_dotenv())
EMAIL = os.getenv("EMAIL_ADDRESS")
PASSWORD = os.getenv("EMAIL_PASSWORD")
INBOX = os.getenv("INBOX")
IMAP_SERVER = os.getenv("IMAP_SERVER_ADDRESS")
SMTP_SERVER = os.getenv("SMTP_SERVER_ADDRESS")
SMTP_PORT = os.getenv("SMTP_SERVER_PORT")
ATTACHMENT_DIR = "attachments"
logging.debug("Environment variables loaded")

# Connect to the inbox
mail = connect_inbox(INBOX)

# Fetch emails with specific subject pattern
matching_emails = fetch_specific_emails(mail, r'^\d{8}_')

for email_id, msg, decoded_subject in matching_emails:
    from_email, subject, six_digit_number, attachments = parse_email_and_download_attachments(msg, decoded_subject)
    if attachments:
        send_response(from_email, subject)
        logging.info(f"Sent response to {from_email} for case number {six_digit_number} and downloaded {len(attachments)} attachment(s)")

    # Mark the email as read
    mail.store(email_id, '+FLAGS', '\\Seen')
    logging.debug(f"Marked email from {from_email} as read")

# Close the connection
logging.debug("Script finished")
mail.close()
mail.logout()
