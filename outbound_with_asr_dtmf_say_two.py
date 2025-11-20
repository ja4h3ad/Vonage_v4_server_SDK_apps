"""
Vonage Voice API Outbound Calling Application to test the input 2

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

# FastAPI and pydantic
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn
from typing import Optional, Dict, Any

import random
import json
from pprint import pprint
from os.path import join, dirname
import queue
import os
import time
import threading
from urllib.parse import urlparse, urljoin
import datetime
import logging

from fastapi_requests.message import InboundMessage
# Import our custom modules
from first_orion import get_auth_token, send_push_notification
from call_tracker import call_tracker

# Configure global logger
logging.basicConfig(
    level=logging.INFO,  # or DEBUG for more verbosity
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


# queue the audio file downloads
download_queue = queue.Queue()
# for any download failures
failed_downloads = queue.Queue()

# Load environment variables
dotenv_path = join(dirname(__file__), ".env")
load_dotenv(dotenv_path)

VONAGE_APPLICATION_ID = os.environ.get("VONAGE_APPLICATION_ID")
VONAGE_PRIVATE_KEY = os.environ.get("VONAGE_APPLICATION_PRIVATE_KEY_PATH")
VONAGE_NUMBER = os.environ.get("VONAGE_NUMBER")
WEBHOOK_BASE_URL = os.environ.get("WEBHOOK_BASE_URL")
TEST_LOOP = [str(num) for num in eval(os.getenv('TEST_LOOP', "['1', '2', '3']"))]

# Initialize Vonage client with application-based authentication
auth = Auth(application_id=VONAGE_APPLICATION_ID, private_key=VONAGE_PRIVATE_KEY)
vonage = Vonage(auth)

# Initialize FastAPI application
app = FastAPI(title="Vonage Voice API Demo with ASR, DTMF and Branded Calling", version="1.0.2")

# global helper functions

def start_step_recording(call_uuid: str, step: str, conversation_uuid: str) -> Optional[Dict[str, Any]]:
    """
    Start recording for a specific survey step with better error handling

    Args:
        call_uuid (str): The UUID of the call
        step (str): The step identifier (e.g., "step_1", "step_2", "step_3")
        conversation_uuid (str): The conversation UUID for tracking

    Returns:
        Dict containing recording info or None if failed
    """
    try:
        # Stop any existing recording first to avoid conflicts
        try:
            vonage.voice.stop_recording(uuid=call_uuid)
            logger.info(f"Stopped any existing recording for call {call_uuid}")
        except Exception as e:
            logger.debug(f"No existing recording to stop for call {call_uuid}: {e}")

        # Start new recording for this step
        response = vonage.voice.start_recording(
            uuid=call_uuid,
            eventUrl=[get_webhook_url('recording')],  # Important: Add eventUrl for notifications
            split="conversation",  # Record both channels separately
            channels=1,  # Single channel for user input only
            format="wav",
            endOnSilence=3,  # Stop recording after 3 seconds of silence
            endOnKey="*",  # Allow user to end recording with *
            timeOut=30  # Maximum recording time
        )

        recording_info = {
            "step": step,
            "recording_uuid": response.get("recording_uuid"),
            "started_at": datetime.datetime.now().isoformat(),
            "call_uuid": call_uuid,
            "conversation_uuid": conversation_uuid
        }

        # Update call tracker with recording info
        if hasattr(call_tracker, "active_calls"):
            for corr_id, call_data in call_tracker.active_calls.items():
                vonage_info = call_data.get("vonage", {})
                if vonage_info and vonage_info.get("call_uuid") == call_uuid:
                    recordings = vonage_info.setdefault("step_recordings", [])
                    recordings.append(recording_info)
                    break

        logger.info(f"Started step {step} recording for call {call_uuid}: {response.get('recording_uuid')}")
        return recording_info

    except Exception as e:
        logger.error(f"Error starting step {step} recording for call {call_uuid}: {e}")
        return None


def stop_step_recording(call_uuid: str, step: str) -> bool:
    """
    Stop the current step recording

    Args:
        call_uuid (str): The UUID of the call
        step (str): The step identifier

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        response = vonage.voice.stop_recording(uuid=call_uuid)
        logger.info(f"Stopped step {step} recording for call {call_uuid}")
        return True
    except Exception as e:
        logger.error(f"Error stopping step {step} recording for call {call_uuid}: {e}")
        return False

