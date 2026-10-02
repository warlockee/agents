# Boson Higgs Avatar plugin for LiveKit Agents

## Isolated stateless v2 experiment

Branch `codex/boson-avatar-stateless` adds explicit `AvatarSession(stateless=True,
api_url="https://your-service.example/v2/avatar/livekit", ...)`. Legacy mode stays
the default/control; this is not an upstream release or production cutover.

V2 creates are one-shot, including timeout/503: there is no idempotency key,
job-derived replay or old-ID recovery. A new call is new GPU work. The session
object retains an opaque control handle (excluded from repr), supplies it during
cleanup, and never falls back to bare-ID deletion. DELETE 202 keeps the handle for
a later cleanup attempt but closes local media immediately. DELETE 410 closes
local media without trying to recreate on another GPU. An unreachable backend
does not count as release proof. Asset listing stays with the asset provider.

Verification checkpoint (2026-10-01 America/Los_Angeles): plugin tests 39 passed
plus 38 subtests; Ruff passed; mypy passed for all seven plugin source files.
Coverage includes uncertain create without retry, protocol/URL mismatch, handle
redaction, pending stop and unreachable stop closing local media while retaining
cleanup context. Real media and fault acceptance remain separate ops gates.
HTTP redirects are disabled so a provider redirect cannot forward a control
handle to another origin; the same 39 tests plus 38 subtests pass with this guard.
Follow-up: v2 disables its still-owned audio-output chain and clears buffered
playout before waiting for DELETE; it does not disable an output replaced by the
application. Original renderer departure closes the Avatar instead of starting a
replacement. Tests now total 40 passed plus 38 subtests; Ruff and mypy pass.

Use Boson's Higgs Audio-Driven Avatar as the video output for a LiveKit voice
agent. This is an Avatar plugin: it composes with your existing voice/LLM
plugin and does not replace or fork it.

The plugin has no dependency on Boson Voice, Boson Audio, or a particular TTS
provider. Its input is the standard audio output of a LiveKit `AgentSession`,
so it works with any TTS plugin, realtime model, or custom source that produces
LiveKit audio frames. Only Avatar rendering and Avatar session lifecycle are
Boson-specific.

## Installation

```shell
pip install livekit-plugins-boson-avatar
```

Set the credentials used by your LiveKit Agent Worker:

```shell
export BOSON_API_KEY="..."
export BOSON_AVATAR_API_URL="https://your-avatar-session-service.example/v1"
export LIVEKIT_URL="wss://..."
export LIVEKIT_API_KEY="..."
export LIVEKIT_API_SECRET="..."
```

The plugin deliberately has no hard-coded provider endpoint. Your application
or deployment environment supplies the base URL exposed by its Boson Avatar
deployment/operator. The plugin appends `POST /sessions` when starting an
Avatar and `DELETE /sessions/{id}` during cleanup, so do not include
`/sessions` itself in `BOSON_AVATAR_API_URL`.

Load the project-scoped Avatar catalog on your application server and use it
to populate the face picker. The returned `avatar_id` is passed unchanged to
`AvatarSession`; browser users never need to type or remember it:

```python
from livekit.plugins import boson_avatar


async def avatar_options():
    # Returns [AvatarInfo(avatar_id="...", name="...")]
    return await boson_avatar.list_avatars()
```

This calls `GET {BOSON_AVATAR_API_URL}/avatars` with `BOSON_API_KEY`. Keep the
key server-side and cache the result according to your application's needs.

## Usage

Create the voice `AgentSession` with the audio provider of your choice, then
start the Avatar before starting the agent session:

```python
from livekit.agents import Agent, AgentSession, JobContext, inference
from livekit.plugins import boson_avatar


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    session = AgentSession(
        stt=inference.STT("deepgram/nova-3"),
        llm=inference.LLM("openai/gpt-4.1-mini"),
        tts=inference.TTS("cartesia/sonic-3"),
    )
    avatar = boson_avatar.AvatarSession(
        avatar_id="your-avatar-id",
    )

    await avatar.start(session, room=ctx.room)
    await session.start(
        agent=Agent(instructions="You are a helpful assistant."),
        room=ctx.room,
    )
```

`BOSON_AVATAR_ID` can supply `avatar_id` instead. `AvatarSession` also accepts
optional `width`, `height`, `max_duration_seconds`,
`avatar_participant_identity`, `idempotency_key`, and `APIConnectOptions`.
`max_duration_seconds` must be an integer from 1 through 14400.

Inside a LiveKit Agent job, provider-session creation automatically derives a
stable UUID idempotency key from the LiveKit job ID and Avatar session binding.
If LiveKit redelivers that job after a worker crash, the plugin recovers the
existing provider session instead of allocating another one. The standard
model is one Avatar lifecycle per LiveKit job. If one job intentionally starts
another Avatar after closing the first, pass a new explicit UUID
`idempotency_key` for that lifecycle.

The `avatar_id` is the value returned by `list_avatars()` behind the Avatar
selected in your application; end users do not need to type or remember it.

The plugin handles the provider API call, LiveKit participant token, PCM data
stream routing, interruption buffer clears, and provider-session cleanup. A
developer does not need to call Boson's Avatar REST API or combine voice and
Avatar responses in an application server. The application server only needs
its normal responsibility: create a LiveKit room and dispatch the Agent Worker.

When started inside a LiveKit job, cleanup is registered automatically. When
using the plugin in a standalone script or test, open LiveKit's HTTP context and
close the Avatar explicitly:

```python
from livekit.agents import utils


async with utils.http_context.open():
    await avatar.start(session, room)
    try:
        # Run the standalone session.
        ...
    finally:
        await avatar.aclose()
```
