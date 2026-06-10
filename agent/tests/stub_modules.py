import importlib.util
import sys
import types


def _ensure_pydantic() -> None:
    try:
        import pydantic  # noqa: F401
        return
    except ModuleNotFoundError:
        # Real pydantic isn't installed — fall through to install
        # the stub used by lightweight test runs.
        pass

    pydantic_stub = types.ModuleType("pydantic")
    _missing = object()

    class _FieldSpec:
        def __init__(self, default=_missing, default_factory=None):
            self.default = default
            self.default_factory = default_factory

    def _stub_field(default=_missing, default_factory=None, **kwargs):
        _ = kwargs
        return _FieldSpec(default=default, default_factory=default_factory)

    def _stub_model_validator(*args, **kwargs):
        _ = (args, kwargs)

        def _decorator(fn):
            setattr(fn, "__is_model_validator__", True)
            return fn

        return _decorator

    class _StubConfigDict(dict):
        pass

    class _StubBaseModel:
        __model_validators__ = ()

        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__(**kwargs)
            validators = []
            for name, value in cls.__dict__.items():
                if callable(value) and getattr(value, "__is_model_validator__", False):
                    validators.append(name)
            cls.__model_validators__ = tuple(validators)

        def __init__(self, **kwargs):
            annotations = {}
            for base in reversed(type(self).__mro__):
                annotations.update(getattr(base, "__annotations__", {}))

            for field_name in annotations:
                if field_name in kwargs:
                    value = kwargs[field_name]
                elif hasattr(type(self), field_name):
                    field_default = getattr(type(self), field_name)
                    if isinstance(field_default, _FieldSpec):
                        if field_default.default_factory is not None:
                            value = field_default.default_factory()
                        elif field_default.default is not _missing:
                            value = field_default.default
                        else:
                            value = None
                    else:
                        value = field_default
                else:
                    value = None
                setattr(self, field_name, value)

            for extra_key, extra_val in kwargs.items():
                if extra_key not in annotations:
                    setattr(self, extra_key, extra_val)

            for validator_name in getattr(type(self), "__model_validators__", ()):
                validator = getattr(self, validator_name)
                validator()

        def model_dump(self, mode="json"):
            _ = mode
            return dict(self.__dict__)

    pydantic_stub.BaseModel = _StubBaseModel
    pydantic_stub.Field = _stub_field
    pydantic_stub.model_validator = _stub_model_validator
    pydantic_stub.ConfigDict = _StubConfigDict
    sys.modules["pydantic"] = pydantic_stub


def _ensure_simple_module_stubs() -> None:
    if "roonapi" not in sys.modules and not importlib.util.find_spec("roonapi"):
        roonapi_stub = types.ModuleType("roonapi")

        class _StubRoonApi:
            def __init__(self, *args, **kwargs):
                _ = (args, kwargs)

        class _StubRoonDiscovery:
            def __init__(self, *args, **kwargs):
                _ = (args, kwargs)

        roonapi_stub.RoonApi = _StubRoonApi
        roonapi_stub.RoonDiscovery = _StubRoonDiscovery
        sys.modules["roonapi"] = roonapi_stub

    if "thefuzz" not in sys.modules and not importlib.util.find_spec("thefuzz"):
        thefuzz_stub = types.ModuleType("thefuzz")

        class _StubFuzz:
            @staticmethod
            def ratio(s1="", s2="", *args, **kwargs):
                """Simple stub: 100 if equal, 0 otherwise."""
                return 100 if str(s1).strip().lower() == str(s2).strip().lower() else 0

            @staticmethod
            def WRatio(s1="", s2="", *args, **kwargs):
                """Simple stub: 100 if equal, partial overlap scored roughly."""
                a, b = str(s1).strip().lower(), str(s2).strip().lower()
                if a == b:
                    return 100
                if a in b or b in a:
                    return 80
                return 0

        thefuzz_stub.fuzz = _StubFuzz
        sys.modules["thefuzz"] = thefuzz_stub

    if "requests" not in sys.modules:
        requests_stub = types.ModuleType("requests")
        requests_stub.get = lambda *args, **kwargs: None
        requests_stub.post = lambda *args, **kwargs: None

        # Expose a `requests.exceptions` submodule with the standard
        # exception classes so handlers that do
        # ``except requests.exceptions.Timeout`` work under the stub.
        # Each is a distinct subclass of a shared base so isinstance
        # checks discriminate correctly.
        exceptions_stub = types.ModuleType("requests.exceptions")

        class _StubRequestException(Exception):  # noqa: N818
            pass
        exceptions_stub.RequestException = _StubRequestException
        exceptions_stub.Timeout = type("Timeout", (_StubRequestException,), {})
        exceptions_stub.ConnectionError = type(
            "ConnectionError", (_StubRequestException,), {},
        )
        exceptions_stub.HTTPError = type("HTTPError", (_StubRequestException,), {})
        requests_stub.exceptions = exceptions_stub

        # `requests.Response` is referenced by MagicMock(spec=...) in
        # some tests; provide a minimal placeholder class.
        class _StubResponse:
            status_code = 200
            def json(self):
                return {}
        requests_stub.Response = _StubResponse

        sys.modules["requests"] = requests_stub
        sys.modules["requests.exceptions"] = exceptions_stub

    if "aiohttp" not in sys.modules:
        aiohttp_stub = types.ModuleType("aiohttp")

        class _StubClientSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                _ = (exc_type, exc, tb)
                return False

        class _StubClientError(Exception):
            pass

        class _StubClientConnectorError(_StubClientError):
            def __init__(self, *args, connection_key=None, os_error=None, **kwargs):
                super().__init__(str(os_error) if os_error else "ClientConnectorError")

        aiohttp_stub.ClientSession = _StubClientSession
        aiohttp_stub.ClientError = _StubClientError
        aiohttp_stub.ClientConnectorError = _StubClientConnectorError
        sys.modules["aiohttp"] = aiohttp_stub

    if "rich" not in sys.modules:
        rich_stub = types.ModuleType("rich")
        console_stub = types.ModuleType("rich.console")
        panel_stub = types.ModuleType("rich.panel")
        syntax_stub = types.ModuleType("rich.syntax")

        class _StubConsole:
            def __init__(self, *args, **kwargs):
                _ = (args, kwargs)

            def print(self, *args, **kwargs):
                _ = (args, kwargs)

        class _StubPanel:
            def __init__(self, *args, **kwargs):
                _ = (args, kwargs)

        class _StubSyntax:
            def __init__(self, *args, **kwargs):
                _ = (args, kwargs)

        console_stub.Console = _StubConsole
        panel_stub.Panel = _StubPanel
        syntax_stub.Syntax = _StubSyntax
        rich_stub.console = console_stub
        rich_stub.panel = panel_stub
        rich_stub.syntax = syntax_stub
        sys.modules["rich"] = rich_stub
        sys.modules["rich.console"] = console_stub
        sys.modules["rich.panel"] = panel_stub
        sys.modules["rich.syntax"] = syntax_stub


def install_common_test_stubs() -> None:
    _ensure_pydantic()
    _ensure_simple_module_stubs()
