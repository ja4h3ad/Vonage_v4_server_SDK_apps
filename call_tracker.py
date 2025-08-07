"""
Call Tracker Module

This module provides correlation tracking between First Orion API calls and Vonage Voice API calls.
It maintains associations between auth tokens, request IDs, and call UUIDs.
"""

import os
import json
import time
import logging
from typing import Dict, Any, Optional
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('call_tracker')


class CallTracker:
    def __init__(self, log_dir='call_logs'):
        """
        Initialize the call tracker

        Args:
            log_dir: Directory to store call logs
        """
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self.active_calls = {}

    def start_auth_flow(self, to_number: str) -> str:
        """
        Start tracking a new call flow, beginning with authentication

        Args:
            to_number: Destination phone number

        Returns:
            correlation_id: Unique ID to track this call flow
        """
        # Create a unique correlation ID for this call attempt
        correlation_id = f"call_{int(time.time())}_{to_number}"

        # Initialize call data structure
        self.active_calls[correlation_id] = {
            "correlation_id": correlation_id,
            "to_number": to_number,
            "timestamp_started": datetime.now().isoformat(),
            "first_orion": {
                "auth": None,
                "push": None
            },
            "vonage": {
                "call_uuid": None,
                "conversation_uuid": None,
                "events": []
            },
            "status": "initializing"
        }

        # Write initial log entry
        self._write_log(correlation_id)

        return correlation_id

    def record_auth_response(self, correlation_id: str, response_data: Dict[str, Any],
                             request_id: Optional[str] = None):
        """
        Record First Orion authentication response

        Args:
            correlation_id: Correlation ID from start_auth_flow
            response_data: Response data from First Orion auth API
            request_id: Request ID from First Orion headers if available
        """
        if correlation_id not in self.active_calls:
            logger.warning(f"Correlation ID {correlation_id} not found")
            return

        # Store auth data
        self.active_calls[correlation_id]["first_orion"]["auth"] = {
            "timestamp": datetime.now().isoformat(),
            "request_id": request_id,
            "success": bool(response_data and "token" in response_data),
            "token_expires_in": response_data.get("expires_in") if response_data else None
        }

        self._write_log(correlation_id)

    def record_push_response(self, correlation_id: str, success: bool, response_data: Optional[Dict[str, Any]] = None,
                             request_id: Optional[str] = None):
        """
        Record First Orion push notification response

        Args:
            correlation_id: Correlation ID from start_auth_flow
            success: Whether the push was successful
            response_data: Response data from First Orion push API
            request_id: Request ID from First Orion headers if available
        """
        if correlation_id not in self.active_calls:
            logger.warning(f"Correlation ID {correlation_id} not found")
            return

        # Store push data
        self.active_calls[correlation_id]["first_orion"]["push"] = {
            "timestamp": datetime.now().isoformat(),
            "request_id": request_id,
            "success": success,
            "response": response_data
        }

        self._write_log(correlation_id)

    def record_vonage_call(self, correlation_id, response):
        """Record Vonage call creation response"""
        if correlation_id not in self.active_calls:
            logger.warning(f"Correlation ID {correlation_id} not found in active calls")
            return

        # Handle both dict and Pydantic model responses
        if hasattr(response, 'model_dump'):
            # It's a Pydantic model
            response_data = response.model_dump()
        else:
            # It's already a dict
            response_data = response

        vonage_data = {
            "call_uuid": response_data.get("uuid"),
            "conversation_uuid": response_data.get("conversation_uuid"),
            "status": response_data.get("status"),
            "direction": response_data.get("direction"),
            "created_at": datetime.now().isoformat(),
            "events": []  # Add this line
        }

        self.active_calls[correlation_id]["vonage"] = vonage_data
        logger.info(f"Recorded Vonage call data for correlation {correlation_id}")
    def record_vonage_event(self, conversation_uuid: str, event_data: Dict[str, Any]):
        """
        Record a Vonage event webhook

        Args:
            conversation_uuid: Conversation UUID from Vonage
            event_data: Event data from Vonage webhook
        """
        # Find the correlation ID by conversation UUID
        correlation_id = None
        for cid, call_data in self.active_calls.items():
            if call_data["vonage"].get("conversation_uuid") == conversation_uuid:
                correlation_id = cid
                break

        if not correlation_id:
            logger.warning(f"No correlation found for conversation UUID {conversation_uuid}")
            return

        # Store event data
        self.active_calls[correlation_id]["vonage"]["events"].append({
            "timestamp": datetime.now().isoformat(),
            "type": event_data.get("status", "unknown"),
            "data": event_data
        })

        # Update call status if applicable
        if "status" in event_data:
            self.active_calls[correlation_id]["status"] = event_data["status"]

        self._write_log(correlation_id)

    def _write_log(self, correlation_id: str):
        """
        Write the current state of a call to its log file

        Args:
            correlation_id: Correlation ID of the call to log
        """
        if correlation_id not in self.active_calls:
            return

        call_data = self.active_calls[correlation_id]
        to_number = call_data["to_number"]

        # Create a sanitized version of the log (e.g., remove tokens)
        sanitized_data = self._sanitize_for_logging(call_data)

        # Write to the call-specific log file
        log_path = os.path.join(self.log_dir, f"{correlation_id}.json")
        with open(log_path, 'w', encoding='utf-8') as f:
            json.dump(sanitized_data, f, indent=2)

        # Also update the latest log for this number
        number_log_path = os.path.join(self.log_dir, f"number_{to_number}_latest.json")
        with open(number_log_path, 'w', encoding='utf-8') as f:
            json.dump(sanitized_data, f, indent=2)

    def _sanitize_for_logging(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a sanitized copy of call data for logging

        Args:
            data: Call data dictionary

        Returns:
            Sanitized copy of the data
        """
        import copy

        # Create a deep copy so as to not modify the original
        sanitized = copy.deepcopy(data)

        # Remove sensitive information
        auth_data = sanitized.get("first_orion", {}).get("auth", {})
        if auth_data and "token" in auth_data:
            # Keep only the first 10 chars of the token
            if isinstance(auth_data["token"], str) and len(auth_data["token"]) > 10:
                auth_data["token"] = auth_data["token"][:10] + "..."

        return sanitized



call_tracker = CallTracker()