import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from livekit.agents import APIConnectionError, APIConnectOptions
from livekit.plugins.boson_avatar.api import AvatarSessionInfo
from livekit.plugins.boson_avatar.errors import BosonAvatarException
from livekit.plugins.boson_avatar.stateless_api import StatelessBosonAvatarAPI

from .test_api import _Response, _Session

pytestmark = pytest.mark.unit
ID = "avrt_" + "a" * 32
HANDLE = "opaque-test-handle"
ACTIVE = {
    "protocol": "avatar-stateless-v2",
    "id": ID,
    "object": "avatar.realtime.session",
    "status": "active",
    "avatar_identity": "renderer",
    "release_confirmed": False,
    "control_handle": HANDLE,
}


def client(outcomes):
    session = _Session(outcomes)
    api = StatelessBosonAvatarAPI(
        api_key="synthetic",
        api_url="http://127.0.0.1/v2/avatar/livekit",
        session=session,
        conn_options=APIConnectOptions(max_retry=4),
    )
    return api, session


async def create(api, **kwargs):
    return await api.start_session(
        avatar_id="builtin_claire",
        livekit_url="wss://unit.livekit.cloud",
        livekit_room="test",
        livekit_token="synthetic-jwt",
        avatar_identity="renderer",
        publisher_identity="publisher",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_explicit_new_protocol_handle_redaction_and_no_idempotency():
    api, session = client([_Response(201, ACTIVE)])
    info = await create(api)
    assert info.control_handle == HANDLE and HANDLE not in repr(info)
    assert "Idempotency-Key" not in session.calls[0]["headers"]
    assert session.calls[0]["json"]["protocol"] == "avatar-stateless-v2"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome", [asyncio.TimeoutError(), _Response(503, {"error": {"code": "creation_uncertain"}})]
)
async def test_uncertain_create_is_never_retried(outcome):
    api, session = client([outcome, _Response(201, ACTIVE)])
    with pytest.raises(APIConnectionError):
        await create(api)
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_stop_preserves_handle_until_release_or_gone():
    api, session = client(
        [
            _Response(202, ACTIVE | {"status": "releasing"}),
            _Response(200, ACTIVE | {"status": "terminated", "release_confirmed": True}),
            _Response(410, {"error": {"code": "session_gone"}}),
        ]
    )
    info = AvatarSessionInfo(ID, "renderer", HANDLE)
    assert await api.stop_owned_session(info) is False
    assert await api.stop_owned_session(info) is True
    assert await api.stop_owned_session(info) is True
    assert all(c["headers"]["X-Avatar-Control-Handle"] == HANDLE for c in session.calls)


@pytest.mark.asyncio
async def test_no_legacy_id_or_replay_fallback():
    api, session = client([])
    with pytest.raises(BosonAvatarException):
        await create(api, idempotency_key="stable-key")
    with pytest.raises(BosonAvatarException):
        await api.end_session(ID)
    with pytest.raises(BosonAvatarException):
        await api.stop_owned_session(AvatarSessionInfo("avasess_old", "renderer", HANDLE))
    assert not session.calls


def test_explicit_v2_url_required():
    with pytest.raises(BosonAvatarException):
        StatelessBosonAvatarAPI(api_key="synthetic", api_url="https://unit.test/v1/avatar/livekit")


@pytest.mark.asyncio
async def test_pending_or_unreachable_stop_still_closes_local_media_and_keeps_control():
    from livekit.agents.voice.avatar import AvatarSession as BaseSession
    from livekit.plugins.boson_avatar.avatar import AvatarSession

    for outcome in (False, APIConnectionError("unreachable")):
        avatar = AvatarSession(
            avatar_id="builtin_claire",
            api_key="synthetic",
            api_url="http://127.0.0.1/v2/avatar/livekit",
            stateless=True,
        )
        info = AvatarSessionInfo(ID, "renderer", HANDLE)
        avatar._session_info = info
        stop = AsyncMock(return_value=False)
        if isinstance(outcome, Exception):
            stop.side_effect = outcome
        with (
            patch.object(avatar._api, "stop_owned_session", stop),
            patch.object(BaseSession, "aclose", AsyncMock()) as close,
        ):
            await avatar.aclose()
            close.assert_awaited_once()
            assert avatar._session_info is info and avatar._closed
        with patch.object(avatar._api, "stop_owned_session", AsyncMock(return_value=True)):
            await avatar.aclose()
            assert avatar._session_info is None
