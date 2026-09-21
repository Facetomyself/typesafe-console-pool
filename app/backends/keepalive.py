"""Console keepalive: reopen the account profile over the same sticky region."""

from __future__ import annotations

import time

from app.backends.automation import (
    LOGIN_URL,
    _accept_tos,
    _finish_setup,
    _logged_in,
    _on_setup,
    _wait_url,
)
from app.backends.base import RegisterResult, utc_now
from app.config import Settings
from app.services.otp import poll_otp


class KeepaliveBackend:
    name = "automation"

    def __init__(self, settings: Settings):
        self.settings = settings

    def keepalive(self, mailbox: dict[str, str], proxy_url: str, job_id: str, account_id: str) -> RegisterResult:
        from ruyipage import FirefoxOptions, FirefoxPage

        email = mailbox["email"]
        profile = self.settings.profiles_dir / f"account-{account_id}"
        profile.mkdir(parents=True, exist_ok=True)
        opts = FirefoxOptions()
        opts.set_browser_path(str(self.settings.firefox_path))
        opts.quick_start(user_dir=str(profile), proxy=proxy_url, close_on_exit=True)
        opts.set_argument("about:blank")
        page = FirefoxPage(opts)
        try:
            page.get("https://console.typesafe.ai/", timeout=45)
            time.sleep(1.2)
            if _logged_in(page.url) and not _on_setup(page.url):
                return RegisterResult(email=email, status="kept", registered_at=utc_now())
            if _on_setup(page.url):
                _accept_tos(page)
                _finish_setup(page)
                return RegisterResult(email=email, status="kept", registered_at=utc_now())
            page.get(LOGIN_URL, timeout=45)
            time.sleep(1)
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
            if _on_setup(page.url):
                _accept_tos(page)
                _finish_setup(page)
            if not _logged_in(page.url):
                raise RuntimeError(f"keepalive did not reach console: {page.url}")
            return RegisterResult(email=email, status="kept", registered_at=utc_now())
        finally:
            try:
                page.browser.quit()
            except Exception:
                pass
