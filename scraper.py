"""
GITAM ERP Scraper — Selenium + OCR CAPTCHA solver
--------------------------------------------------
Automatically logs in to GITAM, solves CAPTCHA using OCR,
scrapes assignments and quizzes, adds them to DeadlineAI.

Install:
    pip install selenium webdriver-manager pytesseract Pillow requests beautifulsoup4

Also install Tesseract OCR:
    Download from: https://github.com/UB-Mannheim/tesseract/wiki
    Install with default settings
"""

import os
import re
import time
import requests
from datetime import datetime
from io import BytesIO
from dotenv import load_dotenv

load_dotenv()

DEADLINE_AI_URL  = "http://127.0.0.1:8000"
GITAM_LOGIN_URL  = "https://login.gitam.edu/Login.aspx"
GITAM_DASH_URL   = "https://glearn.gitam.edu/Student/std_dashboard_main"
GITAM_ASSIGN_URL = "https://glearn.gitam.edu/Student/Assignments"
GITAM_QUIZ_URL   = "https://glearn.gitam.edu/Student/Quizzes"
TESSERACT_PATH   = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# ── SETUP SELENIUM ───────────────────────────────────────────────────────────

def get_driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    options = webdriver.ChromeOptions()
    # options.add_argument("--headless")  # uncomment to run in background
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,800")

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )
    return driver

# ── SOLVE CAPTCHA WITH OCR ───────────────────────────────────────────────────

def solve_captcha(driver) -> str:
    try:
        import pytesseract
        from PIL import Image, ImageFilter, ImageEnhance
        from selenium.webdriver.common.by import By

        pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH

        # find captcha image
        captcha_img = driver.find_element(By.CSS_SELECTOR, "img[src*='captcha'], .captcha-img, #captcha-image, img[alt*='captcha'], img[alt*='CAPTCHA']")
        location    = captcha_img.location
        size        = captcha_img.size

        # screenshot the captcha area
        screenshot = driver.get_screenshot_as_png()
        img         = Image.open(BytesIO(screenshot))

        # crop just the captcha
        left   = location["x"]
        top    = location["y"]
        right  = left + size["width"]
        bottom = top  + size["height"]
        captcha_crop = img.crop((left, top, right, bottom))

        # enhance for better OCR
        captcha_crop = captcha_crop.convert("L")                          # grayscale
        captcha_crop = captcha_crop.resize((captcha_crop.width * 3, captcha_crop.height * 3))  # upscale
        captcha_crop = ImageEnhance.Contrast(captcha_crop).enhance(2.0)  # boost contrast
        captcha_crop = captcha_crop.filter(ImageFilter.SHARPEN)           # sharpen

        # OCR — only digits
        text = pytesseract.image_to_string(
            captcha_crop,
            config="--psm 8 --oem 3 -c tessedit_char_whitelist=0123456789"
        )
        captcha_text = re.sub(r"[^0-9]", "", text).strip()
        print(f"CAPTCHA solved: {captcha_text}")
        return captcha_text

    except Exception as e:
        print(f"CAPTCHA solve error: {e}")
        # if OCR fails ask user
        return input("Could not auto-solve CAPTCHA. Please enter it manually: ").strip()

# ── LOGIN ────────────────────────────────────────────────────────────────────

