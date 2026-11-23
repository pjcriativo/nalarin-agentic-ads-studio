"""Provider-neutral contract of the AI Gateway: messages, tools, requests, responses.

Everything an adapter understands is defined here; adapters translate to and from the
wire format of their provider. No provider concept (and no model name) lives in this module.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

# normalized finish reasons
STOP = "stop"
LENGTH = "length"
TOOL_CALLS = "tool_calls"
CONTENT_FILTER = "content_filter"
OTHER = "other"


@dataclass
class TextPart:
    text: str


@dataclass
class ImagePart:
    mime_type: str
    data: bytes


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any]
    # Opaque, provider-specific data that must travel back unchanged with this call on the next turn
    # (Gemini ``thoughtSignature``). Never shown, logged or interpreted outside its adapter.
    provider_data: Optional[Dict[str, Any]] = None


@dataclass
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False


Part = Union[TextPart, ImagePart, ToolCall, ToolResult]


@dataclass
class Message:
    """``user`` messages carry text/images/tool results; ``assistant`` messages carry
    text and/or tool calls (as returned by a previous turn)."""
    role: str
    parts: List[Part]

    @classmethod
    def user(cls, text: str, *images: ImagePart) -> "Message":
        return cls("user", [TextPart(text), *images])

    @classmethod
    def assistant(cls, text: str) -> "Message":
        return cls("assistant", [TextPart(text)])

    @classmethod
    def tool_results(cls, *results: ToolResult) -> "Message":
        return cls("user", list(results))


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: Dict[str, Any]  # JSON Schema object


@dataclass
class ChatRequest:
    messages: List[Message]
    system: Optional[str] = None
    tools: List[ToolSpec] = field(default_factory=list)
    tool_choice: Union[str, Dict[str, str]] = "auto"   # "auto" | "none" | "required" | {"name": ...}
    max_tokens: int = 1024
    temperature: Optional[float] = None
    json_mode: bool = False
    stop: List[str] = field(default_factory=list)

    def needs(self) -> Dict[str, bool]:
        """Capabilities a model must declare to serve this request."""
        return {
            "tools": bool(self.tools),
            "vision": any(isinstance(p, ImagePart) for m in self.messages for p in m.parts),
            "json": self.json_mode,
        }

    def approx_input_chars(self) -> int:
        total = len(self.system or "")
        for message in self.messages:
            for part in message.parts:
                if isinstance(part, TextPart):
                    total += len(part.text)
                elif isinstance(part, ToolResult):
                    total += len(part.content)
                elif isinstance(part, ToolCall):
                    total += len(str(part.arguments)) + len(part.name)
                elif isinstance(part, ImagePart):
                    total += 4000  # an image costs on the order of a thousand tokens
        for tool in self.tools:
            total += len(tool.name) + len(tool.description) + len(str(tool.parameters))
        return total


@dataclass
class Usage:
    input_tokens: int = 0          # ALL prompt tokens (cached ones included)
    output_tokens: int = 0         # ALL generated tokens (reasoning included)
    cached_input_tokens: int = 0   # subset of input_tokens served from a cache
    reasoning_tokens: int = 0      # subset of output_tokens spent thinking

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class ChatResponse:
    text: str
    tool_calls: List[ToolCall]
    finish_reason: str
    usage: Usage
    model: str
    provider: str
    response_id: Optional[str] = None