def get_webhook_url(endpoint):
    """
    Construct full webhook URL from base URL and endpoint

    Args:
        endpoint (str): The webhook endpoint path

    Returns:
        str: Complete webhook URL
    """
    return urljoin(WEBHOOK_BASE_URL, endpoint)


def download_recording_enhanced(recording_url, filename_prefix, recording_type, step_info=None, max_retries=5,
                                initial_delay=1):
    """
    Enhanced download function for different recording types

    Args:
        recording_url (str): URL of the recording to download
        filename_prefix (str): Prefix for the filename
        recording_type (str): Type of recording ('step' or 'full_call')
        step_info (dict): Optional step information for enhanced naming
        max_retries (int): Maximum number of retry attempts
        initial_delay (int): Initial delay between retries in seconds
    """
    for attempt in range(max_retries):
        try:
            # Create appropriate directory structure
            if recording_type == "step":
                recordings_dir = os.path.join('recordings', 'survey_steps')
            else:
                recordings_dir = os.path.join('recordings', 'full_calls')

            os.makedirs(recordings_dir, exist_ok=True)

            # Parse URL to determine file extension
            parsed_url = urlparse(recording_url)
            file_extension = os.path.splitext(parsed_url.path)[1]
            if not file_extension:
                file_extension = '.wav'

            # Generate enhanced filename using step_info if available
            if step_info and recording_type == "step":
                # Create more descriptive filename for step recordings
                step_name = step_info.get('step', 'unknown_step')
                conversation_uuid = step_info.get('conversation_uuid', 'unknown')
                timestamp = step_info.get('started_at', '').replace(':', '-').replace('.', '-')
                filename = f"survey_{step_name}_{conversation_uuid}_{timestamp}{file_extension}"
            else:
                # Use the provided filename_prefix
                filename = f"{filename_prefix}{file_extension}"

            file_path = os.path.join(recordings_dir, filename)

            # Download recording using Vonage SDK v4
            vonage.voice.download_recording(recording_url, file_path)

            # Verify download was successful
            if os.path.exists(file_path):
                file_size = os.path.getsize(file_path)
                print(f"Recording saved as {file_path} (Size: {file_size} bytes, Type: {recording_type})")

                # Log step info if available
                if step_info and recording_type == "step":
                    print(f"Step recording details: {step_info}")

                if file_size > 512:  # Lower minimum file size for step recordings
                    return True
                else:
                    print(f"Recording file seems too small ({file_size} bytes). Retrying...")
            else:
                print(f"Recording file was not created. Retrying...")

        except Exception as e:
            print(f"Failed to download {recording_type} recording. Error: {str(e)}")
            if step_info:
                print(f"Step info context: {step_info}")
            if attempt < max_retries - 1:
                delay = initial_delay * (2 ** attempt)
                print(f"Retrying in {delay} seconds...")
                time.sleep(delay)
            else:
                print(f"Failed to download {recording_type} recording after {max_retries} attempts.")
                return False

    return False


# Enhanced download worker to handle your 5-tuple format:


def download_worker_enhanced():
    """
    Enhanced background worker thread for processing recording download queue
    Handles both legacy 2-tuple and new 5-tuple formats
    """
    while True:
        try:
            item = download_queue.get()
            if item is None:  # Poison pill to stop worker
                break

            # Handle different tuple formats
            if len(item) == 2:
                # Legacy format: (recording_url, conversation_uuid)
                recording_url, conversation_uuid = item
                filename_prefix = f"recording_{conversation_uuid}"
                recording_type = "full_call"
                step_info = None
                custom_max_retries = 5  # Default

            elif len(item) == 4:
                # 4-tuple format: (recording_url, filename_prefix, recording_type, step_info)
                recording_url, filename_prefix, recording_type, step_info = item
                custom_max_retries = 5  # Default

            elif len(item) == 5:
                # Your 5-tuple format: (recording_url, filename_prefix, recording_type, step_info, max_retries)
                recording_url, filename_prefix, recording_type, step_info, custom_max_retries = item

            else:
                print(f"Unknown item format in download queue: {item}")
                continue

            # Use the custom max_retries from the tuple if provided
            success = download_recording_enhanced(
                recording_url,
                filename_prefix,
                recording_type,
                step_info,
                custom_max_retries
            )

            if not success:
                # Preserve the original tuple format when adding to failed queue
                failed_downloads.put(item)

        except Exception as e:
            print(f"Worker error: {e}")
            print(f"Problem item: {item}")
            # Handle error case - preserve original format
            failed_downloads.put(item)
        finally:
            download_queue.task_done()


