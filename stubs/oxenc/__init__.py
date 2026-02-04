from __future__ import annotations
from typing import Any, Dict, List, overload, Union

@overload
def bt_serialize(val: int) -> bytes: ...
@overload
def bt_serialize(val: bytes) -> bytes: ...
@overload
def bt_serialize(val: str) -> bytes: ...
@overload
def bt_serialize(val: List[Any]) -> bytes: ...
@overload
def bt_serialize(val: Dict[bytes, Any]) -> bytes: ...

def bt_serialize(val: Any) -> bytes:
    """Serialize a value to bencode format (bytes).

    Supported input types:
    - int
    - bytes
    - str                  (encoded to UTF-8 bytes)
    - list / Sequence      (of supported bt values, recursively)
    - dict / Mapping       (keys: bytes or str → values: supported bt values, recursively)

    Returns:
        The bencoded representation as bytes.

    Notes:
        - String keys/values are UTF-8 encoded when serializing.
        - The result is always bytes (standard bencode).
    """
    ...

def bt_deserialize(val: Union[bytes, bytearray, memoryview]) -> Any:
    """Deserialize a bencoded value from bytes-like data.

    Accepts any buffer protocol object (bytes, bytearray, memoryview, etc.).

    Returns:
        One of:
        - int
        - bytes            (all string-like values come out as bytes)
        - List[...]        (list of any of the possible return types here, e.g. recursive)
        - Dict[bytes, ...] (key are always bytes and maps to any of the possible return types here e.g. recursive)

    Raises:
        ValueError: If the input is empty or invalid bencode.
        TypeError / BufferError: If the input does not support the buffer protocol.

    Notes:
        - Always produces bytes for string-like values (not str).
        - It is the caller's responsibility to decode bytes to str if needed
          (e.g. assuming UTF-8 for text fields).
    """
    ...
