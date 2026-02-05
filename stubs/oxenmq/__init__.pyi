from __future__ import annotations

import typing_extensions

from enum import Enum
from typing import Callable, Any, List, Optional, Union
from datetime import datetime, timedelta

class ConnectionID:
    service_node: bool
    @property
    def pubkey(self) -> bytes: ...
    @typing_extensions.override
    def __eq__(self, other: object) -> bool: ...
    @typing_extensions.override
    def __ne__(self, other: object) -> bool: ...
    @typing_extensions.override
    def __hash__(self) -> int: ...

class Address:
    @typing_extensions.overload
    def __init__(self, addr: Union[str, bytes]) -> None: ...
    @typing_extensions.overload
    def __init__(self, addr: Union[str, bytes], pubkey: bytes) -> None: ...
    @typing_extensions.overload
    def __init__(self, host: str, port: int) -> None: ...
    @typing_extensions.overload
    def __init__(self, host: str, port: int, pubkey: bytes) -> None: ...
    @property
    def pubkey(self) -> Optional[bytes]: ...
    @pubkey.setter
    def pubkey(self, value: Optional[bytes]) -> None: ...
    @property
    def curve(self) -> bool: ...
    @property
    def tcp(self) -> bool: ...
    @property
    def ipc(self) -> bool: ...
    @property
    def zmq_address(self) -> str: ...
    @property
    def full_address(self) -> str: ...
    @property
    def full_address_b64(self) -> str: ...
    @property
    def full_address_hex(self) -> str: ...
    @property
    def qr(self) -> str: ...
    @typing_extensions.override
    def __eq__(self, other: object) -> bool: ...
    @typing_extensions.override
    def __ne__(self, other: object) -> bool: ...


class LogLevel(Enum):
    critical = 0  # or any sentinel value; the actual int values aren't exposed/used in Python typically
    error    = 1
    warn     = 2
    info     = 3
    debug    = 4
    trace    = 5

class TaggedThreadID: ...
class TimerID: ...

class AuthLevel(Enum):
    denied = "denied"
    none   = "none"
    basic  = "basic"
    admin  = "admin"

    @typing_extensions.override
    def __str__(self) -> str: ...

class Access:
    def __init__(self, auth: AuthLevel = AuthLevel.none, remote_sn: bool = False, local_sn: bool = False) -> None: ...
    auth: AuthLevel
    remote_sn: bool
    local_sn: bool

class Message:
    remote: str
    conn: ConnectionID
    access: Access
    @property
    def dataview(self) -> List[memoryview]: ...
    def data(self) -> List[bytes]: ...
    def reply(self, *args: Union[bytes, str, List[Union[bytes, str]]]) -> None: ...
    def back(self, command: str, *args: List[Union[bytes, str, List[Union[bytes, str]]]]) -> None: ...
    def request(self, command: str, on_reply: Callable[[Message], None], *args: Union[bytes, str, List[Union[bytes, str]]]) -> None: ...

class Category:
    def add_command(self, name: str, handler: Callable[[Message], None], *, auth: Union[AuthLevel, Access] = ..., thread: Optional[str] = None) -> None: ...
    def add_request_command(self, name: str, handler: Callable[[Message], Union[None, bytes, str, List[Union[bytes, str]]]]) -> None: ...

class FutureStatus(Enum):
    deferred = "deferred"
    ready    = "ready"
    timeout  = "timeout"

    @typing_extensions.override
    def __str__(self) -> str: ...

class ResultFuture:
    def valid(self) -> bool: ...
    def wait(self) -> None: ...
    @typing_extensions.overload
    def wait_for(self, timeout: timedelta, /) -> FutureStatus: ...
    @typing_extensions.overload
    def wait_for(self, seconds: float, /) -> FutureStatus: ...
    def wait_for(self, timeout: Union[timedelta, float], /) -> FutureStatus: ...
    def wait_until(self, deadline: datetime, /) -> FutureStatus: ...
    def get(self) -> List[bytes]: ...

