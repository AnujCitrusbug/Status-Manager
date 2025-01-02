import io
import streamlit as st
from datetime import datetime
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload
from google.oauth2.service_account import Credentials
import os
from dotenv import load_dotenv
from typing import List, Optional

load_dotenv()

# Google Drive configuration
SERVICE_ACCOUNT_FILE = "status-cred.json"
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
]


# Authenticate and create Drive API client
def authenticate_drive() -> "build":
    """
    Authenticates and creates a Google Drive API client using service account credentials.

    Returns:
        build: The authenticated Google Drive API client.
    """
    credentials = Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES
    )
    service = build("drive", "v3", credentials=credentials)
    return service


# Create a folder in Google Drive
def create_folder(
    service: "build",
    folder_name: str,
    parent_id: Optional[str] = None,
    email_addresses: Optional[List[str]] = None,
) -> str:
    """
    Creates a folder in Google Drive and optionally sets permissions for specified email addresses.

    Args:
        service (build): The Google Drive API client.
        folder_name (str): The name of the folder to create.
        parent_id (Optional[str], optional): The parent folder ID. Defaults to None.
        email_addresses (Optional[List[str]], optional): A list of email addresses to grant editor permissions. Defaults to None.

    Returns:
        str: The ID of the created folder.
    """
    folder_metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id] if parent_id else None,
    }

    folder = service.files().create(body=folder_metadata, fields="id").execute()
    folder_id = folder.get("id")

    if email_addresses:
        for email in email_addresses:
            permission = {"type": "user", "role": "writer", "emailAddress": email}
            service.permissions().create(
                fileId=folder_id, body=permission, fields="id"
            ).execute()

    return folder_id


# Check if a folder exists in Google Drive
def get_folder_id(
    service: "build", folder_name: str, parent_id: Optional[str] = None
) -> Optional[str]:
    """
    Checks if a folder exists in Google Drive and returns its ID if found.

    Args:
        service (build): The Google Drive API client.
        folder_name (str): The name of the folder to search for.
        parent_id (Optional[str], optional): The parent folder ID to narrow the search. Defaults to None.

    Returns:
        Optional[str]: The folder ID if the folder exists, otherwise None.
    """
    query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder'"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    response = (
        service.files()
        .list(q=query, spaces="drive", fields="files(id, name)")
        .execute()
    )
    folders = response.get("files", [])
    return folders[0]["id"] if folders else None


def get_file_id(service: "build", file_name: str, folder_id: str) -> Optional[str]:
    """
    Checks if a file exists in a specified folder and returns its ID if found.

    Args:
        service (build): The Google Drive API client.
        file_name (str): The name of the file to search for.
        folder_id (str): The folder ID where the file should be located.

    Returns:
        Optional[str]: The file ID if the file exists, otherwise None.
    """
    query = f"name='{file_name}' and '{folder_id}' in parents and mimeType='text/plain'"
    response = (
        service.files()
        .list(q=query, spaces="drive", fields="files(id, name)")
        .execute()
    )
    files = response.get("files", [])
    return files[0]["id"] if files else None


def save_status(service: "build", folder_id: str, file_name: str, content: str) -> None:
    """
    Saves or appends a status in a Google Docs file on Google Drive.
    If the file exists, appends the content with proper formatting. Otherwise, creates a new Google Docs file.

    Args:
        service (build): The Google Drive API client.
        folder_id (str): The folder ID where the file should be saved.
        file_name (str): The name of the file to create or update.
        content (str): The content of the status to save or append.
    """
    # Check if the file already exists
    query = f"name='{file_name}' and '{folder_id}' in parents and mimeType='application/vnd.google-apps.document'"
    response = (
        service.files().list(q=query, spaces="drive", fields="files(id)").execute()
    )
    files = response.get("files", [])

    if files:
        # Use the first matched file ID (assumes no duplicates)
        existing_file_id = files[0]["id"]

        try:
            # Use the Google Docs API to fetch the existing content
            docs_service = build("docs", "v1", credentials=service._http.credentials)

            # Get the document's current length
            doc = docs_service.documents().get(documentId=existing_file_id).execute()
            doc_length = doc["body"]["content"][-1][
                "endIndex"
            ]  # This gives us the end index

            # Prepare requests for appending content
            requests = [
                # Add a separator line
                {
                    "insertText": {
                        "location": {
                            "index": (doc_length - 1)
                        },  # Use the document's end index
                        "text": "\n",
                    }
                },
                # Add the new content
                {
                    "insertText": {
                        "location": {
                            "index": (doc_length)
                        },  # Use the document's end index
                        "text": "\n---------------------------------------\n\n"
                        + content
                        + "\n",
                    }
                },
            ]

            # Execute the batch update
            docs_service.documents().batchUpdate(
                documentId=existing_file_id,
                body={"requests": requests},
            ).execute()

        except Exception as e:
            raise Exception(f"Failed to update the document: {e}")

    else:
        # If no file exists, create a new Google Docs file
        file_metadata = {
            "name": file_name,
            "parents": [folder_id],
            "mimeType": "application/vnd.google-apps.document",
        }

        # Create a new Google Docs file
        created_file = service.files().create(body=file_metadata, fields="id").execute()
        new_file_id = created_file.get("id")

        # Use the Google Docs API to add content to the new document
        docs_service = build("docs", "v1", credentials=service._http.credentials)
        docs_service.documents().batchUpdate(
            documentId=new_file_id,
            body={
                "requests": [
                    {
                        "insertText": {
                            "location": {"index": 1},  # Start from the beginning
                            "text": content + "\n",
                        }
                    }
                ]
            },
        ).execute()


def main() -> None:
    """
    The main function for the Streamlit UI. It allows the user to select a profile,
    enter a status, and save it to Google Drive.
    """
    st.title("Upwork Status Manager")

    # Dropdown to select profile from predefined options
    profiles = os.getenv("UPWORK_PROFILES").split(",")
    selected_profile = st.selectbox("Select Profile", profiles)

    # Date picker
    today = datetime.today().date()
    selected_date = st.date_input("Select Date", today)

    # Text area for status
    status = st.text_area("Write Status")

    # Submit button
    if st.button("Submit"):
        if not status:
            st.error("Status cannot be empty!")
            return

        try:
            service = authenticate_drive()

            # Get or create main folder
            main_folder_id = get_folder_id(service, "status") or create_folder(
                service, "status", email_addresses=os.getenv("EMAIL_ADDRESS").split(",")
            )

            # Get or create subfolder for the profile
            profile_folder_id = get_folder_id(
                service, selected_profile, main_folder_id
            ) or create_folder(service, selected_profile, main_folder_id)

            # File name based on today's date
            file_name = f"{selected_date}"

            # Save status in a file (create or append)
            save_status(service, profile_folder_id, file_name, status)

            st.success("Status saved successfully!")
        except Exception as e:
            st.error(f"An error occurred: {e}")


if __name__ == "__main__":
    main()
