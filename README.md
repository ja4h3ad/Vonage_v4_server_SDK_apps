# Vonage Voice API Outbound Calling Application

A comprehensive Python application demonstrating advanced voice communication features using the Vonage Voice API SDK v4, FastAPI, and modern Python practices.

## Features

### Core Voice Capabilities
- **Outbound Call Automation** - Automated calling with intelligent retry logic
- **Advanced Machine Detection (AMD)** - Distinguishes between humans, voicemail, and call screening systems
- **Interactive Voice Response (IVR)** - Multi-modal input handling with DTMF and speech recognition
- **Call Recording** - Automatic recording with asynchronous download and retry mechanisms
- **Automatic Speech Recognition (ASR)** - Voice-to-text conversion for natural language interactions

### Technical Features
- **FastAPI Integration** - Modern async web framework for webhook handling
- **Threaded Architecture** - Background processing for file downloads and call management
- **Comprehensive Logging** - Detailed event logging for debugging and analysis
- **Exponential Backoff** - Intelligent retry logic for failed operations
- **Environment Configuration** - Secure credential management with .env files

## Architecture Overview

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Test Cycle    │    │   FastAPI App    │    │  Vonage Voice   │
│   (Threading)   │────│   (Webhooks)     │────│      API        │
└─────────────────┘    └──────────────────┘    └─────────────────┘
         │                       │                       │
         │              ┌─────────────────┐              │
         └──────────────│  Download Queue │──────────────┘
                        │   (Threading)   │
                        └─────────────────┘
```

## Prerequisites

- **Python 3.12+** (recommended for optimal performance)
- **Vonage Developer Account** with Voice API enabled
- **ngrok** or similar tunneling service for webhook URLs
- **Virtual Environment** (recommended)

## Installation

### 1. Clone and Setup Environment

```bash
git clone <repository-url>
cd vonage-voice-app
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

**requirements.txt:**
```
vonage~=4.0
fastapi>=0.104.0
uvicorn[standard]>=0.24.0
python-dotenv~=1.0
```

### 3. Environment Configuration

Create a `.env` file in the project root:

```env
# Vonage Application Credentials
VONAGE_APPLICATION_ID=your_application_id_here
VONAGE_APPLICATION_PRIVATE_KEY_PATH=/path/to/your/private.key
VONAGE_NUMBER=your_vonage_phone_number

# Webhook Configuration  
WEBHOOK_BASE_URL=https://your-ngrok-url.ngrok-free.app

# Test Configuration - must be valid e164 formatted numbers
TEST_LOOP=[12145551212,15551234567]
```

### 4. Vonage Application Setup

1. **Create Vonage Application:**
   ```bash
   # Using Vonage CLI
   vonage apps:create "Voice Demo App" \
     --voice_answer_url=https://your-domain.com/webhooks/answer \
     --voice_event_url=https://your-domain.com/webhooks/events
   ```

2. **Configure Webhooks:**
   - **Answer URL:** `https://your-domain.com/event`
   - **Event URL:** `https://your-domain.com/event` 
   - **Recording URL:** `https://your-domain.com/recording`

3. **Purchase/Rent Phone Number** and link to your application

## Configuration Options

### Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `VONAGE_APPLICATION_ID` | Your Vonage application UUID | `abc12345-def6-7890-ghij-klmnopqrstuv` |
| `VONAGE_APPLICATION_PRIVATE_KEY_PATH` | Path to private key file | `/path/to/private.key` |
| `VONAGE_NUMBER` | Your Vonage phone number | `12014293841` |
| `WEBHOOK_BASE_URL` | Base URL for webhooks | `https://abc123.ngrok-free.app` |
| `TEST_LOOP` | List of test phone numbers | `[12145551212,15551234567]` must be in e164 format

### Advanced Machine Detection Settings

```python
advanced_machine_detection={
    'behavior': 'continue',    # Continue call after detection
    'mode': 'default',         # mode provides the highest level of control to the call. This mode works asynchronously, which means Vonage starts processing NCCO actions during the detection phase
    'beep_timeout': 45         # Seconds to wait for voicemail beep
}
```