class OxenMQ:
    def __init__(self, pubkey: bytes = b"", privkey: bytes = b"", service_node: bool = False, sn_lookup: Optional[Callable[[bytes], Optional[str]]] = None, log_level: Optional[LogLevel] = None) -> None: ...
    def add_category(self, name: str, access_level: Union[AuthLevel, Access], *, reserved_thread: int = ..., max_queue: int = ...) -> Category: ...
    def add_request_command(self, name: str, handler: Callable[[Message], Union[None, bytes, str, List[Union[bytes, str]]]]) -> None: ...
    def disconnect(self, conn: ConnectionID, linger: int = ...) -> None: ...
    def request_future(self, conn: ConnectionID, cmd: str, *data: Union[bytes, str, memoryview, List[Union[bytes, str, memoryview]]], timeout: Optional[int] = None, keep_alive: bool = False, **kwargs: Any,) -> ResultFuture: ...
    def start(self) -> None: ...
    def send(self, conn: ConnectionID, command: str, *args: Union[bytes, str, List[Union[bytes, str]]]) -> None: ...

    @typing_extensions.overload
    def connect_remote(self, remote: Address, on_success: Callable[[ConnectionID], None], on_failure: Callable[[ConnectionID, str], None], /, *, timeout: int = ..., ephemeral_routing_id: Optional[bool] = None, auth_level: AuthLevel = AuthLevel.none) -> None:
        """Asynchronously starts connecting to a remote address.

        Returns immediately. Messages sent on the connection will be queued until
        the connection is established (or dropped on failure).

        The provided callbacks are invoked exactly once:
        - on_success: called with the new ConnectionID when the connection succeeds
        - on_failure: called with (conn_id, reason) when the connection fails

        Keyword-only arguments:
            timeout:              Connection timeout in milliseconds (default: 10 seconds)
            ephemeral_routing_id: If True, use a random routing ID instead of pubkey-based
                                  (overrides the instance default oxenmq.EPHEMERAL_ROUTING_ID)
            auth_level:           Auth level to assign to incoming requests received
                                  over this connection (default: AuthLevel.none)

        This is the non-blocking / callback-based version.
        """
        ...

    @typing_extensions.overload
    def connect_remote(self, remote: Address, timeout: int = ..., /, *, ephemeral_routing_id: Optional[bool] = None, auth_level: AuthLevel = AuthLevel.none) -> ConnectionID:
        """Synchronously connects to a remote address.

        Blocks until the connection succeeds or fails.

        Returns:
            ConnectionID of the established connection

        Raises:
            RuntimeError: If the connection fails (with the failure reason in the message)

        Keyword-only arguments (same as the async version):
            timeout, ephemeral_routing_id, auth_level
        """
        ...

    def connect_inproc(self, on_success: Optional[Callable[[ConnectionID], None]], on_failure: Optional[Callable[[ConnectionID, str], None]], **kwargs: Any,) -> ConnectionID:
        """Establish a connection to ourself.

        Connects to the built-in in-process listening socket of this OxenMQ server for local
        communication.  Note that auth_level defaults to admin (unlike connect_remote), and the
        default timeout is much shorter.

        This connection is designed to allow code within the same process to invoke registered
        commands via the OxenMQ object.  The connection works whether or not there are any
        accessible external listeners.

        Also note that incoming inproc requests are unauthenticated: that is, they will always have
        admin-level access.

        Parameters:
            on_success: called with the new ConnectionID when the connection succeeds.
            on_failure: called with (conn_id, reason) when the connection fails.
        """
        ...

    def listen(self, bind: str, curve: bool, *, allow_connection: Optional[Callable[[str, bytes, bool], AuthLevel]] = None, on_bind: Optional[Callable[[bool], None]] = None) -> None:
        """Start listening on the given bind address.

        Incoming connections can come from anywhere.  ``allow_connection`` is invoked for any
        incoming connections on this address to determine the incoming remote's access and
        authentication level.

        This method may be called after start if dynamic listening is required, but it is generally
        recommended that long-term fixed listening endpoints be set up by calling this *before*
        start().

        Parameters:
            bind: can be any bind address string zmq supports, for example a tcp IP/port combination
                such as: "tcp://*:4567" or "tcp://1.2.3.4:5678".
            curve: whether the connection is curve-encrypted (True) or plaintext (False).  For
                plaintext connections the allow_connection callback will be invoked with an empty
                remote pubkey and service_node set to False.
            allow_connection: function to call to determine whether to allow the connection and, if
                so, the authentication level it receives.  The function is called with the remote's
                address, the remote's 32-byte pubkey as bytes (only for curve; empty for plaintext),
                and whether the remote is recognized as a service node (always False for plaintext;
                requires sn_lookup being configured in construction).  The function must return an
                AuthLevel value to accept the connection, or AuthLevel.denied to refuse it.  If
                omitted (or None) the default returns AuthLevel.none access for all incoming
                connections.
            on_bind: a callback to invoke when the port has been successfully opened or failed to
                open, called with a single boolean argument of True for success, False for failure.
                For addresses set up before .start() this will be called during start() itself; for
                post-start listens this will be called from the proxy thread when it opens the new
                port.  Note that this function is called directly from the proxy thread and so should
                be fast and non-blocking.
        """
        ...

    def add_timer(self, job: Callable[[], None], interval: timedelta, *, squelch: bool = True, thread: Optional[TaggedThreadID] = None) -> TimerId:
        """Adds a callback to be invoked on a repeating timer.

        The callback will be invoked approximately every ``interval``.

        Parameters:
            job: the callback to invoke on each timer tick.
            interval: the interval between invocations.

        Keyword-only parameters:
            squelch: When True (the default) this job will not be double-booked: that is, the
                callback will be skipped if a previous callback from this timer is already scheduled
                (or is still running).  If set to False then the callback will be scheduled even if
                an existing callback has not yet completed.
            thread: a TaggedThreadID specifying a tagged thread (created with ``add_tagged_thread``)
                in which the timer should run.  If unspecified then the timer runs in the general
                batch job queue.
        """
        ...
