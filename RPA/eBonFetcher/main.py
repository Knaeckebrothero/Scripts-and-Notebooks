"""
This is the main file for the eBonFetcher project.

"""
import os
import dotenv

from mailhandler.mailbox import Mailbox

# Main function
if __name__ == '__main__':
    print('Starting eBonFetcher')

    # Load environment variables
    dotenv.load_dotenv(dotenv.find_dotenv())

    # Create a mailbox object
    box = Mailbox(
        imap_server=os.getenv('PRO_IMAP_HOST'),
        imap_port=os.getenv('PRO_IMAP_PORT'),
        username=os.getenv('PRO_IMAP_USER'),
        password=os.getenv('PRO_IMAP_PASS'),
        mailbox='INBOX')
