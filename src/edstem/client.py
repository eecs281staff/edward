from __future__ import annotations

import json
from typing import Any, TypeVar
from urllib.error import HTTPError as UrllibHTTPError
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from edstem.models import (
    AuthenticationMode,
    CourseUser,
    CreateThreadResponse,
    Error,
    HttpError,
    NetworkError,
    NewThread,
    ParseError,
    Result,
    ThreadDetailResponse,
    ThreadListFilter,
    ThreadListResponse,
    ThreadSort,
    decode,
    encode,
)

T = TypeVar("T")


def request_token(
    email: str,
    password: str,
    *,
    base_url: str = "https://us.edstem.org/api",
    timeout: float = 30.0,
) -> Result[str, Error]:
    """Exchange an account email and password for a session token.

    Parameters
    ----------
    email : str
    password : str
    base_url : str, optional
    timeout : float, optional

    Returns
    -------
    Result[str, Error]
    """
    request = Request(
        f"{base_url.rstrip('/')}/token",
        data=json.dumps({"login": email, "password": password}).encode(),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": "https://edstem.org",
            "User-Agent": "edstem-python/1.0",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            body_bytes = response.read()
    except UrllibHTTPError as exception:
        body_bytes = exception.read() if exception.fp is not None else b""
        return Result.Err(HttpError(exception.code, body_bytes.decode("utf-8", errors="replace")))
    except URLError as exception:
        return Result.Err(NetworkError(str(exception.reason)))
    except TimeoutError as exception:
        return Result.Err(NetworkError(str(exception)))

    text = body_bytes.decode("utf-8", errors="replace")

    if status != 200:
        return Result.Err(HttpError(status, text))

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exception:
        return Result.Err(ParseError(str(exception), body=text))

    if type(payload) is not dict or type(payload.get("token")) is not str:
        return Result.Err(ParseError("missing 'token' field", body=text))

    return Result.Ok(payload["token"])


class Client:
    """Unofficial EdStem HTTP API client.

    Methods return ``Result[T, Error]``: ``Ok`` or ``Err`` (HttpError, NetworkError, ParseError).
    """

    def __init__(
        self,
        token: str,
        *,
        base_url: str = "https://us.edstem.org/api",
        authentication: AuthenticationMode = "x-token",
        timeout: float = 30.0,
    ) -> None:
        """
        Parameters
        ----------
        token : str
            Existing session JWT or API token.
        base_url : str, optional
        authentication : {``x-token``, ``bearer``}, optional
        timeout : float, optional
        """
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.authentication = authentication
        self.timeout = timeout

    def get_course_users(self, course_id: int) -> Result[list[CourseUser], Error]:
        """Load the course roster.

        Parameters
        ----------
        course_id : int

        Returns
        -------
        Result[list[CourseUser], Error]
        """
        return self._request_json_field("GET", f"/courses/{course_id}/analytics/users", list[CourseUser], "users")

    def list_course_threads(
        self,
        course_id: int,
        *,
        limit: int | None = None,
        offset: int | str | None = None,
        sort: ThreadSort | None = None,
        category: str | None = None,
        subcategory: str | None = None,
        filter: ThreadListFilter | None = None,
    ) -> Result[ThreadListResponse, Error]:
        """List threads in a course.

        Parameters
        ----------
        course_id : int
        limit : int, optional
        offset : int or str, optional
        sort : ThreadSort, optional
        category : str, optional
        subcategory : str, optional
        filter : ThreadListFilter, optional

        Returns
        -------
        Result[ThreadListResponse, Error]
        """
        return self._request_json(
            "GET",
            f"/courses/{course_id}/threads",
            ThreadListResponse,
            query={
                "limit": limit,
                "offset": offset,
                "sort": sort,
                "category": category,
                "subcategory": subcategory,
                "filter": filter,
            },
        )

    def create_thread(self, course_id: int, thread: NewThread) -> Result[CreateThreadResponse, Error]:
        """Create a thread.

        Parameters
        ----------
        course_id : int
        thread : NewThread

        Returns
        -------
        Result[CreateThreadResponse, Error]
        """
        return self._request_json(
            "POST",
            f"/courses/{course_id}/threads",
            CreateThreadResponse,
            payload={"thread": encode(thread)},
        )

    def get_thread(self, thread_id: int, *, view: bool = False) -> Result[ThreadDetailResponse, Error]:
        """Load a thread by global id.

        Parameters
        ----------
        thread_id : int
        view : bool, optional
            If True, mark the thread as viewed.

        Returns
        -------
        Result[ThreadDetailResponse, Error]
        """
        return self._request_json(
            "GET",
            f"/threads/{thread_id}",
            ThreadDetailResponse,
            query={"view": 1} if view else None,
        )

    def _request_headers(self, extra_headers: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": "edstem-python/1.0"}
        if self.authentication == "bearer":
            headers["Authorization"] = f"Bearer {self.token}"
        else:
            headers["X-Token"] = self.token
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def _build_url(self, path: str, query: dict[str, Any] | None) -> str:
        url = f"{self.base_url}{path}"
        if query:
            query_parameters = {key: str(value) for key, value in query.items() if value is not None}
            if query_parameters:
                url = f"{url}?{urlencode(query_parameters)}"
        return url

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        payload: Any = None,
    ) -> Result[Any, Error]:
        if payload is not None:
            body = json.dumps(payload).encode()
            headers = self._request_headers({"Content-Type": "application/json"})
        else:
            body = None
            headers = self._request_headers()

        request = Request(
            self._build_url(path, query),
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                status = getattr(response, "status", 200)
                body_bytes = response.read()
        except UrllibHTTPError as exception:
            body_bytes = exception.read() if exception.fp is not None else b""
            text = body_bytes.decode("utf-8", errors="replace")
            return Result.Err(HttpError(exception.code, text))
        except URLError as exception:
            return Result.Err(NetworkError(str(exception.reason)))
        except TimeoutError as exception:
            return Result.Err(NetworkError(str(exception)))

        if not body_bytes:
            if 200 <= status < 300:
                return Result.Ok(None)
            return Result.Err(HttpError(status))

        text = body_bytes.decode("utf-8", errors="replace")
        if not (200 <= status < 300):
            return Result.Err(HttpError(status, text))
        try:
            return Result.Ok(json.loads(text))
        except json.JSONDecodeError as exception:
            return Result.Err(ParseError(str(exception), body=text))

    def _decode_value(self, data_type: type[T] | Any, data: Any) -> Result[T, Error]:
        try:
            return Result.Ok(decode(data_type, data))
        except Exception as exception:
            return Result.Err(ParseError(str(exception), body=str(data)))

    def _request_json(
        self,
        method: str,
        path: str,
        data_type: type[T] | Any,
        *,
        query: dict[str, Any] | None = None,
        payload: Any = None,
    ) -> Result[T, Error]:
        result = self._request(method, path, query=query, payload=payload)
        if result.is_err():
            return result
        return self._decode_value(data_type, result.unwrap())

    def _request_json_field(
        self,
        method: str,
        path: str,
        data_type: type[T] | Any,
        key: str,
        *,
        query: dict[str, Any] | None = None,
    ) -> Result[T, Error]:
        result = self._request(method, path, query=query)
        if result.is_err():
            return result
        data = result.unwrap()
        if type(data) is not dict or key not in data:
            return Result.Err(ParseError(f"missing {key!r} field", body=str(data)))
        return self._decode_value(data_type, data[key])
