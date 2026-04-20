"""
This module holds the mailbox class which is responsible for managing a mailbox.

"""
import os
import imaplib
import email


class Mailbox:
    """
    This class is responsible for managing a mailbox.

    """

    def __init__(self, imap_server: str, imap_port: str, username: str,
                 password: str, mailbox: str = None):
        """
        Constructor for the mailbox class.
        Automatically connects to the mailbox, using the provided credentials,
        once the class is instantiated. The method also checks the environment variables
        and uses them as default values for the constructor.

        Args:
            imap_server (): The imap server to connect to.
            imap_port (): The port of the imap server.
            username (): The username/mail to connect to.
            password (): The user's password.
            mailbox (): Mailbox to connect to. Defaults to None.
        """
        self._imap_server = imap_server
        self._imap_port = imap_port
        self._username = username
        self._password = password
        self.mail = None
        self.mailbox = None

        # Connect to the mailbox
        self.connect(mailbox)

    def __del__(self):
        """
        Destructor for the mailbox class.
        Automatically closes the connection to the server when the class is destroyed.
        """
        self.close()

    def connect(self, mailbox=None):
        """
        Method to connect to the mailbox using the classes credentials.

        Args:
            mailbox (str): The mailbox to connect to. Defaults to None,
            which will connect to the default mailbox.
        """
        self.mail = imaplib.IMAP4_SSL(host=self._imap_server, port=self._imap_port)
        self.mail.login(user=self._username, password=self._password)

        if mailbox:
            self.select_mailbox(mailbox)

    def select_mailbox(self, mailbox):
        """
        Method to select a mailbox.
        """
        self.mail.select(mailbox)
        self.mailbox = mailbox

    def close(self):
        """
        Closes the mailbox.
        """
        self.mail.close()
        self.mail.logout()

    def get_mailboxes(self):
        """
        Method to get the mailboxes.
        """
        result, mailboxes = self.mail.list()
        return mailboxes

    def get_mail_ids(self):
        """
        Method to get the mail ids.
        """
        result, data = self.mail.search(None, "ALL")
        return data[0].split()

    def get_mail(self, email_id):
        """
        Method to get the email.
        """
        result, data = self.mail.fetch(email_id, "(RFC822)")
        raw_email = data[0][1]
        email_message = email.message_from_bytes(raw_email)
        return email_message

    def get_list_of_mails(self) -> list[dict]:
        """
        Method to get a list of all the emails, with their id, subject and date.

        Returns:
            list[{"mail_id": str, "subject": str, "date": str}]: A list of all the emails,
            each as a dictionary with their corresponding id, subject and date.
        """
        mail_ids = self.get_mail_ids()
        mail_list = []
        for mail_id in mail_ids:
            mail = self.get_mail(mail_id)
            mail_list.append({"mail_id": mail_id, "subject": mail["subject"], "date": mail["date"]})
        return mail_list

    def fetch_emails(self, search_criteria):
        """
        Fetches emails from the mailbox based on the search criteria.

        Args:
            search_criteria (str): The search criteria to use for fetching the emails.

        Returns:
            list[email.message.EmailMessage]: A list of all the emails fetched.
        """
        result, data = self.mail.search(None, search_criteria)
        email_ids = data[0].split()
        emails = []
        for email_id in email_ids:
            result, data = self.mail.fetch(email_id, "(RFC822)")
            raw_email = data[0][1]
            email_message = email.message_from_bytes(raw_email)
            emails.append(email_message)
        return emails
