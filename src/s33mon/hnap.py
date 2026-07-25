"""HNAP transport for the S33: challenge/response login and authenticated calls."""

import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Self

import httpx

from s33mon.models import HNAPAction, ModemStats, parse_modem_response

LOGIN_KEY = "withoutloginkey"
HNAP_NAMESPACE = "http://purenetworks.com/HNAP1"


def hex_hmac_md5(key: str, data: str) -> str:
    """Calculate HMAC-MD5 and return as hex string."""
    return hmac.new(key.encode("utf-8"), data.encode("utf-8"), hashlib.md5).hexdigest()


def change_text(s: str) -> str:
    """Toggle case of all characters in string."""
    return s.swapcase()


def hnap_auth(key: str, action: str, timestamp_ms: int) -> str:
    """Build the HNAP_AUTH token the modem expects for an action at a given time."""
    uri = f'"{HNAP_NAMESPACE}/{action}"'
    return change_text(hex_hmac_md5(key, str(timestamp_ms) + uri))


@dataclass(frozen=True)
class RebootOptions:
    set_eee_enable: bool = False
    led_status: bool = True

    def to_payload(self) -> dict:
        return {
            "SetEEEEnable": "1" if self.set_eee_enable else "0",
            "LED_Status": "1" if self.led_status else "0",
        }


class HNAPClient:
    def __init__(self, host: str, username: str, password: str, verify: bool = False):
        self.host = host
        self.username = username
        self.password = password
        self.base_url = f"https://{host}"
        self.client = httpx.Client(verify=verify, follow_redirects=True)
        self.private_key = None
        self.cookie = None

    def _make_request(self, action: str, payload: dict) -> dict:
        """Make an authenticated HNAP request."""
        timestamp = int(time.time() * 1000) % 2000000000000
        key = self.private_key or LOGIN_KEY

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "SOAPACTION": f'"{HNAP_NAMESPACE}/{action}"',
            "HNAP_AUTH": f"{hnap_auth(key, action, timestamp)} {timestamp}",
        }

        cookies = {}
        if self.cookie:
            cookies["uid"] = self.cookie
        if self.private_key:
            cookies["PrivateKey"] = self.private_key

        response = self.client.post(
            f"{self.base_url}/HNAP1/",
            json=payload,
            headers=headers,
            cookies=cookies,
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

    def login(self):
        """Perform two-stage login process."""
        # Stage 1: Request challenge
        payload = {
            "Login": {
                "Action": "request",
                "Username": self.username,
                "LoginPassword": "",
                "Captcha": "",
                "PrivateLogin": "LoginPassword",
            }
        }

        response = self._make_request("Login", payload)
        login_response = response.get("LoginResponse", {})

        challenge = login_response.get("Challenge")
        self.cookie = login_response.get("Cookie")
        public_key = login_response.get("PublicKey")

        if not all([challenge, self.cookie, public_key]):
            raise ValueError(
                "Missing challenge, cookie, or public key in login response"
            )

        self.private_key = hex_hmac_md5(public_key + self.password, challenge).upper()
        login_password = hex_hmac_md5(self.private_key, challenge).upper()

        # Stage 2: Perform actual login
        payload = {
            "Login": {
                "Action": "login",
                "Username": self.username,
                "LoginPassword": login_password,
                "Captcha": "",
                "PrivateLogin": "LoginPassword",
            }
        }

        response = self._make_request("Login", payload)
        login_result = response.get("LoginResponse", {}).get("LoginResult")

        if login_result != "OK":
            raise RuntimeError(
                f'Login failed, result not OK: "{login_result}". Password may be incorrect.'
            )

    def get_stats(self, actions: list[HNAPAction]) -> ModemStats:
        if len(actions) == 0:
            raise ValueError("Must specify at least one action to fetch stats")
        get_multiple = "GetMultipleHNAPs"
        payload = {get_multiple: {action: "" for action in actions}}
        response = self._make_request(get_multiple, payload)
        if len(actions) > 1:
            response = response["GetMultipleHNAPsResponse"]
        return parse_modem_response(response)

    def reboot(self, options: RebootOptions | None = None):
        """Reboot the modem."""
        action = "SetArrisConfigurationInfo"
        payload = {action: {"Action": "reboot"}}
        if options:
            payload[action].update(options.to_payload())

        response = self._make_request(action, payload)
        result = response.get(f"{action}Response", {}).get(f"{action}Result")
        if result != "OK":
            raise RuntimeError(f'Reboot failed, result not OK: "{result}"')

    def close(self):
        """Close the HTTP client."""
        self.client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_) -> None:
        self.close()
