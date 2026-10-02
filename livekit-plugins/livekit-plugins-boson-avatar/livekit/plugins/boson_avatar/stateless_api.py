"""Explicit experimental v2 client. Lost creates are never retried or recovered."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from .api import AvatarInfo, AvatarSessionInfo, BosonAvatarAPI
from .errors import BosonAvatarException


class StatelessBosonAvatarAPI(BosonAvatarAPI):
    """No session directory: the owning AvatarSession retains its opaque handle."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if urlsplit(self._api_url).path != "/v2/avatar/livekit":
            raise BosonAvatarException(
                "Stateless mode requires the explicit /v2/avatar/livekit URL"
            )
        # Both create and control are bounded one-shot calls. In particular a
        # timeout/503 is never interpreted as permission to create a second GPU.
        from dataclasses import replace

        self._conn_options = replace(self._conn_options, max_retry=0)

    async def start_session(
        self,
        *,
        avatar_id: str,
        livekit_url: str,
        livekit_room: str,
        livekit_token: str,
        avatar_identity: str,
        publisher_identity: str,
        width: int | None = None,
        height: int | None = None,
        max_duration_seconds: int | None = None,
        idempotency_key: str | None = None,
    ) -> AvatarSessionInfo:
        if idempotency_key is not None:
            raise BosonAvatarException("Stateless sessions do not support idempotency/recovery")
        body: dict[str, Any] = {
            "protocol": "avatar-stateless-v2",
            "avatar_id": avatar_id,
            "transport": {
                "type": "livekit",
                "url": livekit_url,
                "room_name": livekit_room,
                "participant_token": livekit_token,
                "participant_identity": avatar_identity,
                "publisher_identity": publisher_identity,
                "audio_source": "data_stream",
            },
        }
        if width is not None and height is not None:
            body["output"] = {"width": width, "height": height}
        if max_duration_seconds is not None:
            body["max_duration_seconds"] = max_duration_seconds
        _, data = await self._json(
            "POST", "/sessions", json=body, success_statuses=frozenset({201})
        )
        session_id, handle = data.get("id"), data.get("control_handle")
        routable = (
            isinstance(session_id, str)
            and re.fullmatch(r"avrt_[0-9a-f]{32}", session_id) is not None
            and isinstance(handle, str)
            and 1 <= len(handle) <= 8192
        )
        if not (
            routable
            and data.get("protocol") == "avatar-stateless-v2"
            and data.get("object") == "avatar.realtime.session"
            and data.get("status") == "active"
            and data.get("avatar_identity") == avatar_identity
            and data.get("release_confirmed") is False
        ):
            if routable:
                assert isinstance(session_id, str) and isinstance(handle, str)
                try:
                    await self.stop_owned_session(
                        AvatarSessionInfo(session_id, avatar_identity, handle)
                    )
                except Exception:  # noqa: BLE001 - never log a handle/provider body
                    pass
            raise BosonAvatarException("Invalid stateless session response; no recovery attempted")
        assert isinstance(session_id, str) and isinstance(handle, str)
        return AvatarSessionInfo(session_id, avatar_identity, handle)

    async def stop_owned_session(self, info: AvatarSessionInfo) -> bool:
        """False means pending release, not permission to keep local media open."""
        if not re.fullmatch(r"avrt_[0-9a-f]{32}", info.id) or not info.control_handle:
            raise BosonAvatarException("Owned stateless control handle required")
        status, data = await self._json(
            "DELETE",
            "/sessions/" + info.id,
            headers={"X-Avatar-Control-Handle": info.control_handle},
            success_statuses=frozenset({200, 202, 410}),
        )
        if status == 410:
            # Original instance/handle is gone. End local media; do not recreate.
            return True
        valid = (
            data.get("protocol") == "avatar-stateless-v2"
            and data.get("object") == "avatar.realtime.session"
            and data.get("id") == info.id
        )
        if (
            valid
            and status == 202
            and data.get("status") == "releasing"
            and data.get("release_confirmed") is False
        ):
            return False
        if (
            valid
            and status == 200
            and data.get("status") == "terminated"
            and data.get("release_confirmed") is True
        ):
            return True
        raise BosonAvatarException("Invalid stateless stop response; release is unconfirmed")

    async def end_session(self, session_id: str) -> None:
        raise BosonAvatarException(
            "Stateless control requires the owned handle, not a bare session ID"
        )

    async def list_avatars(self) -> list[AvatarInfo]:
        raise BosonAvatarException("Stateless session API does not own the asset catalog")
