"""
Vonage Voice API Outbound Calling Application

This application demonstrates advanced voice features including:
- Outbound call automation with retry logic
- Advanced Machine Detection (AMD)
- Automatic Speech Recognition (ASR)
- DTMF input handling
- Call recording with automatic download
- Interactive Voice Response (IVR) system

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

# Initialize queues for asynchronous file downloads
download_queue = queue.Queue()
failed_downloads = queue.Queue()

# Load environment variables from .env file
dotenv_path = join(dirname(__file__), '.env')
load_dotenv(dotenv_path)

# Configuration from environment variables
VONAGE_APPLICATION_ID = os.getenv('VONAGE_APPLICATION_ID')
VONAGE_PRIVATE_KEY = os.getenv('VONAGE_APPLICATION_PRIVATE_KEY_PATH')
VONAGE_NUMBER = os.getenv('VONAGE_NUMBER')
WEBHOOK_BASE_URL = os.getenv('WEBHOOK_BASE_URL')
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
    """
    Initiate outbound call with Advanced Machine Detection and recording

    Args:
        to_number (str): Phone number to call
        max_retries (int): Maximum retry attempts for failed calls
        initial_delay (int): Initial delay between retries in seconds
    """
    for attempt in range(max_retries):
        try:
            print(f'Calling {to_number}...')

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

    # Enhanced debugging - check for any speech-related data
    if any(key in data for key in ['speech', 'asr', 'transcription']):
        print("🎤 SPEECH/ASR DATA DETECTED IN EVENT WEBHOOK!")
        print(f"Speech data keys found: {[k for k in data.keys() if 'speech' in k.lower() or 'asr' in k.lower()]}")

    # Log all events for debugging
    webhooks_dir = 'webhooks'
    os.makedirs(webhooks_dir, exist_ok=True)
    file_path = os.path.join(webhooks_dir, f"event_{conversation_uuid}.json")
    with open(file_path, 'a') as f:
        json.dump(data, f, indent=2)
        f.write("\n")

    # Special logging for speech events BEFORE status handling
    if 'speech' in data:
        print('🎤 Capturing ASR/Speech event in EVENT webhook')
        speech_file_path = os.path.join(webhooks_dir, f"speech_{conversation_uuid}.json")
        with open(speech_file_path, 'a', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")

    # Handle call status events
    if status == 'answered':
        print('Call answered, starting immediate ASR to capture AI screener greeting')
        ncco = [
            {
                'action': 'input',
                'speech': {
                    'language': 'en-US',
                    'startTimeout': 1,  # Start listening immediately
                    'maxDuration': 45,  # Long enough to capture full greeting
                    'endOnSilence': 8,  # Long silence to avoid cutting off
                    'context': []
                },
                'type': ['speech'],
                'eventUrl': [get_webhook_url('initial_capture')],
                'eventMethod': 'POST'
            }
        ]
        print("🎯 Starting initial ASR capture on answered")
        return JSONResponse(content=ncco, status_code=200)

    elif status == 'human':
        print('Human detected, starting IVR flow')
        ncco = [
            {
                'action': 'talk',
                'text': '<speak>This is Baylor Scott and White Orthopedics, with an appointment confirmation call. You can speak to me, or likewise, use your phone keypad. Press or say one to confirm; two to reschedule; or seven to repeat your options.</speak>',
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
                    'context': ['1', '2', '7'],
                    'startTimeout': 10,
                    'maxDuration': 5,
                    'endOnSilence': 1.5
                },
                'type': ['dtmf', 'speech'],
                'eventUrl': [get_webhook_url('dtmf_input')],
                'eventMethod': 'POST'
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


@app.post("/asr_capture")
async def asr_capture_webhook(request: Request):
    """
    Handle ASR results from immediate capture on call answer
    This captures the very first speech from AI screeners
    """
    data = await request.json()
    print("🎯 Immediate ASR capture webhook:", json.dumps(data, indent=2))
    conversation_uuid = data.get("conversation_uuid", "unknown")

    # Enhanced logging for immediate capture
    if 'speech' in data:
        speech_data = data.get('speech', {})
        results = speech_data.get('results', [])
        if results and results[0]:
            detected_text = results[0].get('text', '').strip()
            confidence = results[0].get('confidence', 0)
            print(f"🎯 IMMEDIATE CAPTURE: '{detected_text}' (confidence: {confidence})")

            # Log the immediate capture for analysis
            immediate_responses_dir = 'immediate_responses'
            os.makedirs(immediate_responses_dir, exist_ok=True)
            response_file = os.path.join(immediate_responses_dir, f"immediate_{conversation_uuid}.json")

            immediate_response = {
                'timestamp': data.get('timestamp'),
                'conversation_uuid': conversation_uuid,
                'detected_text': detected_text,
                'confidence': confidence,
                'full_speech_data': speech_data,
                'capture_type': 'immediate_on_answer'
            }

            with open(response_file, 'a', encoding='utf-8') as f:
                json.dump(immediate_response, f, indent=2, ensure_ascii=False)
                f.write('\n')

            # Now respond appropriately to what was captured
            detected_text_lower = detected_text.lower()

            if any(phrase in detected_text_lower for phrase in
                   ['hello', 'hi', 'thanks for calling', 'please hold', 'one moment']):
                print("🎯 Detected AI screener greeting, responding professionally")
                ncco = [
                    {
                        'action': 'talk',
                        'text': '<speak>Hello, this is Baylor Scott and White Orthopedics calling about an appointment confirmation.</speak>',
                        'language': 'en-US',
                        'style': 2,
                        'premium': True
                    },
                    {
                        'action': 'input',
                        'speech': {
                            'language': 'en-US',
                            'startTimeout': 5,
                            'maxDuration': 30,
                            'endOnSilence': 3,
                            'context': []
                        },
                        'type': ['speech'],
                        'eventUrl': [get_webhook_url('asr_machine')],
                        'eventMethod': 'POST'
                    }
                ]
                return JSONResponse(content=ncco, status_code=200)
            else:
                print("🎯 Continuing to listen for more screener speech")
                ncco = [
                    {
                        'action': 'input',
                        'speech': {
                            'language': 'en-US',
                            'startTimeout': 5,
                            'maxDuration': 30,
                            'endOnSilence': 4,
                            'context': []
                        },
                        'type': ['speech'],
                        'eventUrl': [get_webhook_url('asr_capture')],
                        'eventMethod': 'POST'
                    }
                ]
                return JSONResponse(content=ncco, status_code=200)

    # Default: continue listening
    ncco = [
        {
            'action': 'input',
            'speech': {
                'language': 'en-US',
                'startTimeout': 10,
                'maxDuration': 30,
                'endOnSilence': 4,
                'context': []
            },
            'type': ['speech'],
            'eventUrl': [get_webhook_url('asr_capture')],
            'eventMethod': 'POST'
        }
    ]

    return JSONResponse(content=ncco, status_code=200)


@app.post("/initial_capture")
async def initial_capture_webhook(request: Request):
    """
    Handle ASR results from initial call answer - captures AI screener greeting
    This runs asynchronously while AMD analyzes the call
    """
    data = await request.json()
    print("🎯 Initial capture webhook:", json.dumps(data, indent=2))
    conversation_uuid = data.get("conversation_uuid", "unknown")

    # Store the initial capture globally for later use
    if 'speech' in data:
        speech_data = data.get('speech', {})
        results = speech_data.get('results', [])
        if results and results[0]:
            detected_text = results[0].get('text', '').strip()
            confidence = results[0].get('confidence', 0)
            print(f"🎯 INITIAL SCREENER GREETING: '{detected_text}' (confidence: {confidence})")

            # Store this for use in machine detection logic
            initial_greetings_dir = 'initial_greetings'
            os.makedirs(initial_greetings_dir, exist_ok=True)
            greeting_file = os.path.join(initial_greetings_dir, f"greeting_{conversation_uuid}.json")

            greeting_data = {
                'conversation_uuid': conversation_uuid,
                'initial_greeting': detected_text,
                'confidence': confidence,
                'timestamp': data.get('timestamp'),
                'full_speech_data': speech_data
            }

            with open(greeting_file, 'w', encoding='utf-8') as f:
                json.dump(greeting_data, f, indent=2, ensure_ascii=False)

            print(f"🎯 Stored initial greeting for conversation {conversation_uuid}")
        else:
            print("🎯 No speech detected in initial capture")

    # Return empty NCCO - we don't want to interfere with AMD flow
    # AMD will take over and send machine/human status
    return JSONResponse(content=[], status_code=200)


def get_initial_greeting(conversation_uuid):
    """
    Retrieve the stored initial greeting for a conversation
    """
    try:
        greeting_file = os.path.join('initial_greetings', f"greeting_{conversation_uuid}.json")
        if os.path.exists(greeting_file):
            with open(greeting_file, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"Error reading initial greeting: {e}")
    return None


@app.post("/asr_machine")
async def asr_machine_webhook(request: Request):
    """
    Handle ASR results from machine/call screener interactions
    This captures what automated systems are saying
    """
    data = await request.json()
    print("🤖 Machine ASR webhook data:", json.dumps(data, indent=2))
    conversation_uuid = data.get("conversation_uuid", "unknown")

    # Enhanced logging for machine ASR
    if 'speech' in data:
        speech_data = data.get('speech', {})
        results = speech_data.get('results', [])
        if results and results[0]:
            detected_text = results[0].get('text', '').strip()
            confidence = results[0].get('confidence', 0)
            print(f"🤖 MACHINE SAID: '{detected_text}' (confidence: {confidence})")

            # Log the machine's response for analysis
            machine_responses_dir = 'machine_responses'
            os.makedirs(machine_responses_dir, exist_ok=True)
            response_file = os.path.join(machine_responses_dir, f"machine_{conversation_uuid}.json")

            machine_response = {
                'timestamp': data.get('timestamp'),
                'conversation_uuid': conversation_uuid,
                'detected_text': detected_text,
                'confidence': confidence,
                'full_speech_data': speech_data
            }

            with open(response_file, 'a', encoding='utf-8') as f:
                json.dump(machine_response, f, indent=2, ensure_ascii=False)
                f.write('\n')

            # Analyze what the machine said and respond accordingly
            detected_text_lower = detected_text.lower()

            if any(phrase in detected_text_lower for phrase in
                   ['who is this', 'who\'s calling', 'state your business']):
                print("🤖 Call screener asking for identification")
                ncco = [
                    {
                        'action': 'talk',
                        'text': '<speak>This is Baylor Scott and White Orthopedics calling about an appointment confirmation.</speak>',
                        'language': 'en-US',
                        'style': 2,
                        'premium': True
                    },
                    {
                        'action': 'input',
                        'speech': {
                            'language': 'en-US',
                            'startTimeout': 10,
                            'maxDuration': 20,
                            'endOnSilence': 2,
                            'context': []
                        },
                        'type': ['speech'],
                        'eventUrl': [get_webhook_url('asr_machine')],
                        'eventMethod': 'POST'
                    }
                ]
                return JSONResponse(content=ncco, status_code=200)

            elif any(phrase in detected_text_lower for phrase in ['hold on', 'wait', 'one moment']):
                print("🤖 Call screener asking to wait")
                ncco = [
                    {
                        'action': 'talk',
                        'text': '<speak>Thank you, I\'ll wait.</speak>',
                        'language': 'en-US',
                        'style': 2,
                        'premium': True
                    },
                    {
                        'action': 'input',
                        'speech': {
                            'language': 'en-US',
                            'startTimeout': 30,  # Longer timeout for waiting
                            'maxDuration': 60,
                            'endOnSilence': 5,
                            'context': []
                        },
                        'type': ['speech'],
                        'eventUrl': [get_webhook_url('asr_machine')],
                        'eventMethod': 'POST'
                    }
                ]
                return JSONResponse(content=ncco, status_code=200)

            elif any(phrase in detected_text_lower for phrase in ['not available', 'not here', 'unavailable']):
                print("🤖 Call screener says person is unavailable")
                ncco = [
                    {
                        'action': 'talk',
                        'text': '<speak>Thank you. Could you please let them know that Baylor Scott and White called about their appointment? They can call us back at their convenience.</speak>',
                        'language': 'en-US',
                        'style': 2,
                        'premium': True
                    }
                ]
                return JSONResponse(content=ncco, status_code=200)

            else:
                print("🤖 Unrecognized machine response, continuing to listen")
                ncco = [
                    {
                        'action': 'input',
                        'speech': {
                            'language': 'en-US',
                            'startTimeout': 10,
                            'maxDuration': 20,
                            'endOnSilence': 3,
                            'context': []
                        },
                        'type': ['speech'],
                        'eventUrl': [get_webhook_url('asr_machine')],
                        'eventMethod': 'POST'
                    }
                ]
                return JSONResponse(content=ncco, status_code=200)
        else:
            print("🤖 No speech detected from machine")

    # Log all machine ASR events
    webhooks_dir = 'machine_asr'
    os.makedirs(webhooks_dir, exist_ok=True)
    file_path = os.path.join(webhooks_dir, f"machine_asr_{conversation_uuid}.json")
    with open(file_path, 'a', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    # Default response - continue listening
    ncco = [
        {
            'action': 'input',
            'speech': {
                'language': 'en-US',
                'startTimeout': 15,
                'maxDuration': 30,
                'endOnSilence': 3,
                'context': []
            },
            'type': ['speech'],
            'eventUrl': [get_webhook_url('asr_machine')],
            'eventMethod': 'POST'
        }
    ]

    return JSONResponse(content=ncco, status_code=200)


@app.post('/asr')
async def asr_webhook(request: Request):
    """
    Handle Automatic Speech Recognition events (optional)
    Logs ASR data for analysis and debugging
    """
    data = await request.json()
    status = data.get('status')
    print(f'🎤 ASR webhook data received with status: {status}')
    print(f'🎤 Full ASR data:', json.dumps(data, indent=2))
    conversation_uuid = data.get("conversation_uuid", "unknown")

    # Log ASR events
    webhooks_dir = 'asr'
    os.makedirs(webhooks_dir, exist_ok=True)
    file_path = os.path.join(webhooks_dir, f"asr_{conversation_uuid}.json")
    with open(file_path, 'a', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return JSONResponse(content={'status': 'success'}, status_code=200)


@app.post('/rtc_events')
async def rtc_events_webhook(request: Request):
    """
    Handle Real-Time Communication events (optional)
    These events provide detailed call state information but are verbose
    """
    data = await request.json()
    print(f"📞 RTC Event received: {json.dumps(data, indent=2)}")

    # Fixed: Proper conversation ID extraction with multiple fallbacks
    conversation_uuid = None

    # Method 1: Direct conversation_uuid field
    conversation_uuid = data.get("conversation_uuid")

    # Method 2: Direct conversation_id field
    if not conversation_uuid:
        conversation_uuid = data.get("conversation_id")

    # Method 3: In body.id (for conversation:created events)
    if not conversation_uuid and 'body' in data:
        conversation_uuid = data['body'].get('id')

    # Method 4: In body.conversation.conversation_id (for member events)
    if not conversation_uuid and 'body' in data and 'conversation' in data['body']:
        conversation_uuid = data['body']['conversation'].get('conversation_id')

    # Method 5: Look for any field containing 'conversation' and an ID-like value
    if not conversation_uuid:
        for key, value in data.items():
            if 'conversation' in key.lower() and isinstance(value, str) and value.startswith('CON-'):
                conversation_uuid = value
                break

        # Also check nested in body
        if not conversation_uuid and 'body' in data:
            for key, value in data['body'].items():
                if 'conversation' in key.lower() and isinstance(value, str) and value.startswith('CON-'):
                    conversation_uuid = value
                    break

    # Final fallback
    if not conversation_uuid:
        conversation_uuid = 'unknown'

    print(f"📞 RTC Event for conversation: {conversation_uuid}")

    # Log RTC events
    webhooks_dir = 'rtc_events'
    os.makedirs(webhooks_dir, exist_ok=True)
    file_path = os.path.join(webhooks_dir, f"rtc_{conversation_uuid}.json")

    try:
        with open(file_path, 'a') as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        print(f"📞 RTC Event logged to: {file_path}")
    except Exception as e:
        print(f"❌ Failed to write RTC event: {e}")

    return JSONResponse(content={'status': 'success'}, status_code=200)


@app.post("/dtmf_input")
async def dtmf_input_webhook(request: Request):
    """
    Handle DTMF and speech input from callers during IVR interactions
    Processes both keypad input and voice commands
    """
    data = await request.json()
    print("📝 Full input webhook data:", json.dumps(data, indent=2))

    conversation_uuid = data.get("conversation_uuid", "unknown")

    # Enhanced debugging for input events
    if 'speech' in data:
        print("🎤 SPEECH DATA FOUND IN DTMF_INPUT WEBHOOK!")
        speech_data = data.get('speech', {})
        print(f"🎤 Speech data: {json.dumps(speech_data, indent=2)}")

    # Log webhook data for debugging
    webhooks_dir = 'webhooks'
    os.makedirs(webhooks_dir, exist_ok=True)
    file_path = os.path.join(webhooks_dir, f"dtmf_input_{conversation_uuid}.json")
    with open(file_path, 'a', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    # Process DTMF input
    dtmf_data = data.get('dtmf', {})
    dtmf = dtmf_data.get('digits') if isinstance(dtmf_data, dict) else dtmf_data

    # Process speech recognition results
    speech_results = data.get('speech', {}).get('results', [])
    if speech_results:
        speech_text_raw = speech_results[0].get('text', '') if speech_results[0] else ''
        speech_text = speech_text_raw.strip() if speech_text_raw else ''
    else:
        speech_text = ''

    print(f'📝 Processed input for conversation {conversation_uuid}')
    print(f'📝 DTMF data: {dtmf_data}')
    print(f'📝 DTMF digits: {dtmf}')
    print(f'📝 Speech text: "{speech_text}"')

    def handle_input(input_value):
        """
        Process caller input and return appropriate NCCO response
        """
        if input_value == "1":  # Confirmation
            return [
                {
                    'action': 'talk',
                    'text': '<speak>Thank you for confirming your appointment. Have a great day!</speak>',
                    'language': 'en-US',
                    'style': 2,
                    'premium': True
                }
            ]
        elif input_value == "2":  # Reschedule request
            return [
                {
                    'action': 'talk',
                    'text': '<speak>Please call our office to reschedule your appointment.</speak>',
                    'language': 'en-US',
                    'style': 2,
                    'premium': True
                }
            ]
        elif input_value == "7":  # Repeat options
            return [
                {
                    'action': 'talk',
                    'text': '<speak>Please press or say 1 to confirm, 2 to reschedule, or 7 to repeat your options.</speak>',
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
                        'context': ['1', '2', '7'],
                        'startTimeout': 10,
                        'maxDuration': 5,
                        'endOnSilence': 1.5
                    },
                    'type': ['dtmf', 'speech'],
                    'eventUrl': [get_webhook_url('dtmf_input')],
                    'eventMethod': 'POST'
                }
            ]
        else:  # Invalid input
            return None

    # Route input to appropriate handler
    if dtmf and isinstance(dtmf_data, dict) and dtmf_data.get('digits'):
        ncco = handle_input(dtmf)
    elif speech_text:
        print(f"🎤 Processing speech input: '{speech_text}'")
        ncco = handle_input(speech_text.lower())
    else:
        ncco = None

    # Handle invalid input with default response
    if ncco is None:
        ncco = [
            {
                'action': 'talk',
                'text': '<speak>Invalid input. Please press or say 1 to confirm, 2 to reschedule, or 7 to repeat the message.</speak>',
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
                    'context': ['1', '2', '7'],
                    'startTimeout': 10,
                    'maxDuration': 5,
                    'endOnSilence': 1.5
                },
                'type': ['dtmf', 'speech'],
                'eventUrl': [get_webhook_url('dtmf_input')],
                'eventMethod': 'POST'
            }
        ]

    print(f"📝 Returning NCCO: {json.dumps(ncco, indent=2)}")
    return JSONResponse(content=ncco, status_code=200)


@app.post('/recording')
async def recording_webhook(request: Request):
    """
    Handle recording completion events
    Queues recordings for asynchronous download
    """
    data = await request.json()
    recording_url = data.get('recording_url')
    conversation_uuid = data.get("conversation_uuid", "unknown")

    # Add download task to queue for background processing
    download_queue.put((recording_url, conversation_uuid))
    return JSONResponse(content={'status': 'success'}, status_code=200)



def run_test_cycle():
    """
    Execute automated test calling cycle
    Calls all numbers in TEST_LOOP with randomized timing to avoid fraud detection
    """
    total_calls = len(TEST_LOOP) * 1
    numbers = TEST_LOOP * 2  # Multiply to repeat test cycles if needed
    random.shuffle(numbers)  # Randomize order to prevent fraud detection

    for i, number in enumerate(numbers):
        print(f'Attempting call {i + 1} of {total_calls} to {number}')
        make_call(number)

        # Random delay between calls to simulate human behavior
        wait_time = random.randint(70, 90)
        print(f'Waiting for {wait_time} seconds before next call')
        time.sleep(wait_time)

    # Wait for all recording downloads to complete
    download_queue.join()

    # Retry any failed downloads
    retry_failed_downloads()
    print('All calls and downloads are complete')


if __name__ == '__main__':
    # Start background download worker
    threading.Thread(target=download_worker, daemon=True).start()

    # Start test cycle in separate thread
    test_cycle_thread = threading.Thread(target=run_test_cycle)
    test_cycle_thread.start()

    # Run FastAPI server
    uvicorn.run(app, host="0.0.0.0", port=5003)

    # Wait for test cycle completion
    test_cycle_thread.join()
    print("Script execution complete")




