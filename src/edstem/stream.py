from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlencode

from edstem.models import Error, NetworkError, ParseError, Result, ThreadType

ClientEvent = Literal["course.subscribe"]


@dataclass
class StreamServerMessage:
    """Server WebSocket frame.

    Attributes
    ----------
    type : str
    data : dict
    """

    type: str
    data: dict[str, Any]


@dataclass
class StreamThreadUser:
    """Author embedded in a streamed thread.

    Attributes
    ----------
    id : int
    role : str
    name : str
    avatar : str or None
    course_role : str
    tutorials : dict
    """

    id: int
    role: str
    name: str
    avatar: str | None
    course_role: str
    tutorials: dict[str, str]


@dataclass
class StreamThread:
    """Thread carried by a ``thread.new`` frame.

    Attributes
    ----------
    id : int
    user_id : int
    course_id : int
    original_id : int or None
    editor_id : int or None
    accepted_id : int or None
    duplicate_id : int or None
    number : int
    type : str
    title : str
    content : str
    document : str
    category : str
    subcategory : str
    subsubcategory : str
    flag_count : int
    star_count : int
    view_count : int
    unique_view_count : int
    vote_count : int
    reply_count : int
    unresolved_count : int
    is_locked : bool
    is_pinned : bool
    is_private : bool
    is_endorsed : bool
    is_answered : bool
    is_student_answered : bool
    is_staff_answered : bool
    is_archived : bool
    is_anonymous : bool
    is_megathread : bool
    anonymous_comments : bool
    approved_status : str
    created_at : str
    updated_at : str or None
    deleted_at : str or None
    pinned_at : str or None
    anonymous_id : int
    user : StreamThreadUser
    """

    id: int
    user_id: int
    course_id: int
    original_id: int | None
    editor_id: int | None
    accepted_id: int | None
    duplicate_id: int | None
    number: int
    type: ThreadType
    title: str
    content: str
    document: str
    category: str
    subcategory: str
    subsubcategory: str
    flag_count: int
    star_count: int
    view_count: int
    unique_view_count: int
    vote_count: int
    reply_count: int
    unresolved_count: int
    is_locked: bool
    is_pinned: bool
    is_private: bool
    is_endorsed: bool
    is_answered: bool
    is_student_answered: bool
    is_staff_answered: bool
    is_archived: bool
    is_anonymous: bool
    is_megathread: bool
    anonymous_comments: bool
    approved_status: str
    created_at: str
    updated_at: str | None
    deleted_at: str | None
    pinned_at: str | None
    anonymous_id: int
    user: StreamThreadUser


@dataclass
class StreamThreadNew:
    """``data`` payload of a ``thread.new`` frame.

    Attributes
    ----------
    thread : StreamThread
    """

    thread: StreamThread


class Stream:
    """Unofficial EdStem WebSocket stream.

    Methods return ``Result[T, Error]``: ``Ok`` or ``Err`` (HttpError, NetworkError, ParseError).
    """

    def __init__(self, token: str, *, url: str = "wss://us.edstem.org/api/stream") -> None:
        """
        Parameters
        ----------
        token : str
            Existing session JWT.
        url : str, optional
        """
        self.token = token
        self.url = url.rstrip("/")
        self._sequence = 0
        self._websocket: Any = None

    def connect(self) -> Result[None, Error]:
        """Open the stream, passing the token as ``_token``.

        Returns
        -------
        Result[None, Error]
        """
        try:
            from websockets.sync.client import connect
        except ImportError:
            return Result.Err(
                ParseError("websockets is required for Stream; pip install 'edstem[websockets]'")
            )
        try:
            self._websocket = connect(f"{self.url}?{urlencode({'_token': self.token})}")
        except Exception as exception:
            return Result.Err(NetworkError(str(exception)))
        return Result.Ok(None)

    def close(self) -> Result[None, Error]:
        """Close the stream.

        Returns
        -------
        Result[None, Error]
        """
        if self._websocket is None:
            return Result.Ok(None)
        try:
            self._websocket.close()
        except Exception as exception:
            return Result.Err(NetworkError(str(exception)))
        self._websocket = None
        return Result.Ok(None)

    def send(
        self,
        event_type: ClientEvent | str,
        *,
        object_id: int | None = None,
    ) -> Result[None, Error]:
        """Send a client event.

        Parameters
        ----------
        event_type : str
        object_id : int, optional
            Sent on the wire as ``oid``.

        Returns
        -------
        Result[None, Error]
        """
        if self._websocket is None:
            return Result.Err(NetworkError("stream is not connected"))
        self._sequence += 1
        message: dict[str, Any] = {"id": self._sequence, "type": event_type}
        if object_id is not None:
            message["oid"] = object_id
        try:
            self._websocket.send(json.dumps(message))
        except Exception as exception:
            return Result.Err(NetworkError(str(exception)))
        return Result.Ok(None)

    def receive(self) -> Result[StreamServerMessage, Error]:
        """Read the next server event.

        Returns
        -------
        Result[StreamServerMessage, Error]
        """
        if self._websocket is None:
            return Result.Err(NetworkError("stream is not connected"))
        try:
            frame = self._websocket.recv()
        except Exception as exception:
            return Result.Err(NetworkError(str(exception)))
        if type(frame) is bytes:
            frame = frame.decode("utf-8", errors="replace")
        if type(frame) is not str:
            return Result.Err(ParseError("expected text frame", body=str(frame)))
        try:
            message = json.loads(frame)
        except json.JSONDecodeError as exception:
            return Result.Err(ParseError(str(exception), body=frame))
        if type(message) is not dict or "type" not in message:
            return Result.Err(ParseError("missing type field", body=frame))
        event_type = message["type"]
        if type(event_type) is not str:
            return Result.Err(ParseError("type was not a string", body=frame))
        data = message.get("data", {})
        if data is None:
            data = {}
        if type(data) is not dict:
            return Result.Err(ParseError("data was not an object", body=frame))
        return Result.Ok(StreamServerMessage(type=event_type, data=data))

    def subscribe_course(self, course_id: int) -> Result[None, Error]:
        """Subscribe to live course updates.

        Parameters
        ----------
        course_id : int

        Returns
        -------
        Result[None, Error]
        """
        return self.send("course.subscribe", object_id=course_id)
