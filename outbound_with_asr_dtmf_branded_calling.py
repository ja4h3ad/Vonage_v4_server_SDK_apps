"""
Vonage Voice API Outbound Calling Application

This application demonstrates advanced voice features including:
- Outbound call automation with retry logic
- Advanced Machine Detection (AMD)
- Automatic Speech Recognition (ASR)
- DTMF input handling
- Call recording with automatic download
- Interactive Voice Response (IVR) system
- Branded Calling (BRC) with call authentication and log event correlation

Built with Vonage SDK v4, FastAPI, and Python 3.12+
"""

from vonage import Vonage, Auth
from vonage_voice import CreateCallRequest
from vonage_http_client import AuthenticationError, HttpRequestError
from dotenv import load_dotenv

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

import random
import json
from pprint import pprint
from os.path import join, dirname, abspath
import queue
import os
import time
import threading
from urllib.parse import urlparse, urljoin

from vonage_voice.models import ncco

# Import our custom modules
from first_orion import get_auth_token, send_push_notification
from call_tracker import call_tracker

# queue the audio file downloads
download_queue = queue.Queue()
# for any download failures
failed_downloads = queue.Queue()

# Load environment variables
dotenv_path = join(dirname(__file__), ".env")
load_dotenv(dotenv_path)

VONAGE_APPLICATION_ID = os.environ.get("VONAGE_APPLICATION_ID")
VONAGE_PRIVATE_KEY = os.environ.get("VONAGE_APPLICATION_PRIVATE_KEY_PATH")
VONAGE_NUMBER = os.environ.get("FROM_NUMBER")
WEBHOOK_BASE_URL = os.environ.get("WEBHOOK_BASE_URL")
TEST_LOOP = [str(num) for num in eval(os.getenv('TEST_LOOP', "['1', '2', '3']"))]

# Initialize Vonage client with application-based authentication
auth = Auth(application_id=VONAGE_APPLICATION_ID, private_key=VONAGE_PRIVATE_KEY)
vonage = Vonage(auth)

# Initialize FastAPI application
app = FastAPI(title="Vonage Voice API Demo", version="1.0.0")


def get_webhook_url(endpoint):
    """
    Construct full webhook URL from base URL and endpoint

    Args:
        endpoint (str): The webhook endpoint path

    Returns:
        str: Complete webhook URL
    """
    return urljoin(WEBHOOK_BASE_URL, endpoint)


def download_recording(recording_url, conversation_uuid, max_retries=5, initial_delay=1):
    """
    Download call recording from Vonage with exponential backoff retry logic

    Args:
        recording_url (str): URL of the recording to download
        conversation_uuid (str): Unique conversation identifier
        max_retries (int): Maximum number of retry attempts
        initial_delay (int): Initial delay between retries in seconds

    Returns:
        bool: True if download successful, False otherwise
    """
    for attempt in range(max_retries):
        try:
            # Create recordings directory if it doesn't exist
            recordings_dir = 'recordings'
            os.makedirs(recordings_dir, exist_ok=True)

            # Parse URL to determine file extension
            parsed_url = urlparse(recording_url)
            file_extension = os.path.splitext(parsed_url.path)[1]
            if not file_extension:
                file_extension = '.wav'

            # Generate filename using conversation UUID
            filename = f"recording_{conversation_uuid}{file_extension}"
            file_path = os.path.join(recordings_dir, filename)

            # Download recording using Vonage SDK v4
            vonage.voice.download_recording(recording_url, file_path)

            # Verify download was successful
            if os.path.exists(file_path):
                file_size = os.path.getsize(file_path)
                print(f"Recording saved as {file_path} (Size: {file_size} bytes)")
                if file_size > 1024:  # Minimum file size check
                    return True
                else:
                    print(f"Recording file seems too small. Retrying...")
            else:
                print(f"Recording file was not created. Retrying...")

        except AuthenticationError as e:
            print(f"Authentication error for conversation {conversation_uuid}: {str(e)}")
            return False
        except Exception as e:
            print(f"Failed to download recording. Error: {str(e)}")
            if attempt < max_retries - 1:
                # Exponential backoff delay
                delay = initial_delay * (2 ** attempt)
                print(f"Retrying in {delay} seconds...")
                time.sleep(delay)
            else:
                print(
                    f"Failed to download recording for conversation {conversation_uuid} after {max_retries} attempts.")
                return False

    return False


