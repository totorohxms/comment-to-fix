"""HTTP request schemas (pydantic).

Division of labor: these models own the transport SHAPE — field names, types,
required-ness — so a malformed body dies at the edge with a structured 422.
The semantic POLICY (length limits, truncation, redaction, business rules)
stays in backend/comments/utils.py as the single source of truth; nothing here
duplicates a limit.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

class TargetInput(BaseModel):
    """The element a new thread is anchored to."""
    selector: str = Field(min_length=1)
    label: str | None = None

class CaptureInput(BaseModel):
    """Runtime capture bundle. Known fields are typed; extras are allowed —
    the bundle is semi-opaque by design and the service validates/redacts it."""
    model_config = ConfigDict(extra="allow")

    sha: str | None = None
    env: str | None = None
    url: str | None = None
    sessionId: str | None = None
    traceId: str | None = None
    time: str | None = None
    viewport: dict[str, Any] | None = None
    userAgent: str | None = None
    target: dict[str, Any] | None = None
    network: list[Any] | None = None
    console: list[Any] | None = None
    domSnapshot: str | None = None
    screenshot: str | None = None

class PostCommentRequest(BaseModel):
    text: str
    threadId: str | None = None
    target: TargetInput | None = None      # required by the service for new threads
    capture: CaptureInput | None = None

    def to_body(self) -> dict:
        """The dict shape the comment service consumes."""
        return self.model_dump()

class ApproveThreadRequest(BaseModel):
    # The preview sha the approver actually reviewed; approving a superseded
    # preview is rejected server-side.
    previewSha: str | None = None
