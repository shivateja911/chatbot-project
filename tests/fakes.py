"""A scripted stand-in for the Anthropic client, so tests need no key or network."""
import copy
from types import SimpleNamespace as NS


def text(value):
    return NS(type="text", text=value)


def tool_use(name, args, call_id="t1"):
    return NS(type="tool_use", name=name, input=args, id=call_id)


def reply(*blocks, stop=None, model=None):
    stop = stop or ("tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn")
    usage = NS(input_tokens=100, output_tokens=20,
               cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return NS(content=list(blocks), stop_reason=stop, usage=usage, model=model)


class _Stream:
    def __init__(self, response):
        self.response = response
        self.text_stream = [b.text for b in response.content if b.type == "text"]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.response


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.messages = NS(stream=self._stream)
        self.beta = NS(messages=NS(stream=self._beta_stream))
        self.endpoints = []

    def _beta_stream(self, **kwargs):
        return self._stream(endpoint="beta", **kwargs)

    def _stream(self, endpoint="standard", **kwargs):
        self.endpoints.append(endpoint)
        self.requests.append({**kwargs, "messages": copy.deepcopy(kwargs["messages"])})
        return _Stream(self.responses.pop(0))