def download_worker():
    """
    Background worker thread for processing recording download queue
    Continuously processes download tasks until poison pill received
    """
    while True:
        try:
            item = download_queue.get()
            if item is None:  # Poison pill to stop worker
                break

            recording_url, conversation_uuid = item
            success = download_recording(recording_url, conversation_uuid)
            if not success:
                failed_downloads.put((recording_url, conversation_uuid))
        except Exception as e:
            print(f"Worker error: {e}")
            failed_downloads.put((recording_url, conversation_uuid))
        finally:
            download_queue.task_done()


def retry_failed_downloads(max_retries=2):
    """
    Retry all failed download attempts

    Args:
        max_retries (int): Maximum retry attempts for failed downloads
    """
    print("Retrying failed downloads...")
    retry_queue = queue.Queue()

    # Process all failed downloads
    while not failed_downloads.empty():
        recording_url, conversation_uuid = failed_downloads.get()
        success = download_recording(recording_url, conversation_uuid)
        if not success:
            retry_queue.put((recording_url, conversation_uuid))
        failed_downloads.task_done()

    # Report permanently failed downloads
    while not retry_queue.empty():
        recording_url, conversation_uuid = retry_queue.get()
        print(f'Failed to download recording for conversation {conversation_uuid} after retries')



def make_call(to_number, max_retries=5, initial_delay=1):
    # Start a new call tracking flow
    correlation_id = call_tracker.start_auth_flow(to_number)

    # First Orion branded calling step - get auth token and send push notification
    token, auth_data = get_auth_token(correlation_id)
    if token:
        print(f"Successfully obtained First Orion auth token")
        # Send push notification with the token
        success, push_data = send_push_notification(correlation_id, token, VONAGE_NUMBER, to_number)
        if success:
            print(f"Successfully sent First Orion push notification for {to_number}")
        else:
            print(f"Failed to send First Orion push notification for {to_number}. Call will proceed unbranded.")
    else:
        print(f"Failed to get First Orion auth token. Call will proceed unbranded.")

    # Proceed with the call
    for attempt in range(max_retries):
        try:
            # Create call request with comprehensive configuration
            call_request = CreateCallRequest(
                to=[{'type': 'phone', 'number': to_number}],
                from_={'type': 'phone', 'number': VONAGE_NUMBER},
                ringing_timer=60,
                ncco=[
                    {
                        'action': 'record',
                        'eventUrl': [get_webhook_url('recording')],
                        'split': 'conversation',  # Record both channels separately
                        'channels': 2,
                        'public': True,
                        'validity_time': 30,
                        'format': 'wav'
                    }
                ],
                advanced_machine_detection={
                    'behavior': 'continue',  # Continue call flow after detection
                    'mode': 'default',
                    'beep_timeout': 45  # Wait 45 seconds for voicemail beep
                },
                event_url=[get_webhook_url('event')],
                event_method='POST'
            )

            # Execute call
            response = vonage.voice.create_call(call_request)
            pprint(response.model_dump())

            # Record the Vonage call creation in our tracker
            call_tracker.record_vonage_call(correlation_id, response)

            return

        except (AuthenticationError, HttpRequestError) as e:
            print(f'Error when calling {to_number}: {str(e)}')
            if attempt < max_retries - 1:
                delay = initial_delay * (2 ** attempt)
                print(f'Retrying call in {delay} seconds...')
                time.sleep(delay)
            else:
                print(f'Failed to create call for {to_number} after {max_retries} attempts.')


# Start download worker thread
threading.Thread(target=download_worker, daemon=True).start()

