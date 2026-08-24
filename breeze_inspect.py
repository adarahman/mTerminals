import os
import time
import urllib.parse as urlparse

from dotenv import load_dotenv, set_key

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager


# --------------------------------------------------
# ENV
# --------------------------------------------------

ENV_PATH = os.path.join(os.getcwd(), ".env")

load_dotenv(ENV_PATH)


API_KEY = os.getenv("BREEZE_API_KEY")
USER_ID = os.getenv("BREEZE_USER_ID")
PASSWORD = os.getenv("BREEZE_PASSWORD")


missing = []

for x, v in {
    "BREEZE_API_KEY": API_KEY,
    "BREEZE_USER_ID": USER_ID,
    "BREEZE_PASSWORD": PASSWORD,
}.items():
    if not v:
        missing.append(x)

if missing:
    print("Missing:", missing)
    raise SystemExit(1)


API_KEY = API_KEY.strip()


# --------------------------------------------------
# LOGIN URL
# --------------------------------------------------

login_url = (
    "https://api.icicidirect.com/apiuser/login"
    f"?api_key={urlparse.quote(API_KEY)}"
)


print("Opening Breeze login...")
print(login_url)


# --------------------------------------------------
# CHROME
# --------------------------------------------------

options = webdriver.ChromeOptions()
options.add_experimental_option(
    "detach",
    True
)

driver = webdriver.Chrome(
    service=Service(
        ChromeDriverManager().install()
    ),
    options=options
)


driver.get(login_url)


wait = WebDriverWait(driver, 20)


try:

    print("Filling Breeze User ID...")


    # Breeze username field
    userid = wait.until(
        EC.presence_of_element_located(
            (
                By.XPATH,
                "//input[contains(@placeholder,'User') or contains(@name,'user') or @type='text']"
            )
        )
    )

    userid.send_keys(USER_ID)


    print("Filling Password...")


    password = wait.until(
        EC.presence_of_element_located(
            (
                By.XPATH,
                "//input[contains(@placeholder,'Password') or @type='password']"
            )
        )
    )


    password.send_keys(PASSWORD)


    print("Submitting login...")


    button = wait.until(
        EC.element_to_be_clickable(
            (
                By.XPATH,
                "//button | //input[@type='submit']"
            )
        )
    )

    button.click()


except Exception as e:

    print("Automatic fill failed:")
    print(e)

    print(
        "Login page may have changed. Continue manually."
    )


# --------------------------------------------------
# OTP WAIT
# --------------------------------------------------

print("\n")
print("=" * 70)
print("WAITING FOR MOBILE OTP")
print("=" * 70)
print("Enter OTP on mobile")
print("Waiting automatically for Breeze redirect...")
print("=" * 70)


# --------------------------------------------------
# Wait automatically for redirect
# --------------------------------------------------

while True:

    current_url = driver.current_url

    if "apisession=" in current_url.lower():
        break

    time.sleep(1)


print("\nRedirect detected:")
print(current_url)


# --------------------------------------------------
# Extract Breeze API Session
# --------------------------------------------------

parsed = urlparse.urlparse(current_url)

params = urlparse.parse_qs(parsed.query)

api_session = params.get(
    "apisession",
    [None]
)[0]


if api_session:

    set_key(
        ENV_PATH,
        "BREEZE_API_SESSION",
        api_session
    )

    print("\n" + "=" * 60)
    print("✅ SUCCESS")
    print("BREEZE_API_SESSION saved")
    print("Length:", len(api_session))
    print("=" * 60)

else:

    print("❌ apisession not found")