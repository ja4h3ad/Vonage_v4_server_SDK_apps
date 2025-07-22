"""
First Orion Branded Calling API Module

This module provides functions to interact with First Orion's Branded Calling API.
It handles authentication and push notifications needed for outbound calling applications.
"""

import requests
import json
from typing import Dict, Any, Optional, Tuple, TypedDict
import time
import logging
import os
from dotenv import load_dotenv
from call_tracker import call_tracker

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('first_orion_api')

# Suppress SSL warnings when verify=False is used
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class AuthResponse(TypedDict):
    """Type definition for First Orion auth response"""
    token: str
    refresh_token: str
    expires_in: int
    token_type: str
    expires_at: int

# API Constants
AUTH_URL = "https://api.firstorion.com/v1/auth"
PUSH_URL = "https://api.firstorion.com/exchange/v1/calls/push"
# Keys should come from environment variables
API_KEY = os.environ.get("FIRST_ORION_API_KEY")
API_SECRET = os.environ.get("FIRST_ORION_API_PASSWORD")

def get_auth_token(correlation_id: str) -> Tuple[Optional[str], Optional[AuthResponse]]:
    """
    Get authentication token from First Orion API.

    Args:
        correlation_id: ID to correlate this auth request with subsequent calls

    Returns:
        Tuple[Optional[str], Optional[Dict[str, Any]]]:
            - Token string if successful, None if failed
            - Full response data as dictionary containing:
              - token: The JWT token for API authorization
              - refresh_token: Token used to obtain new access tokens
              - expires_in: Token validity in seconds
              - token_type: Usually "Bearer"
              - expires_at: Unix timestamp when token expires
    """
    # Debug output for credentials
    masked_key = f"{API_KEY[:8]}...{API_KEY[-5:]}" if API_KEY and len(API_KEY) > 13 else "Not set"
    masked_secret = "Value set (masked)" if API_SECRET else "Not set"
    print(f"API Key: {masked_key}")
    print(f"Secret Key: {masked_secret}")

    headers = {
        'X-API-KEY': API_KEY,
        'X-SECRET-KEY': API_SECRET,
        'X-SERVICE': 'auth',
        'accept': 'application/json',
        'content-type': 'application/json'
    }

    try:
        print(f"Making POST request to: {AUTH_URL}")
        # Temporarily disable SSL verification for development environment
        response = requests.post(AUTH_URL, headers=headers, data={}, verify=False)

        print(f"Response Status Code: {response.status_code}")
        print(f"Response Headers: {dict(response.headers)}")

        # Extract request ID from headers for correlation
        request_id = response.headers.get('X-Forp-Meta-Request-Id')

        # Try to print the response body
        try:
            print(f"Response Body: {response.text[:200]}...")  # Print first 200 chars
        except:
            print("Could not print response body")

        response.raise_for_status()

        response_data = response.json()
        token = response_data.get('token')

        # Record the auth response in the call tracker
        call_tracker.record_auth_response(correlation_id, response_data, request_id)

        if token:
            print(f"Token received (first 20 chars): {token[:20]}...")
            logger.info(f"Successfully obtained authentication token, expires in {response_data.get('expires_in')} seconds")
            # Check if token will expire soon (less than 5 minutes remaining)
            expires_at = response_data.get('expires_at', 0)
            current_time = int(time.time())
            if expires_at - current_time < 300:  # Less than 5 minutes remaining
                logger.warning(f"Token will expire soon (in {expires_at - current_time} seconds)")

            return token, response_data
        else:
            logger.error("Authentication succeeded but no token in response")
            return None, response_data

    except requests.exceptions.RequestException as e:
        logger.error(f"Authentication request failed: {str(e)}")
        # Record the failed auth in the call tracker
        call_tracker.record_auth_response(correlation_id, None)
        return None, None

    except json.JSONDecodeError:
        logger.error("Failed to parse authentication response JSON")
        # Record the failed auth in the call tracker
        call_tracker.record_auth_response(correlation_id, None)
        return None, None


def send_push_notification(correlation_id: str, token: str, a_number: str, b_number: str) -> Tuple[
    bool, Optional[Dict[str, Any]]]:
    """
    Send a push notification to First Orion before making a call.

    Args:
        correlation_id: ID to correlate this push request with auth and subsequent calls
        token (str): Authentication token from get_auth_token()
        a_number (str): The originating phone number (your Vonage Voice API LVN)
        b_number (str): The destination phone number

    Returns:
        Tuple[bool, Optional[Dict[str, Any]]]:
            - Success status (True/False)
            - Response data as dictionary if available
    """

    # Ensure phone numbers have the '+' prefix required by First Orion
    def ensure_plus_prefix(number):
        # Convert to string if it's an integer or other type
        number_str = str(number)
        if not number_str.startswith('+'):
            return '+' + number_str
        return number_str

    formatted_a_number = ensure_plus_prefix(a_number)
    formatted_b_number = ensure_plus_prefix(b_number)

    headers = {
        'accept': 'application/json',
        'authorization': f'Bearer {token}',
        'content-type': 'application/json'
    }

    payload = {
        "aNumber": formatted_a_number,
        "bNumber": formatted_b_number
    }

    try:
        print(f"Sending push notification: {formatted_a_number} → {formatted_b_number}")
        print(f"Push URL: {PUSH_URL}")
        print(f"Request payload: {json.dumps(payload)}")

        response = requests.post(PUSH_URL, headers=headers, json=payload, verify=False)

        # Extract request ID from headers for correlation if available
        request_id = response.headers.get('X-Forp-Meta-Request-Id')

        response.raise_for_status()

        response_data = response.json() if response.content else {}
        logger.info(f"Successfully sent push notification: {formatted_a_number} → {formatted_b_number}")

        # Record the successful push in the call tracker
        call_tracker.record_push_response(correlation_id, True, response_data, request_id)

        return True, response_data

    except requests.exceptions.RequestException as e:
        logger.error(f"Push notification request failed: {str(e)}")

        # Try to get request ID from headers even if request failed
        request_id = None
        if hasattr(e, 'response') and e.response is not None:
            request_id = e.response.headers.get('X-Forp-Meta-Request-Id')

            try:
                error_detail = e.response.json()
                logger.error(f"Error details: {error_detail}")
            except:
                logger.error(f"Status code: {e.response.status_code}")

        # Record the failed push in the call tracker
        call_tracker.record_push_response(correlation_id, False, None, request_id)

        return False, None
def first_orion_flow(a_number: str, b_number: str) -> Tuple[bool, str]:
    """
    Complete First Orion flow: authenticate and send push notification.

    Args:
        a_number (str): The originating phone number (your Vonage Voice API LVN)
        b_number (str): The destination phone number

    Returns:
        Tuple[bool, str]:
            - Success status (True/False)
            - Correlation ID for tracking
    """
    # Start tracking this call flow
    correlation_id = call_tracker.start_auth_flow(b_number)

    # Step 1: Get authentication token
    token, auth_data = get_auth_token(correlation_id)
    if not token:
        logger.error("Cannot complete First Orion flow: authentication failed")
        return False, correlation_id

    # Step 2: Send push notification
    success, push_data = send_push_notification(correlation_id, token, a_number, b_number)
    if not success:
        logger.error("Cannot complete First Orion flow: push notification failed")
        return False, correlation_id

    logger.info("First Orion flow completed successfully")
    return True, correlation_id