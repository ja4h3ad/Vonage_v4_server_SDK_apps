def make_call(to_number, max_retries=5, initial_delay=1, branding_delay_ms=300):
    """
    Initiate branded outbound call with configurable delay between branding and call
    
    Args:
        to_number: Destination phone number
        max_retries: Maximum retry attempts for call creation
        initial_delay: Initial delay between retries in seconds
        branding_delay_ms: Milliseconds to wait after branding before initiating call (default 300ms)
    
    Returns:
        Call UUID if successful, None otherwise
    """
    # Start a new call tracking flow
    correlation_id = call_tracker.start_auth_flow(to_number)
    
    # First Orion branded calling step - get auth token and send push notification
    branding_success = False
    token, auth_data = get_auth_token(correlation_id)
    
    if token:
        logger.info(f"Successfully obtained First Orion auth token")
        
        # Send push notification with the token
        success, push_data = send_push_notification(correlation_id, token, VONAGE_NUMBER, to_number)
        
        if success:
            logger.info(f"Successfully sent First Orion push notification for {to_number}")
            branding_success = True
            
            # CRITICAL: Wait for branding data to propagate to handset
            # This ensures the logo/caller name are delivered before call arrives
            branding_delay_seconds = branding_delay_ms / 1000.0
            logger.info(f"Waiting {branding_delay_ms}ms for branding to propagate to handset...")
            time.sleep(branding_delay_seconds)
            logger.info(f"Branding delay complete, initiating call now")
        else:
            logger.warning(f"Failed to send First Orion push notification for {to_number}. Call will proceed unbranded.")
    else:
        logger.warning(f"Failed to get First Orion auth token. Call will proceed unbranded.")
    
    # Proceed with the call (after branding delay if branding was successful)
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
                    'beep_timeout': 90  # Wait 90 seconds for voicemail beep
                },
                event_url=[get_webhook_url('event')],
                event_method='POST'
            )
            
            # Execute call
            response = vonage.voice.create_call(call_request)
            
            logger.info(f"Call created successfully to {to_number}: {response.uuid}")
            logger.info(f"Branding status: {'branded' if branding_success else 'unbranded'}")
            pprint(response.model_dump())
            
            # Record the Vonage call creation in our tracker
            call_tracker.record_vonage_call(correlation_id, response)
            
            return response.uuid
            
        except (AuthenticationError, HttpRequestError) as e:
            logger.error(f'Error when calling {to_number}: {str(e)}')
            if attempt < max_retries - 1:
                delay = initial_delay * (2 ** attempt)
                logger.warning(f'Retrying call in {delay} seconds...')
                time.sleep(delay)
            else:
                logger.error(f'Failed to create call for {to_number} after {max_retries} attempts.')
                return None
    
    return None


# If you want to make the delay configurable via environment variable:
# Add this at the top with your other environment variables:
# BRANDING_DELAY_MS = int(os.environ.get("BRANDING_DELAY_MS", "300"))
#
# Then call it like:
# make_call(to_number, branding_delay_ms=BRANDING_DELAY_MS)


# Example usage with different delays for testing:
# make_call("+15551234567", branding_delay_ms=300)  # 300ms delay (default)
# make_call("+15551234567", branding_delay_ms=500)  # 500ms delay for slower networks
# make_call("+15551234567", branding_delay_ms=100)  # 100ms delay for faster networks