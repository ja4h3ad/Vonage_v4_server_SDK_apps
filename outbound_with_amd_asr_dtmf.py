from vonage import Vonage, Auth
from vonage_voice import CreateCallRequest, Talk, Record, Input
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
auth = Auth(application_id=VONAGE_APPLICATION_ID, private_key=VONAGE_PRIVATE_KEY)
vonage = Vonage(auth)
app = FastAPI()

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
                file_extension = '.wav' # automatically creates a .wav file if the file does not come with extension

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

def download_worker():
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
    print("Retrying failed downloads...")
    retry_queue = queue.Queue()

    while not failed_downloads.empty():
        recording_url, conversation_uuid = failed_downloads.get()
        success = download_recording(recording_url, conversation_uuid)
        if not success:
            retry_queue.put((recording_url, conversation_uuid))
        failed_downloads.task_done()

    # Handle items that failed retry
    while not retry_queue.empty():
        recording_url, conversation_uuid = retry_queue.get()
        print(f'Failed to download recording for conversation {conversation_uuid} after retries')

threading.Thread(target=download_worker, daemon=True).start()


def make_call(to_number, max_retries=5, initial_delay=1):
    for attempt in range(max_retries):
        try:
            print(f'Calling {to_number}...')

            # Create the call request object
            call_request = CreateCallRequest(
                to=[{'type': 'phone', 'number': to_number}],
                from_={'type': 'phone', 'number': VONAGE_NUMBER},
                ringing_timer=60,
                ncco=[
                    {
                        'action': 'record',
                        'eventUrl': [get_webhook_url('recording')],
                        'split': 'conversation',
                        'channels': 2,
                        'public': True,
                        'validity_time': 30,
                        'format': 'wav'
                    }
                ],
                advanced_machine_detection={
                    'behavior': 'continue',
                    'mode': 'default',
                    'beep_timeout': 45
                },
                event_url=[get_webhook_url('event')],
                event_method='POST'
            )

            # Make the call using the request object
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

# webhook handlers using FastAPI to match Pydantic structures of Vonage SDK

