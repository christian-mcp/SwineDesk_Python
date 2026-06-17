from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Iterable


SMS_BASE_URL = os.getenv("SWINEDESK_SMS_URL", "http://localhost:3000")
BACKEND_URL = os.getenv("SWINEDESK_BACKEND_URL", "http://localhost:8080")
TWILIO_TO = os.getenv("TWILIO_TO", "+16625277603")
REQUEST_TIMEOUT = float(os.getenv("SWINEDESK_SMS_TIMEOUT", "60"))

ROLE_PHONES = {
    "seller": "+525519535147",
    "buyer": "+528331208426",
    "vet": "+525554095443",
    "freight_operator": "+525526958910",
    "broker": "+525530503269",
}

ERROR_FRAGMENTS = (
    "technical issue",
    "try again in a minute",
    "i don't have a",
    "something went wrong",
    "internal server error",
    "traceback",
)


class HarnessFailure(AssertionError):
    pass


@dataclass(frozen=True)
class Turn:
    body: str
    expect_all: tuple[str, ...] = ()
    expect_any: tuple[str, ...] = ()
    forbid: tuple[str, ...] = ()


@dataclass(frozen=True)
class Scene:
    key: str
    role: str
    turns: tuple[Turn, ...]
    reset_before: bool = True

    @property
    def phone(self) -> str:
        return ROLE_PHONES[self.role]


SCENES = {
    "seller-create-intent": Scene(
        key="seller-create-intent",
        role="seller",
        turns=(
            Turn(
                body="I want to sell.",
                expect_any=("sell", "listing", "what kind", "what type", "how many"),
            ),
        ),
    ),
    "buyer-create-intent": Scene(
        key="buyer-create-intent",
        role="buyer",
        turns=(
            Turn(
                body="I need to buy pigs.",
                expect_any=("buy", "request", "what kind", "what type", "how many"),
            ),
        ),
    ),
    "broker-open-market": Scene(
        key="broker-open-market",
        role="broker",
        turns=(
            Turn(
                body="what's open on the board?",
                expect_all=("SUPPLY", "DEMAND"),
                expect_any=("159696", "271185"),
            ),
        ),
    ),
    "seller-loads": Scene(
        key="seller-loads",
        role="seller",
        turns=(
            Turn(
                body="what loads do I have coming up?",
                expect_all=("Iowa Select",),
                expect_any=("load", "pickup", "status"),
            ),
        ),
    ),
    "buyer-delivery": Scene(
        key="buyer-delivery",
        role="buyer",
        turns=(
            Turn(
                body="when's my delivery and who's hauling?",
                expect_any=("driver", "hauling", "delivery", "load"),
            ),
        ),
    ),
    "vet-health-cert": Scene(
        key="vet-health-cert",
        role="vet",
        turns=(
            Turn(
                body="which loads need a cert from me?",
                expect_all=("426721",),
                expect_any=("docs@elmpork.com", "health cert"),
            ),
        ),
    ),
    "freight-assignment": Scene(
        key="freight-assignment",
        role="freight_operator",
        turns=(
            Turn(
                body="What am I hauling?",
                expect_all=("426721", "104120", "787488"),
                expect_any=("Storm Lake", "loads", "assigned"),
            ),
        ),
    ),
}

DEFAULT_SCENES = tuple(
    SCENES[name]
    for name in (
        "seller-create-intent",
        "buyer-create-intent",
        "broker-open-market",
        "seller-loads",
        "buyer-delivery",
        "vet-health-cert",
        "freight-assignment",
    )
)


def _contains(text: str, fragment: str) -> bool:
    return fragment.casefold() in text.casefold()


def _parse_twiml_message(xml_text: str) -> str:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise HarnessFailure(f"Invalid TwiML response: {exc}\n{xml_text}") from exc

    messages = ["".join(message.itertext()).strip() for message in root.iter("Message")]
    messages = [message for message in messages if message]
    if not messages:
        raise HarnessFailure(f"TwiML response did not include a <Message> body.\n{xml_text}")
    return "\n\n".join(messages)


def _assert_no_error(reply: str, scene_key: str, body: str) -> None:
    for fragment in ERROR_FRAGMENTS:
        if _contains(reply, fragment):
            raise HarnessFailure(
                f"{scene_key}: reply to {body!r} contained error fragment {fragment!r}.\n{reply}"
            )


def _assert_turn_expectations(scene_key: str, turn: Turn, reply: str) -> None:
    _assert_no_error(reply, scene_key, turn.body)

    missing = [fragment for fragment in turn.expect_all if not _contains(reply, fragment)]
    if missing:
        raise HarnessFailure(
            f"{scene_key}: reply to {turn.body!r} missed required fragments {missing}.\n{reply}"
        )

    if turn.expect_any and not any(_contains(reply, fragment) for fragment in turn.expect_any):
        raise HarnessFailure(
            f"{scene_key}: reply to {turn.body!r} missed any-of fragments {turn.expect_any}.\n{reply}"
        )

    violated = [fragment for fragment in turn.forbid if _contains(reply, fragment)]
    if violated:
        raise HarnessFailure(
            f"{scene_key}: reply to {turn.body!r} contained forbidden fragments {violated}.\n{reply}"
        )