def login(driver) -> bool:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    username = os.getenv("GITAM_USERNAME")
    password = os.getenv("GITAM_PASSWORD")

    if not username or not password:
        print("ERROR: Add GITAM_USERNAME and GITAM_PASSWORD to your .env file")
        return False

    print(f"Logging in as {username}...")
    driver.get(GITAM_LOGIN_URL)
    time.sleep(2)

    try:
        wait = WebDriverWait(driver, 10)

        # fill username
        user_field = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR,
            "input[type='text'], input[name*='user'], input[id*='user'], input[placeholder*='user'], input[placeholder*='User']"
        )))
        user_field.clear()
        user_field.send_keys(username)

        # fill password
        pass_field = driver.find_element(By.CSS_SELECTOR,
            "input[type='password']"
        )
        pass_field.clear()
        pass_field.send_keys(password)

        time.sleep(1)

        # solve captcha
        captcha_text = solve_captcha(driver)

        # fill captcha
        captcha_field = driver.find_element(By.CSS_SELECTOR,
            "input[placeholder*='CAPTCHA'], input[placeholder*='captcha'], input[name*='captcha'], input[id*='captcha']"
        )
        captcha_field.clear()
        captcha_field.send_keys(captcha_text)

        time.sleep(0.5)

        # click login
        login_btn = driver.find_element(By.CSS_SELECTOR,
            "input[type='submit'], button[type='submit'], .login-btn, #login-btn, input[value='LOGIN']"
        )
        login_btn.click()
        time.sleep(3)

        # check if login worked
        if "login" in driver.current_url.lower():
            print("Login failed — wrong CAPTCHA or credentials. Retrying...")
            return False

        print(f"Login successful! Current URL: {driver.current_url}")
        return True

    except Exception as e:
        print(f"Login error: {e}")
        # fallback — ask user to log in manually
        print("\nCould not auto-login. Browser is open — please log in manually.")
        print("Press Enter here after you have logged in...")
        input()
        return True

# ── SCRAPE DASHBOARD ─────────────────────────────────────────────────────────

