"""The YAML route config as typed models, with validation.

The schema of the previous router stays valid as it is. Unknown keys are errors
on purpose: a misspelt ``request_defaults`` that is silently ignored would break
reasoning output without any signal.
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping
from typing import Any, Literal, get_args

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

RouteType = Literal["chat", "vision", "embedding", "reranker", "stt", "tts"]
ROUTE_TYPES: tuple[str, ...] = get_args(RouteType)
PROXY_SCHEMES = ("http", "https", "socks5", "socks5h")


class ConfigError(Exception):
    """The config cannot be used. ``messages`` holds one line per problem."""

    def __init__(self, messages: list[str]) -> None:
        super().__init__("; ".join(messages))
        self.messages = messages


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _empty_to_none(value: Any) -> Any:
    return None if isinstance(value, str) and value.strip() == "" else value


def _check_http_url(value: str, what: str) -> str:
    if not value.startswith(("http://", "https://")) or len(value.split("://", 1)[1]) == 0:
        raise ValueError(f"{what} must be an http:// or https:// URL, got {value!r}")
    return value


def _check_proxy(value: str | None) -> str | None:
    if value is None:
        return None
    scheme = value.split("://", 1)[0] if "://" in value else ""
    if scheme not in PROXY_SCHEMES:
        raise ValueError(
            f"proxy must start with one of {', '.join(s + '://' for s in PROXY_SCHEMES)}; got {value!r}"
        )
    return value


def _check_health_path(value: str | None) -> str | None:
    if value is not None and not value.startswith("/"):
        raise ValueError(f"health_path must start with '/', got {value!r}")
    return value


class ServerConfig(_Model):
    host: str | None = None
    port: int | None = None


class RouteConfig(_Model):
    name: str
    type: RouteType
    # A single URL or a list of URLs (a load-balanced pool); always a list here.
    backend: list[str]
    endpoint: str
    backend_model_name: str | None = None
    models: list[str] = Field(default_factory=list)
    owned_by: str | None = None
    proxy: str | None = None
    # Absent means "/health". Explicit null or "" means: no probe.
    health_path: str | None = "/health"
    backend_api_key_env: str | None = None
    request_defaults: dict[str, Any] = Field(default_factory=dict)
    timeout_s: float | None = None
    usage_from_stream: bool = True

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be empty")
        return value

    @field_validator("backend", mode="before")
    @classmethod
    def _backend(cls, value: Any) -> Any:
        items = [value] if isinstance(value, str) else value
        if not isinstance(items, list) or not items:
            raise ValueError("backend must be a URL or a non-empty list of URLs")
        for item in items:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("backend must be a URL or a non-empty list of URLs")
            _check_http_url(item, "backend")
        if len(set(items)) != len(items):
            raise ValueError("backend lists the same URL more than once")
        return items

    @field_validator("endpoint")
    @classmethod
    def _endpoint(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError(f"endpoint must start with '/', got {value!r}")
        return value

    @field_validator("models", mode="before")
    @classmethod
    def _models(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, list):
            for item in value:
                if not isinstance(item, str) or not item.strip():
                    raise ValueError("models must be a list of non-empty strings")
        return value

    @field_validator("request_defaults", mode="before")
    @classmethod
    def _request_defaults(cls, value: Any) -> Any:
        return {} if value is None else value

    @field_validator(
        "backend_model_name", "owned_by", "proxy", "backend_api_key_env", "health_path", mode="before"
    )
    @classmethod
    def _blank(cls, value: Any) -> Any:
        return _empty_to_none(value)

    @field_validator("proxy")
    @classmethod
    def _proxy(cls, value: str | None) -> str | None:
        return _check_proxy(value)

    @field_validator("health_path")
    @classmethod
    def _health_path(cls, value: str | None) -> str | None:
        return _check_health_path(value)

    @field_validator("timeout_s")
    @classmethod
    def _timeout(cls, value: float | None) -> float | None:
        if value is not None and value <= 0:
            raise ValueError("timeout_s must be greater than 0")
        return value


class UpstreamConfig(_Model):
    """Another router used as one upstream with one key (design 7.9)."""

    name: str
    base_url: str
    api_key_env: str | None = None
    health_path: str | None = "/livez"
    include: list[str] = Field(default_factory=lambda: ["*"])
    exclude: list[str] = Field(default_factory=list)
    discovery_interval: float = 60.0
    proxy: str | None = None
    tls_verify: bool = True
    ca_file: str | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be empty")
        return value

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str) -> str:
        return _check_http_url(value, "base_url").rstrip("/")

    @field_validator("include", "exclude", mode="before")
    @classmethod
    def _globs(cls, value: Any, info: Any) -> Any:
        if value is None:
            return ["*"] if info.field_name == "include" else []
        return value

    @field_validator("api_key_env", "proxy", "ca_file", "health_path", mode="before")
    @classmethod
    def _blank(cls, value: Any) -> Any:
        return _empty_to_none(value)

    @field_validator("proxy")
    @classmethod
    def _proxy(cls, value: str | None) -> str | None:
        return _check_proxy(value)

    @field_validator("health_path")
    @classmethod
    def _health_path(cls, value: str | None) -> str | None:
        return _check_health_path(value)

    @field_validator("discovery_interval")
    @classmethod
    def _interval(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("discovery_interval must be greater than 0")
        return value


class RouterConfig(_Model):
    server: ServerConfig = Field(default_factory=ServerConfig)
    routes: list[RouteConfig] = Field(default_factory=list)
    upstreams: list[UpstreamConfig] = Field(default_factory=list)

    @field_validator("server", mode="before")
    @classmethod
    def _server(cls, value: Any) -> Any:
        return {} if value is None else value

    @field_validator("routes", "upstreams", mode="before")
    @classmethod
    def _lists(cls, value: Any) -> Any:
        return [] if value is None else value


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe loader that refuses a mapping with the same key twice.

    Plain YAML loading keeps the last one silently, which hides exactly the kind
    of mistake the validation is there to catch.
    """

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=True)
            try:
                duplicate = key in seen
                seen.add(key)
            except TypeError:
                continue
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    None, None, f"duplicate key {key!r}", key_node.start_mark
                )
        return super().construct_mapping(node, deep=deep)