@app.post("/dtmf_input")
async def dtmf_input_webhook(request: Request):
    data = await request.json()
    print("Full input webhook data:", json.dumps(data, indent=2))  # Fixed typo

    conversation_uuid = data.get("conversation_uuid", "unknown")

    # Ensure the webhooks directory exists
    webhooks_dir = 'webhooks'
    os.makedirs(webhooks_dir, exist_ok=True)

    # write the webhook DTMF data to a file
    file_path = os.path.join(webhooks_dir, f"dtmf_input_{conversation_uuid}.json")
    with open(file_path, 'a', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    # DTMF input processing
    dtmf_data = data.get('dtmf', {})
    dtmf = dtmf_data.get('digits') if isinstance(dtmf_data, dict) else dtmf_data

    # speech input processing
    speech_results = data.get('speech', {}).get('results', [])

    if speech_results:
        speech_text_raw = speech_results[0].get('text', '') if speech_results[0] else ''
        speech_text = speech_text_raw.strip() if speech_text_raw else ''
    else:
        speech_text = ''

    print(f'Processed input for conversation {conversation_uuid}')
    print(f'DTMF data: {dtmf_data}')
    print(f'DTMF digits: {dtmf}')
    print(f'Speech text: {speech_text}')

    def handle_input(input_value):
        pass

    # process DTMF or speech input
    if dtmf and isinstance(dtmf_data, dict) and dtmf_data.get('digits'):
        ncco = handle_input(dtmf)
    elif speech_text:
        ncco = handle_input(speech_text.lower())
    else:
        ncco = None

    # handle invalid DTMF or speech input and create new input prompt
    if ncco is None:
        ncco = [
            {
                'action': 'talk',
                'text': '<speak>Invalid input. Please press or say 1 to confirm, 2 to reschedule, or 7 to repeat the message.</speak>',  # Fixed typo
                'language': 'en-US',
                'style': 2,
                'premium': True
            },
            {
                'action': 'input',
                'dtmf': {
                    'maxDigits': 1,
                    'timeOut': 10  # Note: should be 'timeOut' not 'timeout'
                },
                'speech': {  # Fixed: moved speech out of dtmf block
                    'language': 'en-US',
                    'context': ['1', '2', '7'],
                    'startTimeout': 10,
                    'maxDuration': 5,
                    'endOnSilence': 1.5  # Fixed typo: 'endsOnSilence' → 'endOnSilence'
                },
                'type': ['dtmf', 'speech'],
                'eventUrl': [get_webhook_url('dtmf_input')],
                'eventMethod': 'POST'
            }
        ]

    print(f"Returning NCCO: {json.dumps(ncco, indent=2)}")
    return JSONResponse(content=ncco, status_code=200)


@app.post("/event")
async def event_webhook(request: Request):
    data = await request.json()
    status = data.get('status')
    print(f'Event webhook data received with status: {status}')
    print(f'Full data:', json.dumps(data, indent=2))
    conversation_uuid = data.get("conversation_uuid", "unknown")

    # validate the webhooks directory exists, if not, build it
    webhooks_dir = 'webhooks'
    os.makedirs(webhooks_dir, exist_ok=True)

    # write the event webhook data to the directory
    file_path = os.path.join(webhooks_dir, f"event_{conversation_uuid}.json")
    with open(file_path, 'a') as f:
        json.dump(data, f, indent=2)
        f.write("\n")

    # logging for speech and ASR events
    if 'speech' in data:
        print('Capturing ASR/Speech event')
        speech_file_path = os.path.join(webhooks_dir, f"speech_{conversation_uuid}.json")
        with open(speech_file_path, 'a', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")

    # AMD behavior below for human and machine detection
    if status == 'human':
        print('Human detected, starting IVR flow')
        ncco = [
            {
                'action': 'talk',
                'text': '<speak>This is a test of Vonage Automatic Speech Recognition and Advanced Machine Detection  You can speak to me, or likewise use use your phone keypad.  Press or say one to confirm, two to reschedule, or seven to repeat your options</speak>',
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

        # beep start detection based on webhook
        if sub_state == 'beep_start':
            print('Beep detected, playing the voicemail message')
            ncco = [
                {
                    'action': 'talk',
                    'text': '''<speak>This is Baylor Scott and White Orthopedic Associates calling to remind you of your upcoming appointment.</speak>''',
                    'language': 'en-US',  # Fixed: was 'language: "en-US",'
                    'style': 2,
                    'premium': True,
                    'level': 1,
                    'loop': 1,
                }
            ]
            print('Returning voicemail NCCO:', json.dumps(ncco, indent=2))
            return JSONResponse(content=ncco, status_code=200)

        else:  # Fixed: proper indentation and placement
            # conditional logic to handle call screening systems where a machine is detected but no beep
            print("Initial machine detected, playing call screener greeting")
            ncco = [
                {
                    'action': 'talk',
                    'text': '''<speak>Hi, this is Baylor Scott and White Orthopedic Associates, calling to remind you of your appointment tomorrow.</speak>''',
                    'language': 'en-US',
                    'style': 2,
                    'premium': True,
                    'level': 1,
                    'loop': 1
                }
            ]
            print("Returning screening NCCO:", json.dumps(ncco, indent=2))
            return JSONResponse(content=ncco, status_code=200)

    # fallback response
    return JSONResponse(content={'status': 'success'}, status_code=200)

@app.post('/asr')
async def asr_webhook(request: Request):
    data = await request.json()
    status = data.get('status')
    print(f'Event webhook data received with status: {status}')
    print(f'Full data:', json.dumps(data, indent=2))
    conversation_uuid = data.get("conversation_uuid", "unknown")
    # ensure the webhooks directory exists
    webhooks_dir = 'asr'
    os.makedirs(webhooks_dir, exist_ok=True)
    # write event data to a file
    file_path = os.path.join(webhooks_dir, f"asr_{conversation_uuid}.json")
    with open(file_path, 'a', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n") # new line for readability


    return JSONResponse(content={'status': 'success'}, status_code=200)

@app.post('/recording')
async def recording_webhook(request: Request):
    data = await request.json()
    recording_url = data.get('recording_url')
    conversation_uuid = data.get("conversation_uuid", "unknown")

    # add the download task to the queue
    download_queue.put((recording_url, conversation_uuid))
    return JSONResponse(content={'status': 'success'}, status_code=200)


@app.post('/rtc_events') #optional to include, rtc events are chatty
async def rtc_events_webhook(request: Request):
    data = await request.json()
    conversation_uuid = data.get("conversation_uuid", "unknown") or data.get('body', {}).get('id', 'unknown')
    # ensure the webhooks directory exists
    webhooks_dir = 'rtc_events'
    os.makedirs(webhooks_dir, exist_ok=True)
    file_path = os.path.join(webhooks_dir, f"rtc_{conversation_uuid}.json")
    with open(file_path, 'a') as f:
        json.dump(data, f, indent=2)
        f.write("\n") # for readability

    return JSONResponse(content={'status': 'success'}, status_code=200)


def run_test_cycle():
    total_calls = len(TEST_LOOP)*1 # this is the total number of times to run the the test loop
    numbers = TEST_LOOP * 1 #1-10, repeating each number for that integer value
    random.shuffle(numbers) # shuffle the numbers to prevent the calls from being detected as fraud

    for i, number in enumerate(numbers):
        print(f'Attempting call {i} of {total_calls} to {number}')
        make_call(number) #run the make call function which contains preliminary set of call control logic
        wait_time = random.randint(70, 90) # this is the wait time between calls, again, to prevent calls from being flagged
        print(f'Waiting for {wait_time} seconds before next call')
        time.sleep(wait_time)

    # wait for media recording downloads to complete
    download_queue.join()

    # retry failed downloads
    retry_failed_downloads()
    print('All calls and downloads are complete')


if __name__ == '__main__':
    # start the download worker thread
    threading.Thread(target=download_worker, daemon=True).start()

    # start the test cycle in a separate thread
    test_cycle_thread = threading.Thread(target=run_test_cycle)
    test_cycle_thread.start()
    # designate the port for webhook service (ngrok)
    uvicorn.run(app, host="0.0.0.0", port=5003)
    # wait for the test cycle to complete
    test_cycle_thread.join()
    print("Script execution complete")









