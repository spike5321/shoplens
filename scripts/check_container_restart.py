"""Real Compose down/up check with one retained Cookie jar, no model calls."""

import argparse
import http.cookiejar
import json
import subprocess
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="shoplens")
    args = parser.parse_args()
    try:
        ready = subprocess.run(["docker", "info"], capture_output=True, check=False)
    except FileNotFoundError:
        print("NOT_RUN: Docker CLI is unavailable")
        return 2
    if ready.returncode:
        print("NOT_RUN: Docker engine is unavailable")
        return 2
    compose = ["docker", "compose", "-p", args.project, "--env-file", ".local/compose.env"]
    browser = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )

    def request(path, body=None, csrf=None):
        req = urllib.request.Request(
            "http://127.0.0.1:5173" + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Content-Type": "application/json",
                **({"x-csrf-token": csrf} if csrf else {}),
            },
        )
        with browser.open(req, timeout=10) as response:
            return json.load(response)

    session = request("/api/session")
    if session["provider"] != "offline":
        print("FAIL: restart check requires the offline provider")
        return 1
    run = request("/api/runs", {"question": "9月各渠道实付金额"}, session["csrf_token"])
    run_id = run["run_id"]
    for _ in range(60):
        before = request(f"/api/runs/{run_id}")
        if before["status"] != "running":
            break
        time.sleep(0.5)
    else:
        print("FAIL: run timed out before restart")
        return 1
    if before["status"] != "succeeded":
        print("FAIL: run did not succeed before restart")
        return 1
    subprocess.run([*compose, "down"], check=True)
    subprocess.run([*compose, "up", "-d", "--wait", "--wait-timeout", "120"], check=True)
    after = request(f"/api/runs/{run_id}")
    history = request("/api/runs")["runs"]
    ok = (
        after["status"] == "succeeded"
        and after["report"] == before["report"]
        and after["model_calls"] == before["model_calls"]
        and any(item["run_id"] == run_id for item in history)
    )
    print(
        f"{'PASS' if ok else 'FAIL'}: Compose down/up preserves Cookie access, report, history and call count."
    )
    return int(not ok)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"FAIL: container restart check stopped ({type(error).__name__})")
        raise SystemExit(1) from None