def parse_yaml(text: str) -> Any:
    try:
        return yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506 - a SafeLoader subclass
    except yaml.YAMLError as exc:
        raise ConfigError([f"not valid YAML: {' '.join(str(exc).split())}"]) from exc


def load_config(path: str) -> RouterConfig:
    """Read and validate the config file. Raises ``ConfigError``."""
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        raise ConfigError([f"cannot read {path}: {exc.strerror or exc}"]) from exc
    return parse_config(parse_yaml(text))


def parse_config(data: Any) -> RouterConfig:
    """Validate an already parsed document. Raises ``ConfigError`` listing every problem."""
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(["the top level must be a mapping with 'server', 'routes' and 'upstreams'"])
    # Duplicates are looked for in the raw document, so that they are reported
    # together with schema problems instead of one run later.
    problems = _cross_checks(data)
    try:
        config = RouterConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_format_validation_errors(exc, data) + problems) from exc
    if problems:
        raise ConfigError(problems)
    return config


def config_warnings(config: RouterConfig, env: Mapping[str, str]) -> list[str]:
    """Things worth a log line that do not stop the config from loading."""
    out: list[str] = []
    for route in config.routes:
        var = route.backend_api_key_env
        if var and not env.get(var):
            out.append(
                f"route '{route.name}' references env var '{var}' but it is unset or empty; "
                "requests to its backends carry no Authorization header"
            )
    for upstream in config.upstreams:
        var = upstream.api_key_env
        if var and not env.get(var):
            out.append(
                f"upstream '{upstream.name}' references env var '{var}' but it is unset or empty; "
                "requests to it carry no Authorization header"
            )
    if not config.routes and not config.upstreams:
        out.append("the config defines no routes and no upstreams; every model request will get 404")
    return out