@app.post("/dtmf_input")
async def dtmf_input_webhook(request: Request):
    """
    Handle DTMF and speech input from callers during IVR interactions
    Processes both keypad input and voice commands
    """
    data = await request.json
    print("Full input webhook data:", json.dumps(data, indent=2))

    conversation_uuid = data.get('conversation_uuid', 'unknown')

    # Track this event in our call tracker
    if hasattr(call_tracker, 'record_vonage_event'):
        call_tracker.record_vonage_event(conversation_uuid, data)

    # Ensure the 'webhooks' directory exists
    webhooks_dir = 'webhooks'
    os.makedirs(webhooks_dir, exist_ok=True)

    # Write DTMF input data to a file
    file_path = os.path.join(webhooks_dir, f"dtmf_input_{conversation_uuid}.json")
    with open(file_path, 'a', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write('\n')  # Add a newline for readability between events

    # Extract the input from either DTMF or speech
    dtmf_data = data.get('dtmf', {})
    dtmf = dtmf_data.get('digits') if isinstance(dtmf_data, dict) else dtmf_data

    # More robust speech text extraction
    speech_results = data.get('speech', {}).get('results', [])
    speech_text = ''
    if speech_results and isinstance(speech_results, list) and len(speech_results) > 0:
        if isinstance(speech_results[0], dict):
            # Handle case where text might be None
            text_value = speech_results[0].get('text')
            if text_value is not None:
                speech_text = text_value.strip()

    print(f"Processed input for conversation {conversation_uuid}:")
    print(f"DTMF data: {dtmf_data}")
    print(f"DTMF digits: {dtmf}")
    print(f"Speech text: {speech_text}")

    # Load responses file to determine the current step
    responses_dir = 'responses'
    os.makedirs(responses_dir, exist_ok=True)
    response_file = os.path.join(responses_dir, f"survey_{conversation_uuid}.json")

    # Load existing responses if file exists
    responses = {}
    if os.path.exists(response_file):
        try:
            with open(response_file, 'r') as f:
                responses = json.load(f)
        except:
            print(f"Error loading existing responses for {conversation_uuid}")

    # Determine current step based on what questions have been answered
    if 'saw_vonage_caller_id' in responses:
        current_step = 4  # All questions answered
    elif 'saw_vonage_logo' in responses:
        current_step = 3  # Two questions answered
    elif 'device_type' in responses:
        current_step = 2  # One question answered
    else:
        current_step = 1  # No questions answered yet

    print(f"Current step based on responses: {current_step}")

    # Process user input
    user_input = None
    if dtmf and isinstance(dtmf_data, dict) and dtmf_data.get('digits'):
        user_input = dtmf
    elif speech_text:
        # Convert speech like "one" to "1" (normalize to lowercase)
        speech_text_lower = speech_text.lower()
        speech_map = {
            "one": "1", "two": "2",
            "yes": "1", "no": "2",
            "iphone": "1", "android": "2",
            "go": "go"  # Just to handle the initial "go" command
        }
        user_input = speech_map.get(speech_text_lower, speech_text_lower)

    print(f"User input: {user_input}")

    # Determine next step based on current input
    next_step = current_step
    if user_input == "go" and current_step == 1:
        # Special case for "go" command - start first question
        next_step = 1
    elif user_input:
        # For any valid input other than "go", record the response and advance
        if current_step == 1:
            responses['device_type'] = user_input
            next_step = 2
        elif current_step == 2:
            responses['saw_vonage_logo'] = user_input
            next_step = 3
        elif current_step == 3:
            responses['saw_vonage_caller_id'] = user_input
            next_step = 4

        # Save to the responses file
        with open(response_file, 'w') as f:
            json.dump(responses, f, indent=2)

        # Also record in our call tracker if available
        if hasattr(call_tracker, 'record_survey_response'):
            if current_step == 1:
                call_tracker.record_survey_response(conversation_uuid, "device_type", user_input)
            elif current_step == 2:
                call_tracker.record_survey_response(conversation_uuid, "saw_vonage_logo", user_input)
            elif current_step == 3:
                call_tracker.record_survey_response(conversation_uuid, "saw_vonage_caller_id", user_input)

    print(f"Next step: {next_step}")

    # Based on next_step, generate the appropriate NCCO
    if next_step == 1:
        # First question - Device type
        ncco = [
            {
                'action': 'talk',
                'text': '<speak>What type of device do you have? You can either say, "iPhone", or press or say 1; you can say "Android", or press or say 2.</speak>',
                'language': 'en-US',
                'style': 2,
                'premium': True,
                'bargeIn': True
            },
            {
                'action': 'input',
                'dtmf': {
                    'maxDigits': 1,
                    'timeOut': 10
                },
                'speech': {
                    'language': 'en-US',
                    'context': ['1', '2', 'iphone', 'android'],
                    'startTimeout': 10,
                    'maxDuration': 5,
                    'endOnSilence': 1.5
                },
                'type': ['dtmf', 'speech'],
                'eventUrl': [get_webhook_url('dtmf_input')],
                'eventMethod': 'POST'
            }
        ]
    elif next_step == 2:
        # Second question - Vonage logo
        ncco = [
            {
                'action': 'talk',
                'text': '<speak>Did you see the Vonage Logo on your handset when I called you? Press or say 1 for yes, press or say 2 for no.</speak>',
                'language': 'en-US',
                'style': 2,
                'premium': True,
                'bargeIn': True
            },
            {
                'action': 'input',
                'dtmf': {
                    'maxDigits': 1,
                    'timeOut': 10
                },
                'speech': {
                    'language': 'en-US',
                    'context': ['1', '2', 'yes', 'no'],
                    'startTimeout': 10,
                    'maxDuration': 5,
                    'endOnSilence': 1.5
                },
                'type': ['dtmf', 'speech'],
                'eventUrl': [get_webhook_url('dtmf_input')],
                'eventMethod': 'POST'
            }
        ]
    elif next_step == 3:
        # Third question - Caller ID
        ncco = [
            {
                'action': 'talk',
                'text': '<speak>Did you see the Vonage caller name on your handset when I called you? Press or say 1 for yes, press or say 2 for no.</speak>',
                'language': 'en-US',
                'style': 2,
                'premium': True,
                'bargeIn': True
            },
            {
                'action': 'input',
                'dtmf': {
                    'maxDigits': 1,
                    'timeOut': 10
                },
                'speech': {
                    'language': 'en-US',
                    'context': ['1', '2', 'yes', 'no'],
                    'startTimeout': 10,
                    'maxDuration': 5,
                    'endOnSilence': 1.5
                },
                'type': ['dtmf', 'speech'],
                'eventUrl': [get_webhook_url('dtmf_input')],
                'eventMethod': 'POST'
            }
        ]
    else:
        # End of survey - mark the call as completed in the log
        call_data = call_tracker.get_call_by_conversation_uuid(conversation_uuid) if hasattr(call_tracker,
                                                                                             'get_call_by_conversation_uuid') else None
        if call_data:
            correlation_id = call_data.get("correlation_id")
            if correlation_id and hasattr(call_tracker, 'active_calls') and correlation_id in call_tracker.active_calls:
                call_tracker.active_calls[correlation_id]["status"] = "survey_completed"

        # Final thank you message
        ncco = [
            {
                'action': 'talk',
                'text': '<speak>Thank you for your responses. Tim Dentry thanks you for your input. Goodbye!</speak>',
                'language': 'en-US',
                'style': 2,
                'premium': True
            }
        ]

    print(f"Returning NCCO for step {next_step}: {json.dumps(ncco, indent=2)}")
    return JSONResponse(content=ncco, status_code=200)


@app.post("/event")
async def event_webhook(request: Request):
    """
    Handle call events including Advanced Machine Detection results
    Processes human/machine detection and manages call flow accordingly
    """
    data = await request.json()
    status = data.get('status')
    print(f'Event webhook data received with status: {status}')
    print(f'Full data:', json.dumps(data, indent=2))
    conversation_uuid = data.get("conversation_uuid", "unknown")

    # Record this event in our call tracker
    call_tracker.record_vonage_event(conversation_uuid, data)

    # Ensure the 'webhooks' directory exists
    webhooks_dir = 'webhooks'
    os.makedirs(webhooks_dir, exist_ok=True)

    # Write event data to a file in the webhooks directory
    file_path = os.path.join(webhooks_dir, f"event_{conversation_uuid}.json")
    with open(file_path, 'a') as f:
        json.dump(data, f, indent=2)
        f.write('\n')  # Add a newline for readability between events

    # Logging for speech/ASR events
    if 'speech' in data:
        print("Capturing ASR/Speech event")
        # Write ASR/Speech data to a specific file
        speech_file_path = os.path.join(webhooks_dir, f"speech_{conversation_uuid}.json")
        with open(speech_file_path, 'a', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write('\n')

    # Handle status events - properly indented, not inside speech condition
    if status == 'human':
        print("Human detected, starting IVR flow")
        ncco = [
            {
                'action': 'talk',
                'text': '<speak>This is a test of Vonage Branded Calling. I will be asking you three questions about your experience with this call. You can speak to me or use your phone keypad to respond.  Say the word "Go" when you are ready.</speak>',
                'language': 'en-US',
                'style': 2,
                'premium': True
            },
            {
                'action': 'input',
                'dtmf': {
                    'maxDigits': 1,
                    'timeOut': 10
                },
                'speech': {
                    'language': 'en-US',
                    'context': ['go', 'yes'],
                    'startTimeout': 10,
                    'maxDuration': 5,
                    'endOnSilence': 1.5
                },
                'type': ['dtmf', 'speech'],
                'eventUrl': [get_webhook_url('dtmf_input')],
                'eventMethod': 'POST',
                'step': 1  # Set initial step
            }
        ]
        print("Returning human NCCO:", json.dumps(ncco, indent=2))
        return JSONResponse(content=ncco, status_code=200)


    elif status == 'machine':
        sub_state = data.get('sub_state')
        print('Machine detected with substate:', sub_state)
        if sub_state == 'beep_start':
            # Voicemail beep detected - leave message
            # print('Beep detected, playing the voicemail message')
            # ncco = [ #### 20250721 updates ended here 12:07 machine time

                {

                    'action': 'talk',

                    'text': '<speak>This is Baylor Scott and White Orthopedic Associates calling to remind you of your upcoming appointment.</speak>',

                    'language': 'en-US',

                    'style': 2,

                    'premium': True,

                    'level': 1,

                    'loop': 1,

                }

            ]

            print('Returning voicemail NCCO:', json.dumps(ncco, indent=2))

            return JSONResponse(content=ncco, status_code=200)

        else:

            # Machine detected but no beep - likely call screening

            print("Initial machine detected, playing call screener greeting")

            ncco = [

                {

                    'action': 'talk',

                    'text': '<speak>Baylor Scott and White Orthopedics appointment reminder.</speak>',

                    'language': 'en-US',

                    'style': 2,

                    'premium': True,

                    'level': 1,

                    'loop': 1

                }

            ]

            print("Returning screening NCCO:", json.dumps(ncco, indent=2))

            return JSONResponse(content=ncco, status_code=200)

    # Default response for other event types

    return JSONResponse(content={'status': 'success'}, status_code=200)


@app.route("/asr", methods=['POST'])
def asr_webhook():
    data = request.json
    conversation_uuid = data.get('conversation_uuid', 'unknown')

    # Ensure the 'webhooks' directory exists
    webhooks_dir = 'asr'
    os.makedirs(webhooks_dir, exist_ok=True)

    # Write event data to a file in the webhooks directory
    file_path = os.path.join(webhooks_dir, f"asr_{conversation_uuid}.json")
    with open(file_path, 'a', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write('\n')  # Add a newline for readability between events


    return jsonify({"status": "success"}), 200
@app.route("/recording", methods=['POST'])
def recording_webhook():
    data = request.json
    recording_url = data['recording_url']
    conversation_uuid = data.get('conversation_uuid', 'unknown')

    # Add the download task to the queue
    download_queue.put((recording_url, conversation_uuid))

    return jsonify({"status": "success"}), 200
@app.route("/rtc_events", methods=['POST'])
def rtc_events_webhook():
    data = request.json
    conversation_id = data.get('conversation_id') or data.get('body', {}).get('id', 'unknown')

    # Ensure the 'webhooks' directory exists
    webhooks_dir = 'webhooks'
    os.makedirs(webhooks_dir, exist_ok=True)

    # Write RTC event data to a file in the webhooks directory
    file_path = os.path.join(webhooks_dir, f"rtc_events_{conversation_id}.json")
    with open(file_path, 'a') as f:
        json.dump(data, f, indent=2)
        f.write('\n')  # Add a newline for readability between events


    return jsonify({"status": "success"}), 200


def run_test_cycle():
    total_calls = len(TEST_LOOP) * 1  # Total number of calls to make
    numbers = TEST_LOOP * 3 #10  # Repeat each number 10 times
    random.shuffle(numbers)  # Shuffle the list of numbers

    for i, number in enumerate(numbers, 1):
        print(f"Attempting call {i} of {total_calls} to {number}")
        make_call(number)
        wait_time = random.randint(70, 90)
        print(f"Waiting for {wait_time} seconds before next call")
        time.sleep(wait_time)

    # Wait for all downloads to complete
    download_queue.join()

    # Retry failed downloads
    retry_failed_downloads()

    print("All calls and downloads completed.")



if __name__ == "__main__":
    # Start the download worker thread
    threading.Thread(target=download_worker, daemon=True).start()

    # Start the test cycle in a separate thread
    test_cycle_thread = threading.Thread(target=run_test_cycle)
    test_cycle_thread.start()

    # Run the Flask app
    app.run(port=5003)

    # Wait for the test cycle to complete
    test_cycle_thread.join()

    print("Script execution completed.")