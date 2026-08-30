"""
Breeze (ICICI Direct) login automation.

Opens the Breeze API login page, fills in credentials, waits for the user
to enter the mobile OTP, then captures the `apisession` token from the
post-login redirect and writes it into .env as BREEZE_API_SESSION.
"""

import argparse
import logging
import os
import sys
import time
import urllib.parse as urlparse

from dotenv import load_dotenv, set_key
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("breeze_inspect")

ENV_PATH = os.path.join(os.getcwd(), ".env")

LOGIN_URL_TMPL = "https://api.icicidirect.com/apiuser/login?api_key={api_key}"
FORM_WAIT_SECONDS = 20
OTP_REDIRECT_TIMEOUT_SECONDS = 300  # 5 min to enter OTP on mobile
OTP_POLL_INTERVAL_SECONDS = 1

USERID_XPATH = "//input[contains(@placeholder,'User') or contains(@name,'user') or @type='text']"
PASSWORD_XPATH = "//input[contains(@placeholder,'Password') or @type='password']"
SUBMIT_XPATH = "//button | //input[@type='submit']"


def load_credentials() -> tuple[str, str, str]:
    load_dotenv(ENV_PATH)

    required = {
        "BREEZE_API_KEY": os.getenv("BREEZE_API_KEY"),
        "BREEZE_USER_ID": os.getenv("BREEZE_USER_ID"),
        "BREEZE_PASSWORD": os.getenv("BREEZE_PASSWORD"),
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        log.error("Missing env vars: %s", ", ".join(missing))
        sys.exit(1)

    api_key = required["BREEZE_API_KEY"].strip()
    return api_key, required["BREEZE_USER_ID"], required["BREEZE_PASSWORD"]


def build_driver(headless: bool) -> webdriver.Chrome:
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
    else:
        options.add_experimental_option("detach", True)

    return webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options,
    )


def fill_login_form(driver: webdriver.Chrome, user_id: str, password: str) -> None:
    wait = WebDriverWait(driver, FORM_WAIT_SECONDS)
    try:
        log.info("Filling Breeze User ID...")
        userid_field = wait.until(EC.presence_of_element_located((By.XPATH, USERID_XPATH)))
        userid_field.send_keys(user_id)

        log.info("Filling password...")
        password_field = wait.until(EC.presence_of_element_located((By.XPATH, PASSWORD_XPATH)))
        password_field.send_keys(password)

        log.info("Submitting login...")
        submit_button = wait.until(EC.element_to_be_clickable((By.XPATH, SUBMIT_XPATH)))
        submit_button.click()
    except Exception as exc:
        log.warning("Automatic fill failed (%s). Login page may have changed — continue manually.", exc)


def wait_for_api_session(driver: webdriver.Chrome, timeout: int) -> str | None:
    log.info("=" * 70)
    log.info("WAITING FOR MOBILE OTP — enter OTP on your phone")
    log.info("Waiting automatically for Breeze redirect (timeout: %ss)...", timeout)
    log.info("=" * 70)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current_url = driver.current_url
        if "apisession=" in current_url.lower():
            log.info("Redirect detected: %s", current_url)
            params = urlparse.parse_qs(urlparse.urlparse(current_url).query)
            return params.get("apisession", [None])[0]
        time.sleep(OTP_POLL_INTERVAL_SECONDS)

    log.error("Timed out after %ss waiting for OTP redirect.", timeout)
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Breeze login automation")
    parser.add_argument("--headless", action="store_true", help="Run Chrome headless (you'll still need another way to enter the OTP)")
    parser.add_argument("--timeout", type=int, default=OTP_REDIRECT_TIMEOUT_SECONDS, help="Seconds to wait for OTP redirect")
    args = parser.parse_args()

    api_key, user_id, password = load_credentials()
    login_url = LOGIN_URL_TMPL.format(api_key=urlparse.quote(api_key))

    log.info("Opening Breeze login...")
    log.info(login_url)

    driver = build_driver(headless=args.headless)
    try:
        driver.get(login_url)
        fill_login_form(driver, user_id, password)
        api_session = wait_for_api_session(driver, args.timeout)

        if api_session:
            set_key(ENV_PATH, "BREEZE_API_SESSION", api_session)
            log.info("=" * 60)
            log.info("SUCCESS — BREEZE_API_SESSION saved (length: %d)", len(api_session))
            log.info("=" * 60)
        else:
            log.error("apisession not found")
            sys.exit(1)
    finally:
        if args.headless:
            driver.quit()


if __name__ == "__main__":
    main()
