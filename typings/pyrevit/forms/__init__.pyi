from __future__ import annotations

from typing import Any, Iterable, Mapping, MutableMapping, Optional, Sequence


class _FormComponent(object):
    name: str

    def __init__(self, name: str, *args: Any, **kwargs: Any) -> None: ...


class Label(_FormComponent):
    text: str

    def __init__(self, text: str) -> None: ...


class ComboBox(_FormComponent):
    values: Sequence[Any]
    default: Any

    def __init__(
        self,
        name: str,
        values: Mapping[str, Any] | Sequence[Any],
        default: Any | None = ...,
    ) -> None: ...


class TextBox(_FormComponent):
    Text: str

    def __init__(self, name: str, Text: str = "", **kwargs: Any) -> None: ...


class CheckBox(_FormComponent):
    state: bool

    def __init__(self, name: str, text: str = "", default: bool = False) -> None: ...


class Separator(_FormComponent):
    def __init__(self) -> None: ...


class FlexForm(object):
    values: MutableMapping[str, Any]

    def __init__(self, title: str, components: Sequence[Any]) -> None: ...

    def show(self) -> bool: ...
