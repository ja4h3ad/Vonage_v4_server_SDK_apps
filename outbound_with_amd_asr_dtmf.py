from vonage import Vonage, Auth
from vonage_voice import CreateCallRequest, Talk
from vonage_http_client import AuthenticationError
from dotenv import load_dotenv
import random
import json
from pprint import pprint
from os.path import join, dirname, abspath
import queue
import os
import time
import threading
from urllib.parse import urlparse, urljoin

# queue the file for audio downloads
download_queue = queue.Queue()
# retry any failed downloads
failed_downloads = queue.Queue()
# load environment variables
dotenv_path = join(dirname(__file__), '.env')
load_dotenv(dotenv_path)

VONAGE_APPLICATION_ID = os.getenv('VONAGE_APPLICATION_ID')
VONAGE_PRIVATE_KEY = os.getenv('VONAGE_APPLICATION_PRIVATE_KEY_PATH')
VONAGE_NUMBER = os.getenv('VONAGE_NUMBER')
WEBHOOK_BASE_URL = os.getenv('WEBHOOK_BASE_URL')
TEST_LOOP = eval(os.getenv('TEST_LOOP', "['1', '2', '3']")) # convert the strings to a list

# Initialize the Vonage server object
auth = Auth(VONAGE_APPLICATION_ID, VONAGE_PRIVATE_KEY)
vonage = Vonage(auth)


# helper function to get webhook URLs
def get_webhook_url(endpoint):
    '''

    :param endpoint:
    :return: full webhook UR from base URL and endpoint
    '''
    return urljoin(WEBHOOK_BASE_URL, endpoint)

def download_recording(recording_url, conversation_uuid, max_retries=5, initial_delay=1):
    for attempt in range(max_retries):
        try:
            response=vonage.voice.download_recording(recording_url)
            recordings_dir = 'recordings'
            os.makedirs(recordings_dir, exist_ok=True) # create the directory if not already created
            parsed_url = urlparse(recording_url)
            file_extension = os.path.splitext(parsed_url.path)[1]
            if not file_extension:
                file_extension = 'wav' # automatically creates a .wav file if the file does not come with extension

            filename = f"recording_{conversation_uuid}.{file_extension}" # name file according to Conversation ID
            file_path = os.path.join(recordings_dir, filename)
            with open(file_path, 'wb') as f:
                f.write(response)

            file_size = os.path.getsize(file_path)
            print(f"Recording saved as {file_path} (Size: {file_size} bytes)")
            if file_size > 1024:
                return True
            else:
                print(f"Recording file seems to small.  Retrying...")

        except AuthenticationError as e:
            print(f"Authentication error for conversation {conversation_uuid} {str(e)} ...")
            return False
        except Exception as e:
            print(f"Failed to download recoring.  Error:  {str(e)}")
            if attempt < max_retries - 1:
                delay = initial_delay * (2 ** attempt)
                print (f"Retrying in {delay} seconds...")
                time.sleep(delay)
            else:
                print(f"Failed to download recording for conversation {conversation_uuid} after {max_retries} attempts.")
                return False

    return False

failed_downloads = queue.Queue()

def download_worker:


