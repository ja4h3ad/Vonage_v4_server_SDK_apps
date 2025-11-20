# ./fastap_requests/messages.py

from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
# inbound message model
class InboundMessage(BaseModel):
    channel: str
    message_uuid: str
    to: str
    from_: str = Field(alias="from")  # “from” maps cleanly to “from_”
    timestamp: str
    text: str
    sms: Optional[Dict[str, str]] = None
    usage: Optional[Dict[str, str]] = None
    origin: Optional[Dict[str, str]] = None

    model_config = {
        "populate_by_name": True  # allows population via either alias or field name
    }