def retry_failed_downloads_enhanced(max_retries=2):
    """
    Retry all failed download attempts with enhanced format support

    Args:
        max_retries (int): Maximum retry attempts for failed downloads
    """
    print("Retrying failed downloads...")
    retry_queue = queue.Queue()

    # Process all failed downloads
    while not failed_downloads.empty():
        try:
            item = failed_downloads.get()

            # Handle different tuple formats for retry
            if len(item) == 2:
                recording_url, conversation_uuid = item
                filename_prefix = f"recording_{conversation_uuid}"
                recording_type = "full_call"
                step_info = None
                custom_max_retries = max_retries
            elif len(item) == 4:
                recording_url, filename_prefix, recording_type, step_info = item
                custom_max_retries = max_retries
            elif len(item) == 5:
                recording_url, filename_prefix, recording_type, step_info, _ = item
                custom_max_retries = max_retries  # Override with retry max_retries
            else:
                print(f"Unknown failed item format: {item}")
                continue

            success = download_recording_enhanced(
                recording_url,
                filename_prefix,
                recording_type,
                step_info,
                custom_max_retries
            )

            if not success:
                retry_queue.put(item)

        except Exception as e:
            print(f"Error during retry: {e}")
            retry_queue.put(item)
        finally:
            failed_downloads.task_done()

    # Report permanently failed downloads
    while not retry_queue.empty():
        item = retry_queue.get()
        if len(item) >= 2:
            if len(item) >= 4 and item[3]:  # Has step_info
                step_info = item[3]
                conversation_uuid = step_info.get('conversation_uuid', 'unknown')
                step = step_info.get('step', 'unknown_step')
                print(
                    f'Permanently failed to download {item[2]} recording for conversation {conversation_uuid}, step {step}')
            else:
                # Legacy or full call format
                identifier = item[1] if len(item) > 1 else 'unknown'
                print(f'Permanently failed to download recording: {identifier}')



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
                    'beep_timeout': 90  # Wait 45 seconds for voicemail beep
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



@app.post('/inbound')
async def inbound_message(
        sms: InboundMessage
):
    """
    Handle inbound message and trigger outbound message from the same number
    """
    from_number = sms.from_
    print(f"received inbound message from this number: {from_number}")
    result = make_call(from_number)
    return {"from_number": sms.from_, "outbound_result": result}