def _request(url: str, *, data: dict[str, str] | None = None) -> tuple[int, str]:
    encoded_data = None
    headers: dict[str, str] = {}
    if data is not None:
        encoded_data = urllib.parse.urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    request = urllib.request.Request(url, data=encoded_data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return response.getcode(), response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, body
    except urllib.error.URLError as exc:
        raise HarnessFailure(f"Request to {url} failed: {exc}") from exc


def _get_json(url: str, params: dict[str, str] | None = None) -> dict[str, object]:
    final_url = url
    if params:
        final_url = f"{url}?{urllib.parse.urlencode(params)}"
    status_code, body = _request(final_url)
    if status_code != 200:
        raise HarnessFailure(f"GET {final_url} returned HTTP {status_code}.\n{body}")
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise HarnessFailure(f"GET {final_url} did not return JSON.\n{body}") from exc


def _preflight() -> None:
    py_status, _ = _request(f"{SMS_BASE_URL}/")
    if py_status != 200:
        raise HarnessFailure(
            f"Python webhook health check failed: {py_status} at {SMS_BASE_URL}/"
        )

    backend_status, _ = _request(f"{BACKEND_URL}/api-docs")
    if backend_status != 200:
        raise HarnessFailure(
            f"Backend OpenAPI check failed: {backend_status} at {BACKEND_URL}/api-docs"
        )


def _verify_roles(scenes: Iterable[Scene]) -> None:
    checked_roles: set[str] = set()
    for scene in scenes:
        if scene.role in checked_roles:
            continue
        checked_roles.add(scene.role)

        payload = _get_json(
            f"{BACKEND_URL}/v1/sms/actors/resolve",
            params={"phone": scene.phone},
        )
        actual_role = payload.get("role")
        if actual_role != scene.role:
            raise HarnessFailure(
                f"Phone {scene.phone} resolved as {actual_role!r}, expected {scene.role!r}."
            )


def _send_sms(phone: str, body: str) -> str:
    status_code, reply_body = _request(
        f"{SMS_BASE_URL}/sms",
        data={"From": phone, "To": TWILIO_TO, "Body": body},
    )
    if status_code != 200:
        raise HarnessFailure(f"POST /sms returned HTTP {status_code}.\n{reply_body}")
    return _parse_twiml_message(reply_body)


def _reset_session(scene: Scene, verbose: bool) -> None:
    reply = _send_sms(scene.phone, "forget that, start over")
    _assert_no_error(reply, scene.key, "forget that, start over")
    if verbose:
        print(f"[{scene.key}] reset -> {reply}")


def run_scenes(selected_scenes: Iterable[Scene], *, verbose: bool = True) -> list[tuple[str, list[str]]]:
    scenes = list(selected_scenes)
    transcripts: list[tuple[str, list[str]]] = []

    _preflight()
    _verify_roles(scenes)

    for scene in scenes:
        transcript: list[str] = []
        if scene.reset_before:
            _reset_session(scene, verbose)

        if verbose:
            print(f"[{scene.key}] {scene.role} {scene.phone}")

        for turn in scene.turns:
            reply = _send_sms(scene.phone, turn.body)
            _assert_turn_expectations(scene.key, turn, reply)
            transcript.append(f"User: {turn.body}")
            transcript.append(f"Agent: {reply}")
            if verbose:
                print(f"  User:  {turn.body}")
                print(f"  Agent: {reply}")

        transcripts.append((scene.key, transcript))
        if verbose:
            print(f"[{scene.key}] PASS\n")

    return transcripts


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay local SwineDesk SMS demo scenes against POST /sms."
    )
    parser.add_argument(
        "--scenes",
        nargs="*",
        default=[scene.key for scene in DEFAULT_SCENES],
        help="Scene keys to run. Defaults to the seeded Phase 0 smoke set.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available scene keys and exit.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print the summary line.",
    )
    return parser.parse_args(argv)


def _resolve_scene_names(scene_names: Iterable[str]) -> list[Scene]:
    resolved: list[Scene] = []
    missing = [name for name in scene_names if name not in SCENES]
    if missing:
        raise HarnessFailure(f"Unknown scenes requested: {', '.join(sorted(missing))}")
    for name in scene_names:
        resolved.append(SCENES[name])
    return resolved


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    if args.list:
        for scene_name in sorted(SCENES):
            print(scene_name)
        return 0

    scenes = _resolve_scene_names(args.scenes)
    try:
        transcripts = run_scenes(scenes, verbose=not args.quiet)
    except HarnessFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print(f"PASS: {len(transcripts)}/{len(scenes)} scenes")
    return 0


def test_sms_webhook_smoke() -> None:
    run_scenes(DEFAULT_SCENES, verbose=False)


if __name__ == "__main__":
    raise SystemExit(main())