def _named_items(data: dict[str, Any], section: str) -> list[tuple[int, str, dict[str, Any]]]:
    """(index, name, item) for the entries of a section that have a usable name."""
    items = data.get(section)
    if not isinstance(items, list):
        return []
    return [
        (index, item["name"], item)
        for index, item in enumerate(items)
        if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"]
    ]


def _cross_checks(data: dict[str, Any]) -> list[str]:
    """Duplicate route names, duplicate aliases (two routes sharing one) and upstream names."""
    problems: list[str] = []
    routes = _named_items(data, "routes")
    names: dict[str, int] = {}
    for index, name, _ in routes:
        if name in names:
            first = names[name]
            problems.append(
                f"route '{name}' (routes[{index}]): duplicate route name, already used by routes[{first}]"
            )
        else:
            names[name] = index
    aliases: dict[str, tuple[int, str]] = {}
    for index, name, route in routes:
        models = route.get("models")
        for alias in dict.fromkeys(
            m for m in (models if isinstance(models, list) else []) if isinstance(m, str)
        ):
            owner = aliases.get(alias)
            if owner is not None and owner[0] != index:
                problems.append(
                    f"route '{name}' (routes[{index}]): models: alias '{alias}' "
                    f"is already defined by route '{owner[1]}'"
                )
            else:
                aliases[alias] = (index, name)
    upstream_names: dict[str, int] = {}
    for index, name, _ in _named_items(data, "upstreams"):
        if name in upstream_names:
            problems.append(
                f"upstream '{name}' (upstreams[{index}]): duplicate upstream name, "
                f"already used by upstreams[{upstream_names[name]}]"
            )
        else:
            upstream_names[name] = index
        if name in names:
            problems.append(f"upstream '{name}' (upstreams[{index}]): name is already used by a route")
    return problems


def _format_validation_errors(exc: ValidationError, data: dict[str, Any]) -> list[str]:
    messages: list[str] = []
    for error in exc.errors():
        loc = tuple(error["loc"])
        where, field_path, model = _locate(loc, data)
        kind = error["type"]
        field = ".".join(str(part) for part in field_path)
        if kind == "extra_forbidden":
            known = list(model.model_fields) if model is not None else []
            hint = difflib.get_close_matches(str(field_path[-1]), known, n=1) if field_path else []
            suffix = f" (did you mean '{hint[0]}'?)" if hint else ""
            text = f"unknown key '{field}'{suffix}"
        elif kind == "missing":
            text = f"missing required key '{field}'"
        elif kind == "literal_error" and field_path and field_path[-1] == "type":
            text = f"unknown type {error.get('input')!r}; expected one of {', '.join(ROUTE_TYPES)}"
        else:
            detail = str(error["msg"]).removeprefix("Value error, ")
            text = f"{field}: {detail}" if field else detail
        messages.append(f"{where}: {text}" if where else text)
    return messages


def _locate(
    loc: tuple[Any, ...], data: dict[str, Any]
) -> tuple[str, tuple[Any, ...], type[BaseModel] | None]:
    """Turn a pydantic error location into "route 'X' (routes[3])" plus the field path."""
    sections: dict[str, tuple[str, type[BaseModel]]] = {
        "routes": ("route", RouteConfig),
        "upstreams": ("upstream", UpstreamConfig),
    }
    if len(loc) >= 2 and loc[0] in sections and isinstance(loc[1], int):
        label, model = sections[loc[0]]
        items = data.get(loc[0])
        item = items[loc[1]] if isinstance(items, list) and loc[1] < len(items) else None
        name = item.get("name") if isinstance(item, dict) else None
        index = f"{loc[0]}[{loc[1]}]"
        where = f"{label} '{name}' ({index})" if isinstance(name, str) and name else index
        return where, loc[2:], model
    if loc and loc[0] == "server":
        return "server", loc[1:], ServerConfig
    return "", loc, RouterConfig