def scrape_dashboard(driver) -> list:
    from bs4 import BeautifulSoup
    items = []
    try:
        print("Scraping dashboard...")
        driver.get(GITAM_DASH_URL)
        time.sleep(3)
        soup = BeautifulSoup(driver.page_source, "html.parser")

        # target "Scheduled assignments" section specifically
        all_text = soup.get_text(separator="\n")
        lines    = [l.strip() for l in all_text.split("\n") if l.strip()]

        for i, line in enumerate(lines):
            # look for lines with month names — these are due dates
            if re.search(r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b', line):
                # get surrounding context as subject
                context = " ".join(lines[max(0,i-2):i+3])
                if any(word in context.lower() for word in ["assignment", "quiz", "lab", "exam", "test", "project"]):
                    items.append({
                        "subject":  context[:120],
                        "raw_date": line,
                        "source":   "Dashboard"
                    })

        print(f"Found {len(items)} dashboard items")
    except Exception as e:
        print(f"Dashboard error: {e}")
    return items
# ── SCRAPE ASSIGNMENTS ────────────────────────────────────────────────────────

def scrape_assignments(driver) -> list:
    from bs4 import BeautifulSoup

    items = []
    try:
        print("Scraping assignments page...")
        driver.get(GITAM_ASSIGN_URL)
        time.sleep(3)

        soup = BeautifulSoup(driver.page_source, "html.parser")

        # try all table rows
        rows = soup.select("table tbody tr")
        for row in rows:
            cells = row.find_all("td")
            text  = " ".join(c.get_text(strip=True) for c in cells)
            date_match = re.search(r'\d{1,2}[-/]\w{3}[-/]\d{4}|\d{1,2}\s+\w{3,9}\s+\d{4}|\d{1,2}[-/]\d{1,2}[-/]\d{4}', text)
            if date_match and len(text) > 5:
                items.append({
                    "subject":  text[:120],
                    "raw_date": date_match.group(),
                    "source":   "Assignments"
                })

        # also try cards
        cards = soup.find_all("div", class_=re.compile(r"card|assignment", re.I))
        for card in cards:
            text = card.get_text(separator=" ", strip=True)
            date_match = re.search(r'\d{1,2}[-/]\w{3}[-/]\d{4}|\d{1,2}\s+\w{3,9}\s+\d{4}', text)
            if date_match and len(text) > 5:
                items.append({
                    "subject":  text[:120],
                    "raw_date": date_match.group(),
                    "source":   "Assignments"
                })

        print(f"Found {len(items)} assignments")

    except Exception as e:
        print(f"Assignments error: {e}")

    return items

# ── SCRAPE QUIZZES ────────────────────────────────────────────────────────────

def scrape_quizzes(driver) -> list:
    from bs4 import BeautifulSoup

    items = []
    try:
        print("Scraping quizzes page...")
        driver.get(GITAM_QUIZ_URL)
        time.sleep(3)

        soup = BeautifulSoup(driver.page_source, "html.parser")

        rows = soup.select("table tbody tr")
        for row in rows:
            text = row.get_text(separator=" ", strip=True)
            date_match = re.search(r'\d{1,2}[-/]\w{3}[-/]\d{4}|\d{1,2}\s+\w{3,9}\s+\d{4}', text)
            if date_match and len(text) > 5:
                items.append({
                    "subject":  text[:120],
                    "raw_date": date_match.group(),
                    "source":   "Quizzes"
                })

        cards = soup.find_all("div", class_=re.compile(r"card|quiz", re.I))
        for card in cards:
            text = card.get_text(separator=" ", strip=True)
            date_match = re.search(r'\d{1,2}[-/]\w{3}[-/]\d{4}|\d{1,2}\s+\w{3,9}\s+\d{4}', text)
            if date_match and len(text) > 5:
                items.append({
                    "subject":  text[:120],
                    "raw_date": date_match.group(),
                    "source":   "Quizzes"
                })

        print(f"Found {len(items)} quizzes")

    except Exception as e:
        print(f"Quizzes error: {e}")

    return items

# ── SEND TO DEADLINEAI ────────────────────────────────────────────────────────

def send_to_deadlineai(items: list, token: str) -> list:
    added = []
    for item in items:
        try:
            message = f"{item['subject']} - due {item['raw_date']}"
            res     = requests.post(
                DEADLINE_AI_URL + "/chat",
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
                json={"message": message},
                timeout=60
            )
            data = res.json()
            if data.get("status") == "success":
                tool  = data["steps"][0]["tool"]
                if tool == "extract_deadline":
                    saved = data["final"].get("saved", [])
                    print(f"Added: {saved}")
                    added.extend(saved)
        except Exception as e:
            print(f"Send error: {e}")
    return added

# ── MAIN ──────────────────────────────────────────────────────────────────────

def run_scraper(token: str):
    print("\n" + "="*50)
    print("GITAM Scraper Starting...")
    print("="*50 + "\n")

    driver = None
    try:
        driver = get_driver()

        # try to login — retry up to 3 times for bad CAPTCHA
        logged_in = False
        for attempt in range(3):
            print(f"Login attempt {attempt + 1}/3...")
            if login(driver):
                logged_in = True
                break
            time.sleep(1)

        if not logged_in:
            print("Could not login after 3 attempts.")
            print("Browser is open — please log in manually then press Enter...")
            input()

        # scrape everything
        all_items = []
        all_items += scrape_dashboard(driver)
        all_items += scrape_assignments(driver)
        all_items += scrape_quizzes(driver)

        # remove duplicates
        seen  = set()
        unique = []
        for item in all_items:
            key = item["subject"][:30]
            if key not in seen:
                seen.add(key)
                unique.append(item)

        print(f"\nTotal unique items: {len(unique)}")

        if not unique:
            print("No items found!")
            return []

        print("Adding to DeadlineAI...")
        added = send_to_deadlineai(unique, token)
        print(f"\n✅ Done! Added {len(added)} deadlines")
        return added

    except Exception as e:
        print(f"Scraper error: {e}")
        return []
    finally:
        if driver:
            driver.quit()


if __name__ == "__main__":
    token = input("Paste your DeadlineAI token: ").strip()
    if token:
        run_scraper(token)
    else:
        print("Get token from browser: localStorage.getItem('dai_token')")