@app.post("/dtmf_input")
async def dtmf_input_webhook(request: Request):
    """
    Handle DTMF and speech input from callers during IVR interactions
    Processes both keypad input and voice commands with improved recording
    """
    data = await request.json()
    print("Full input webhook data:", json.dumps(data, indent=2))

    conversation_uuid = data.get('conversation_uuid', 'unknown')

    # Get call UUID for recordings
    call_data = call_tracker.get_call_by_conversation_uuid(conversation_uuid) if hasattr(call_tracker,
                                                                                         'get_call_by_conversation_uuid') else None
    call_uuid = None
    if call_data and "vonage" in call_data:
        call_uuid = call_data["vonage"].get("call_uuid")

    # Track this event in our call tracker
    if hasattr(call_tracker, 'record_vonage_event'):
        call_tracker.record_vonage_event(conversation_uuid, data)

    # Save webhook data
    webhooks_dir = 'webhooks'
    os.makedirs(webhooks_dir, exist_ok=True)
    file_path = os.path.join(webhooks_dir, f"dtmf_input_{conversation_uuid}.json")
    with open(file_path, 'a', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write('\n')

    # Extract input from DTMF or speech
    dtmf_data = data.get('dtmf', {})
    dtmf = dtmf_data.get('digits') if isinstance(dtmf_data, dict) else dtmf_data

    speech_results = data.get('speech', {}).get('results', [])
    speech_text = ''
    if speech_results and isinstance(speech_results, list) and len(speech_results) > 0:
        if isinstance(speech_results[0], dict):
            text_value = speech_results[0].get('text')
            if text_value is not None:
                speech_text = text_value.strip()

    print(f"Processed input for conversation {conversation_uuid}:")
    print(f"DTMF data: {dtmf_data}")
    print(f"DTMF digits: {dtmf}")
    print(f"Speech text: {speech_text}")

    # Load existing responses
    responses_dir = 'responses'
    os.makedirs(responses_dir, exist_ok=True)
    response_file = os.path.join(responses_dir, f"survey_{conversation_uuid}.json")

    responses = {}
    if os.path.exists(response_file):
        try:
            with open(response_file, 'r') as f:
                responses = json.load(f)
        except:
            print(f"Error loading existing responses for {conversation_uuid}")

    # Determine current step
    if 'saw_vonage_caller_id' in responses:
        current_step = 4  # All questions answered
    elif 'saw_vonage_logo' in responses:
        current_step = 3  # Two questions answered
    elif 'device_type' in responses:
        current_step = 2  # One question answered
    else:
        current_step = 1  # No questions answered yet

    # Process user input
    user_input = None
    if dtmf and isinstance(dtmf_data, dict) and dtmf_data.get('digits'):
        user_input = dtmf
    elif speech_text:
        speech_text_lower = speech_text.lower()
        speech_map = {
            "one": "1", "two": "2",
            "yes": "1", "no": "2",
            "iphone": "1", "android": "2",
            "go": "go"
        }
        user_input = speech_map.get(speech_text_lower, speech_text_lower)

    print(f"User input: {user_input}")

    # Handle step progression and recording
    next_step = current_step

    if user_input == "go" and current_step == 1:
        # Start first question and begin recording
        if call_uuid:
            start_step_recording(call_uuid, "question_1", conversation_uuid)
        next_step = 1

    elif user_input and user_input != "go":
        # Stop current step recording before processing response
        if call_uuid:
            stop_step_recording(call_uuid, f"question_{current_step}")

        # Process the response
        if current_step == 1:
            responses['device_type'] = user_input
            next_step = 2
        elif current_step == 2:
            responses['saw_vonage_logo'] = user_input
            next_step = 3
        elif current_step == 3:
            responses['saw_vonage_caller_id'] = user_input
            next_step = 4

        # Save responses
        with open(response_file, 'w') as f:
            json.dump(responses, f, indent=2)

        # Record in call tracker
        if hasattr(call_tracker, 'record_survey_response'):
            if current_step == 1:
                call_tracker.record_survey_response(conversation_uuid, "device_type", user_input)
            elif current_step == 2:
                call_tracker.record_survey_response(conversation_uuid, "saw_vonage_logo", user_input)
            elif current_step == 3:
                call_tracker.record_survey_response(conversation_uuid, "saw_vonage_caller_id", user_input)

        # Start recording for next question (if there is one)
        if next_step < 4 and call_uuid:
            start_step_recording(call_uuid, f"question_{next_step}", conversation_uuid)

    print(f"Next step: {next_step}")

    # Generate NCCO based on next step
    if next_step == 1:
        ncco = [
            {
                'action': 'talk',
                'text': '<speak>Please say the number 2.</speak>',
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
                    'context': ['2', 'two'],
                    'startTimeout': 10,
                    'maxDuration': 5,
                    'endOnSilence': 0.4
                },
                'type': ['dtmf', 'speech'],
                'eventUrl': [get_webhook_url('dtmf_input')],
                'eventMethod': 'POST'
            }
        ]
    elif next_step == 2:
        ncco = [
            {
                'action': 'talk',
                'text': '<speak>Great. Please say the number two twice, with a brief pause in between each utterance, such as 2, 2.</speak>',
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
                    'context': ['2', 'two'],
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
        ncco = [
            {
                'action': 'talk',
                'text': '<speak>Fantastic!  Almost done.  Now, say the number two, three times, with a brief pause in between each utterance, such as 2, 2, 2 .</speak>',
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
                    'context': ['2', 'two'],
                    'startTimeout': 10,
                    'maxDuration': 5,
                    'endOnSilence': 1.5
                },
                'type': ['dtmf', 'speech'],
                'eventUrl': [get_webhook_url('dtmf_input')],
                'eventMethod': 'POST'
            }
        ]
    elif next_step == 4:
        # End of survey - stop any remaining recordings
        if call_uuid:
            stop_step_recording(call_uuid, f"question_{current_step}")

        # Update call status
        if call_data:
            correlation_id = call_data.get("correlation_id")
            if correlation_id and hasattr(call_tracker, 'active_calls') and correlation_id in call_tracker.active_calls:
                call_tracker.active_calls[correlation_id]["status"] = "survey_completed"

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
                'text': '<speak>This is a test of Vonage ASR.  I will be asking you to normally recite the digit 2.  Say the word "Go" when you are ready.</speak>',
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
            print('Beep detected, playing the voicemail message')
            ncco = [

                {
                    'action': 'talk',
                    'text': '<speak>This is the TTS that will play out if an answering machine beep is detected.</speak>',
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
            print("Initial machine detected, playing call screener greeting")
            ncco = [
                {
                    'action': 'talk',
                    'text': '<speak>This is the call screener TTS playout.</speak>',
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

@app.post("/asr")
async def asr_webhook(request: Request):
    data = await request.json()
    conversation_uuid = data.get('conversation_uuid', 'unknown')
    # Ensure the 'webhooks' directory exists
    webhooks_dir = 'asr'
    os.makedirs(webhooks_dir, exist_ok=True)
    # Write event data to a file in the webhooks directory
    file_path = os.path.join(webhooks_dir, f"asr_{conversation_uuid}.json")
    with open(file_path, 'a', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write('\n')  # Add a newline for readability between events
    return JSONResponse(content={'status': 'success'}, status_code=200)


@app.post("/recording")
async def recording_webhook(request: Request):
    """
    Enhanced recording webhook to handle both full call and step recordings
    """
    data = await request.json()
    recording_url = data['recording_url']
    conversation_uuid = data.get('conversation_uuid', 'unknown')
    recording_uuid = data.get('recording_uuid', 'unknown')

    print(f"Recording webhook received for conversation {conversation_uuid}")
    print(f"Recording URL: {recording_url}")
    print(f"Recording UUID: {recording_uuid}")

    # Determine if this is a full call recording or step recording
    recording_type = "full_call"  # Default assumption
    step_info = None

    # Check if this recording UUID matches any step recordings we started
    if hasattr(call_tracker, "active_calls"):
        for corr_id, call_data in call_tracker.active_calls.items():
            vonage_info = call_data.get("vonage", {})
            if vonage_info and vonage_info.get("conversation_uuid") == conversation_uuid:
                step_recordings = vonage_info.get("step_recordings", [])
                for step_rec in step_recordings:
                    if step_rec.get("recording_uuid") == recording_uuid:
                        recording_type = "step"
                        step_info = step_rec
                        break
                if step_info:
                    break

    # Create appropriate filename based on recording type
    if recording_type == "step" and step_info:
        filename_prefix = f"step_{step_info['step']}_{conversation_uuid}"
    else:
        filename_prefix = f"full_call_{conversation_uuid}"

    print(f"Recording type: {recording_type}")
    if step_info:
        print(f"Step info: {step_info}")

    # Add to download queue with additional metadata
    download_queue.put((recording_url, filename_prefix, recording_type, step_info))

    return JSONResponse(content={'status': 'success'}, status_code=200)


@app.post("/rtc_events")
async def rtc_events_webhook(request: Request):
    data = await request.json()
    conversation_id = data.get('conversation_id') or data.get('body', {}).get('id', 'unknown')

    # Ensure the 'webhooks' directory exists
    webhooks_dir = 'webhooks'
    os.makedirs(webhooks_dir, exist_ok=True)

    # Write RTC event data to a file in the webhooks directory
    file_path = os.path.join(webhooks_dir, f"rtc_events_{conversation_id}.json")
    with open(file_path, 'a') as f:
        json.dump(data, f, indent=2)
        f.write('\n')  # Add a newline for readability between events


    return JSONResponse(content={'status': 'success'}, status_code=200)


def run_test_cycle():
    total_calls = len(TEST_LOOP) * 1  # Total number of calls to make
    numbers = TEST_LOOP * 1 #10  # Repeat each number 10 times
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
    retry_failed_downloads_enhanced()

    print("All calls and downloads completed.")


if __name__ == '__main__':
    # Start background download worker
    threading.Thread(target=download_worker_enhanced, daemon=True).start()

    # Start test cycle in separate thread
    test_cycle_thread = threading.Thread(target=run_test_cycle)
    test_cycle_thread.start()

    # Run FastAPI server
    uvicorn.run(app, host="0.0.0.0", port=5003)

    # Wait for test cycle completion
    test_cycle_thread.join()
    print("Script execution complete")


    # # CORRECT - Pass the function reference without parentheses:
    # threading.Thread(target=download_worker_enhanced, daemon=True).start()
    #
    # # Start test cycle in separate thread
    # test_cycle_thread = threading.Thread(target=run_test_cycle)
    # test_cycle_thread.start()
    #
    # # Run FastAPI server
    # uvicorn.run(app, host="0.0.0.0", port=5003)
    #
    # # Wait for test cycle completion
    # test_cycle_thread.join()
    # print("Script execution complete")
