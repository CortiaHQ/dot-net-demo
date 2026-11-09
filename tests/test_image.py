"""Integration checks against the RUNNING IMAGE.

These checks use HTTP for behavior and Docker for the final image's runtime inventory.
The evaluator (tools/evaluate.py check) starts the container, points DEMO_BASE_URL at
it, runs this file, and stops the container again. Run by hand with either of:

    python -m unittest discover -s tests -v
    python -m pytest tests -v

The three behavioral assertions the fixture is judged on:

  1. A signed-out user who asks for the protected JSON is sent to the local sign-in page.
  2. The unauthenticated response does not contain the protected data.
  3. A signed-in user gets the JSON.

The final image must also contain stable Microsoft.NETCore.App and
Microsoft.AspNetCore.App 10.0.14 or newer within the .NET 10.0 runtime line.
The runtime check requires Docker access and a loopback-published container.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import unittest
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from client.democlient import DemoClient  # noqa: E402

BASE_URL = os.environ.get("DEMO_BASE_URL", "http://127.0.0.1:8080")
USERNAME = os.environ.get("DEMO_USERNAME", "demo")
PASSWORD = os.environ.get("DEMO_PASSWORD", "demo-password-not-real")
# Synthetic marker; it is the value the protected endpoint returns and must never leak.
PROTECTED_MARKER = os.environ.get("DEMO_SECRET", "DEMO-PROTECTED-VALUE-7f3c9a")


def _wait_until_up(base: str, seconds: float = 30.0) -> None:
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        try:
            resp = DemoClient(base).request("/healthz", timeout=2)
            if resp.status == 200:
                return
            last = f"HTTP {resp.status}"
        except Exception as exc:  # noqa: BLE001 - any connection error counts as "not up"
            last = repr(exc)
        time.sleep(0.5)
    raise RuntimeError(
        f"{base}/healthz did not answer 200 within {seconds}s (last: {last})"
    )


class CookieDemoChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _wait_until_up(BASE_URL)

    def setUp(self) -> None:
        self.client = DemoClient(BASE_URL)

    def test_final_image_contains_patched_dotnet_10_runtimes(self) -> None:
        target = urllib.parse.urlsplit(BASE_URL)
        self.assertIn(
            target.hostname,
            ("127.0.0.1", "localhost", "::1"),
            "the runtime inventory check requires a local Docker container",
        )
        port = str(target.port or (443 if target.scheme == "https" else 80))
        containers = subprocess.run(
            ["docker", "ps", "--quiet", "--no-trunc", "--filter", f"publish={port}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.split()
        self.assertTrue(containers, f"no running container publishes port {port}")

        details = json.loads(
            subprocess.run(
                ["docker", "inspect", *containers],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout
        )
        matching = [
            container
            for container in details
            if any(
                binding["HostPort"] == port
                and binding["HostIp"] in ("127.0.0.1", "::1", "0.0.0.0", "::", "")
                for endpoint, bindings in container["NetworkSettings"]["Ports"].items()
                if endpoint.endswith("/tcp")
                for binding in (bindings or [])
            )
        ]
        self.assertEqual(
            len(matching),
            1,
            f"expected exactly one running container publishing TCP port {port}",
        )
        image_id = matching[0]["Image"]
        inventory = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--entrypoint",
                "dotnet",
                image_id,
                "--list-runtimes",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout
        print(f"Runtime inventory inside final image {image_id}:\n{inventory}")

        for framework in ("Microsoft.NETCore.App", "Microsoft.AspNetCore.App"):
            with self.subTest(framework=framework):
                versions = re.findall(
                    rf"^{re.escape(framework)}\s+(\S+)\s+\[.+\]\s*$",
                    inventory,
                    flags=re.M,
                )
                self.assertTrue(
                    versions, f"{framework} is missing from the final image"
                )
                for version in versions:
                    stable = re.fullmatch(r"10\.0\.(\d+)", version)
                    self.assertIsNotNone(
                        stable,
                        f"{framework} must be a stable .NET 10.0 runtime; found {version}",
                    )
                    self.assertGreaterEqual(
                        int(stable.group(1)),
                        14,
                        f"{framework} must be 10.0.14 or newer; found {version}",
                    )

    # 1. signed-out sign-in flow -------------------------------------------------------

    def test_signed_out_user_is_sent_to_the_sign_in_page(self) -> None:
        first = self.client.get_secret()

        self.assertEqual(
            first.status,
            302,
            "a signed-out request to /api/secret must be answered with a redirect "
            f"(302) to the sign-in page; got {first.status} with location={first.location!r}",
        )
        self.assertIsNotNone(
            first.location, "the redirect must carry a Location header"
        )
        target = urllib.parse.urlsplit(urllib.parse.urljoin(first.url, first.location))
        self.assertEqual(
            target.netloc,
            urllib.parse.urlsplit(BASE_URL).netloc,
            "the sign-in page must be local",
        )
        self.assertEqual(target.path, "/login")
        return_url = urllib.parse.parse_qs(target.query).get("ReturnUrl", [""])[0]
        self.assertEqual(
            return_url,
            "/api/secret",
            "the sign-in page must know where to send the user back",
        )

        landing = self.client.follow(first)
        self.assertEqual(landing.status, 200)
        self.assertIn("text/html", landing.content_type)
        self.assertIn(
            'id="sign-in"', landing.text, "the landing page must be the sign-in form"
        )

    # 2. nothing protected leaks to the signed-out user ------------------------------------

    def test_unauthenticated_response_contains_no_protected_data(self) -> None:
        first = self.client.get_secret()
        self.assertNotEqual(
            first.status,
            200,
            "the protected endpoint must not answer 200 to a signed-out user",
        )
        self.assertNotIn(PROTECTED_MARKER, first.text)
        self.assertNotIn("application/json", first.content_type)
        for value in first.headers.values():
            self.assertNotIn(PROTECTED_MARKER, value)

        landing = self.client.follow(first)
        self.assertNotIn(PROTECTED_MARKER, landing.text)

    # 3. signed-in user gets the JSON -------------------------------------------------------

    def test_signed_in_user_retrieves_the_json(self) -> None:
        signed_in = self.client.login(USERNAME, PASSWORD, return_url="/api/secret")
        self.assertEqual(
            signed_in.status,
            302,
            f"sign-in must redirect; got {signed_in.status}: {signed_in.text[:200]}",
        )
        self.assertTrue(
            any(c.name == "demo.auth" for c in self.client.jar),
            "sign-in must set the auth cookie",
        )

        resp = self.client.get_secret()
        self.assertEqual(resp.status, 200)
        self.assertIn("application/json", resp.content_type)
        payload = resp.json()
        self.assertEqual(payload.get("user"), USERNAME)
        self.assertEqual(payload.get("secret"), PROTECTED_MARKER)

    def test_wrong_password_does_not_sign_in(self) -> None:
        rejected = self.client.login(USERNAME, "not-the-password")
        self.assertEqual(rejected.status, 401)
        self.assertFalse(any(c.name == "demo.auth" for c in self.client.jar))
        self.assertNotEqual(self.client.get_secret().status, 200)


if __name__ == "__main__":
    unittest.main()
