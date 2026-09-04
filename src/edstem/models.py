from __future__ import annotations

import dataclasses
from dataclasses import dataclass, fields, is_dataclass
from types import NoneType, UnionType
from typing import (
    Any,
    Generic,
    Literal,
    TypeAlias,
    TypeVar,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)

T = TypeVar("T")
E = TypeVar("E")

ThreadType = Literal["post", "question", "announcement"]
CommentType = Literal["comment", "answer"]
ThreadSort = Literal["new", "top", "week", "relevance"]
ThreadListFilter = Literal["unanswered", "unresolved", "unread", "answered"]
AuthenticationMode = Literal["x-token", "bearer"]


@dataclass(frozen=True)
class HttpError:
    """HTTP 4xx/5xx from the server.

    Attributes
    ----------
    status : int
    body : str
    tag : {``http``}
    """
    status: int
    body: str = ""
    tag: Literal["http"] = "http"


@dataclass(frozen=True)
class NetworkError:
    """Transport failure talking to the server.

    Attributes
    ----------
    message : str
    tag : {``network``}
    """
    message: str
    tag: Literal["network"] = "network"


@dataclass(frozen=True)
class ParseError:
    """Response was not valid JSON or did not match the expected shape.

    Attributes
    ----------
    message : str
    body : str or None
    tag : {``parse``}
    """
    message: str
    body: str | None = None
    tag: Literal["parse"] = "parse"


Error: TypeAlias = HttpError | NetworkError | ParseError


@dataclass(frozen=True)
class Result(Generic[T, E]):
    """Success or error container (``Ok`` / ``Err``).

    Attributes
    ----------
    tag : {``ok``, ``err``}
    value : T or None
    error : E or None
    """
    tag: Literal["ok", "err"]
    value: T | None = None
    error: E | None = None

    @staticmethod
    def Ok(value: T) -> Result[T, Any]:
        """Wrap a success value.

        Parameters
        ----------
        value : T

        Returns
        -------
        Result[T, Any]
        """
        return Result(tag="ok", value=value)

    @staticmethod
    def Err(error: E) -> Result[Any, E]:
        """Wrap an error value.

        Parameters
        ----------
        error : E

        Returns
        -------
        Result[Any, E]
        """
        return Result(tag="err", error=error)

    def is_ok(self) -> bool:
        """Whether this is ``Ok``.

        Returns
        -------
        bool
        """
        return self.tag == "ok"

    def is_err(self) -> bool:
        """Whether this is ``Err``.

        Returns
        -------
        bool
        """
        return self.tag == "err"

    def unwrap(self) -> T:
        """Return the success value.

        Returns
        -------
        T

        Raises
        ------
        ValueError
            If this is ``Err``.
        """
        if self.tag != "ok":
            raise ValueError(f"called unwrap on Err: {self.error}")
        return cast(T, self.value)

    def unwrap_err(self) -> E:
        """Return the error value.

        Returns
        -------
        E

        Raises
        ------
        ValueError
            If this is ``Ok``.
        """
        if self.tag != "err":
            raise ValueError("called unwrap_err on Ok")
        return cast(E, self.error)


@dataclass
class UserReference:
    id: int
    name: str
    role: str
    course_role: str
    avatar: str | None


@dataclass
class CourseUser:
    user_id: int
    name: str
    course_role: str
    avatar: str | None
    tutorial: int | None
    email: str


@dataclass
class ThreadSummary:
    id: int
    number: int
    course_id: int
    user_id: int
    type: ThreadType
    title: str
    category: str
    subcategory: str
    is_locked: bool
    is_pinned: bool
    is_private: bool
    is_endorsed: bool
    is_answered: bool
    is_starred: bool
    reply_count: int
    vote: int
    created_at: str


@dataclass
class Comment:
    id: int
    type: CommentType
    thread_id: int
    user_id: int
    parent_id: int | None
    content: str
    document: str
    vote: int
    is_endorsed: bool
    is_private: bool
    created_at: str
    comments: list[Comment]


@dataclass
class Thread(ThreadSummary):
    content: str
    document: str
    anonymous_comments: bool
    is_anonymous: bool
    is_megathread: bool
    accepted_id: int | None
    comments: list[Comment] = dataclasses.field(default_factory=list)
    answers: list[Comment] = dataclasses.field(default_factory=list)


@dataclass
class ThreadListResponse:
    sort_key: str
    threads: list[ThreadSummary]
    users: list[UserReference]


@dataclass
class ThreadDetailResponse:
    thread: Thread
    users: list[UserReference]


@dataclass
class NewThread:
    title: str
    category: str
    content: str
    type: ThreadType = "post"
    subcategory: str = ""
    subsubcategory: str = ""
    is_pinned: bool = False
    is_private: bool = False
    is_anonymous: bool = False
    is_megathread: bool = False
    anonymous_comments: bool = False


@dataclass
class CreateThreadResponse:
    status: str
    thread: Thread
    bot: dict[str, Any]


def decode(data_type: Any, data: Any) -> Any:
    """Map JSON-like data onto ``data_type``.

    Parameters
    ----------
    data_type : type
    data : Any

    Returns
    -------
    Any

    Raises
    ------
    TypeError
        If a required field is missing or a value has the wrong shape.
    """
    if data_type is Any:
        return data
    origin = get_origin(data_type)
    if origin is Union or origin is UnionType:
        arguments = get_args(data_type)
        if NoneType in arguments and data is None:
            return None
        concrete_types = [argument for argument in arguments if argument is not NoneType]
        if data is None:
            raise TypeError(f"expected {data_type}, got None")
        if len(concrete_types) == 1:
            return decode(concrete_types[0], data)
        return data
    if origin is list:
        if type(data) is not list:
            raise TypeError(f"expected list, got {type(data).__name__}")
        item_type = get_args(data_type)[0]
        return [decode(item_type, item) for item in data]
    if origin is dict:
        if type(data) is not dict:
            raise TypeError(f"expected object, got {type(data).__name__}")
        return data
    if origin is Literal:
        if data is None:
            raise TypeError("expected literal, got None")
        return data
    if is_dataclass(data_type) and type(data_type) is type:
        if type(data) is not dict:
            raise TypeError(f"expected object for {data_type.__name__}, got {type(data).__name__}")
        type_hints = get_type_hints(data_type)
        field_values: dict[str, Any] = {}
        for field in fields(data_type):
            if field.name not in data:
                if (
                    field.default is not dataclasses.MISSING
                    or field.default_factory is not dataclasses.MISSING
                ):
                    continue
                raise TypeError(f"missing field {data_type.__name__}.{field.name}")
            field_values[field.name] = decode(type_hints.get(field.name, Any), data[field.name])
        return data_type(**field_values)
    if data is None:
        raise TypeError(f"expected {getattr(data_type, '__name__', data_type)}, got None")
    return data


def encode(value: Any) -> Any:
    """Map a dataclass or nested value to JSON-like data, omitting ``None``.

    Parameters
    ----------
    value : Any

    Returns
    -------
    Any
    """
    if is_dataclass(value):
        encoded: dict[str, Any] = {}
        for field in fields(value):
            field_value = getattr(value, field.name)
            if field_value is not None:
                encoded[field.name] = encode(field_value)
        return encoded
    if type(value) is list:
        return [encode(item) for item in value]
    if type(value) is dict:
        return {key: encode(item) for key, item in value.items() if item is not None}
    return value