### Speech Recognition Configuration

```python
speech_config = {
    'language': 'en-US',       # Language model
    'context': ['1', '2', '7'], # Expected responses
    'startTimeout': 10,        # Seconds before starting recognition
    'maxDuration': 5,          # Maximum speech duration
    'endOnSilence': 1.5       # Silence threshold to end recognition
}
```

## Usage

### Development Mode

```bash
# Start with ngrok for webhook testing
ngrok http 5003

# Update WEBHOOK_BASE_URL in .env with ngrok URL
# Run the application
python outbound_with_amd_asr_dtmf.py
```

### Production Mode

```bash
# Use a production ASGI server
uvicorn outbound_with_amd_asr_dtmf:app --host 0.0.0.0 --port 5003 --workers 4
```

## API Endpoints

### Webhook Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/event` | POST | Call events and AMD results |
| `/dtmf_input` | POST | DTMF and speech input handling |
| `/recording` | POST | Recording completion notifications |
| `/asr` | POST | Speech recognition events (optional) |
| `/rtc_events` | POST | Real-time communication events (optional) |

### Call Flow Examples
```
1. Call initiated → with Advanced Machine Detection 
``` 

#### Call Screener navigation
```
1. Play call screener message if machine detected → for GoogleFi an iOS 26 phones with screening service
```

#### Human Detection Flow
```
1. Call initiated → Advanced Machine Detection
2. Human detected → IVR greeting played
3. User input (DTMF/Speech) → Response based on input
4. Call completion → Recording downloaded
```

#### Machine Detection Flow
```
1. Call initiated → with Advanced Machine Detection  
2. Machine detected
3. Check for voicemail beep
3a. Beep detected → Leave voicemail message
3b. No beep → Play screening message
4. Call completion → Recording downloaded
```

## Data Storage

The application creates several directories for data organization:

```
project/
├── recordings/           # Downloaded call recordings
├── webhooks/            # Event and input logs  
├── asr/                 # Speech recognition logs
└── rtc_events/          # Real-time communication logs
```

## Logging and Debugging

### Event Logging
All webhook events are automatically logged to JSON files organized by conversation UUID:

```
webhooks/event_CON-uuid.json
webhooks/dtmf_input_CON-uuid.json  
webhooks/speech_CON-uuid.json
```

### Console Output
The application provides detailed console logging for:
- Call initiation and status
- AMD detection results
- Input processing (DTMF/Speech)
- Recording download status
- Error handling and retries

### Debug Mode
Enable verbose logging by modifying the logging configuration:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## Error Handling

### Automatic Retries
- **Call Failures:** Exponential backoff retry (max 5 attempts)
- **Recording Downloads:** Exponential backoff retry (max 5 attempts)  
- **Failed Downloads:** Secondary retry queue processing

### Error Types Handled
- Authentication errors
- Network timeouts
- Invalid responses
- File system errors
- Webhook processing errors

## Customization

### Modifying IVR Options

Edit the `handle_input()` function in the DTMF webhook:

```python
def handle_input(input_value):
    if input_value == "1":
        # Confirmation response
        return [{"action": "talk", "text": "Custom confirmation message"}]
    elif input_value == "2":  
        # Reschedule response
        return [{"action": "talk", "text": "Custom reschedule message"}]
    # Add more options as needed
```

### Custom AMD Messages

Modify the machine detection responses in the event webhook:

```python
if sub_state == 'beep_start':
    ncco = [{
        'action': 'talk',
        'text': '<speak>Your custom voicemail message here</speak>',
        # ... other properties
    }]
```

## Performance Considerations

### Scalability
- **Threading:** Background download processing prevents blocking
- **Queue Management:** Asynchronous recording downloads
- **Memory Usage:** Efficient file handling and cleanup

### Rate Limiting
- Random delays between calls (70-90 seconds) prevent fraud detection
- Exponential backoff for API retries
- Configurable batch sizes for large call volumes

## Security Best Practices

### Credential Management
- Store credentials in `.env` files (never in code)
- Use application-based authentication (more secure than API key/secret)
-
