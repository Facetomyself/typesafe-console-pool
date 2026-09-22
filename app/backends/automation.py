"""ruyipage Firefox 151 registration over a Cliproxy sticky proxy."""

from __future__ import annotations

import time

from app.backends.base import RegisterResult, utc_now
from app.config import Settings
from app.services.otp import poll_otp

LOGIN_URL = "https://console.typesafe.ai/login"


class AutomationBackend:
    name = "automation"

    def __init__(self, settings: Settings):
        self.settings = settings

    def register(self, mailbox: dict[str, str], proxy_url: str, job_id: str) -> RegisterResult:
        from ruyipage import FirefoxOptions, FirefoxPage

        email = mailbox["email"]
        profile = self.settings.profiles_dir / f"job-{job_id}"
        profile.mkdir(parents=True, exist_ok=True)
        opts = FirefoxOptions()
        opts.set_browser_path(str(self.settings.firefox_path))
        opts.quick_start(user_dir=str(profile), proxy=proxy_url, close_on_exit=True)
        opts.set_argument("about:blank")
        page = FirefoxPage(opts)
        try:
            page.get(LOGIN_URL, timeout=45)
            time.sleep(1)
            if _on_setup(page.url):
                _accept_tos(page)
                _finish_setup(page)
                return RegisterResult(email=email, status="registered", registered_at=utc_now())
            email_box = page.ele("#email", timeout=10)
            if not email_box:
                raise RuntimeError("email input missing")
            email_box.input(email)
            after = utc_now()
            code_btn = page.ele("text=Email me a code instead", timeout=5)
            if not code_btn:
                raise RuntimeError("email-code button missing")
            code_btn.click()
            _wait_url(page, lambda u: "otp=true" in u, timeout=15)
            time.sleep(1)
            code = poll_otp(email, mailbox["client_id"], mailbox["refresh_token"], after)
            code_box = page.ele("#code", timeout=10)
            if not code_box:
                raise RuntimeError("code input missing")
            code_box.input(code)
            time.sleep(0.4)
            verify = page.ele("text=Verify", timeout=5)
            if not verify:
                raise RuntimeError("verify button missing")
            verify.click()
            _wait_url(page, _logged_in, timeout=20)
            time.sleep(2)
            if not _logged_in(page.url):
                raise RuntimeError(f"verify did not leave login: {page.url}")
            if "/setup/tos" in page.url:
                _accept_tos(page)
            _finish_setup(page)
            return RegisterResult(email=email, status="registered", registered_at=utc_now())
        finally:
            try:
                page.browser.quit()
            except Exception:
                pass

    def keepalive(self, mailbox: dict[str, str], proxy_url: str, job_id: str, account_id: str) -> RegisterResult:
        from app.backends.keepalive import KeepaliveBackend

        return KeepaliveBackend(self.settings).keepalive(mailbox, proxy_url, job_id, account_id)


def _logged_in(url: str) -> bool:
    return "console.typesafe.ai" in url and "/login" not in url


def _on_setup(url: str) -> bool:
    return "/setup/" in (url or "")


def _wait_url(page, pred, timeout=30) -> str:
    deadline = time.time() + timeout
    url = ""
    while time.time() < deadline:
        url = page.url
        if pred(url):
            return url
        time.sleep(0.4)
    return url


def _accept_tos(page) -> None:
    legal = page.ele("#legal", timeout=5)
    if legal:
        legal.click()
        time.sleep(0.4)
    else:
        box = page.ele("css:input[name=legalAcknowledged]", timeout=2)
        if box:
            box.click()
    cont = page.ele("text=Continue", timeout=5)
    if not cont:
        raise RuntimeError("TOS Continue missing")
    cont.click()
    _wait_url(page, lambda u: "/setup/tos" not in u, timeout=20)
    time.sleep(1.2)


def _finish_setup(page) -> None:
    for _ in range(8):
        if not _on_setup(page.url):
            break
        skipped = False
        for label in ("Skip", "Continue", "Next", "Submit", "Finish", "Get started"):
            btn = page.ele(f"text={label}", timeout=1.2)
            if btn:
                btn.click()
                skipped = True
                time.sleep(1.4)
                break
        if not skipped:
            break
    enter = page.ele("text=Enter console", timeout=3)
    if enter:
        enter.click()
        _wait_url(page, lambda u: "/hook" not in u and "/setup/" not in u, timeout=20)
        time.sleep(1.5)
