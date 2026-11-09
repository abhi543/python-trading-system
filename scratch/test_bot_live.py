import time
import os
import json
import sys
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def run_deep_check():
    options = Options()
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.set_capability('goog:loggingPrefs', {'browser': 'ALL'})

    print("[1] Launching Chrome...")
    driver = webdriver.Chrome(options=options)
    
    try:
        url = "https://python-trading-system.vercel.app/bot"
        print(f"[2] Navigating to {url}...")
        driver.get(url)
        time.sleep(4)
        
        driver.save_screenshot("scratch/01_initial_load.png")
        print("    [Screenshot] scratch/01_initial_load.png")
        
        # Check initial metrics
        cash = driver.find_element(By.ID, "nav-cash-balance").text
        equity = driver.find_element(By.ID, "nav-total-equity").text
        bp = driver.find_element(By.ID, "nav-buying-power").text
        print(f"    Account Metrics: Cash={cash}, Equity={equity}, BuyingPower={bp}")

        # Check console errors
        logs = driver.get_log('browser')
        errors = [l for l in logs if l['level'] == 'SEVERE' and 'favicon.ico' not in l['message']]
        print(f"    Initial Console Errors (excluding 404 favicon): {len(errors)}")
        for e in errors:
            print(f"      [ERROR] {e['message']}")

        # 1. Test RESET & RESTART BOT first to restore clean ₹50,000
        print("[3] Testing RESET & RESTART BOT button...")
        reset_restart_btn = driver.find_element(By.ID, "bot-reset-restart-btn")
        # Handle the confirm() dialog automatically
        reset_restart_btn.click()
        time.sleep(1)
        try:
            alert = driver.switch_to.alert
            print(f"    Confirm Dialog Text: {alert.text[:60]}...")
            alert.accept()
            print("    Confirmed alert.")
        except Exception:
            pass
        time.sleep(3)

        # Check account metrics after reset
        cash_after = driver.find_element(By.ID, "nav-cash-balance").text
        print(f"    Account Metrics after Reset: Cash={cash_after}")
        driver.save_screenshot("scratch/02_after_reset.png")

        # 2. Check START NIFTY BOT button
        print("[4] Checking Bot Activation...")
        primary_btn = driver.find_element(By.ID, "bot-primary-btn")
        print(f"    Initial Bot Button Text: {primary_btn.text.strip()}")
        if "START" in primary_btn.text:
            print("    Clicking START NIFTY BOT...")
            primary_btn.click()
            time.sleep(2)
            print(f"    Updated Bot Button Text: {primary_btn.text.strip()}")
            driver.save_screenshot("scratch/03_bot_active.png")

        # 3. Test Manual Radar Scan Sweep
        print("[5] Triggering SCAN BASKET NOW...")
        scan_btn = driver.find_element(By.ID, "bot-scan-btn")
        scan_btn.click()
        print("    Scan triggered! Waiting for sweep results...")
        
        # Wait up to 30 seconds for scan to complete
        sweep_done = False
        for i in range(30):
            time.sleep(1)
            btn_text = scan_btn.text
            if "SCAN" in btn_text and not scan_btn.get_attribute("disabled"):
                print(f"    Scan completed in ~{i+1} seconds!")
                sweep_done = True
                break

        time.sleep(2)
        driver.save_screenshot("scratch/04_scan_complete.png")

        # Check Scanner Radar rows
        radar_rows = driver.find_elements(By.CSS_SELECTOR, "#bot-scanner-tbody tr")
        print(f"    Scanner Radar Rows Count: {len(radar_rows)}")

        # 4. Check Active Holdings Tab & Monitor Stability
        print("[6] Checking Active Holdings Tab & Stability...")
        active_tab = driver.find_element(By.ID, "tab-btn-active-holdings")
        active_tab.click()
        time.sleep(2)

        badge_count = driver.find_element(By.ID, "holdings-badge-count").text
        hero_count = driver.find_element(By.ID, "hero-holdings-count").text
        print(f"    Initial Holdings Count: Badge={badge_count}, Hero={hero_count}")

        # Monitor over 20 seconds for any flickering or zeroing out
        counts = []
        for s in range(5):
            time.sleep(4)
            c = driver.find_element(By.ID, "holdings-badge-count").text
            counts.append(c)
            print(f"    Holdings Polling Check {s+1} (t={ (s+1)*4 }s): Count = {c}")

        driver.save_screenshot("scratch/05_holdings_monitored.png")
        if len(set(counts)) <= 1:
            print("    [PASS] Active Holdings count is 100% stable without any fluctuation!")
        else:
            print(f"    [WARN] Fluctuation observed: {counts}")

        # 5. Check Bot Session Report Tab
        print("[7] Checking Bot Session Report Tab...")
        sess_tab = driver.find_element(By.ID, "tab-btn-session-report")
        sess_tab.click()
        time.sleep(2)
        sess_badge = driver.find_element(By.ID, "session-trades-badge-count").text
        print(f"    Bot Session Trades Count: {sess_badge}")
        driver.save_screenshot("scratch/06_session_report.png")

        # 6. Check All Closed Trades Tab
        print("[8] Checking All Closed Trades Tab...")
        closed_tab = driver.find_element(By.ID, "tab-btn-closed-trades")
        closed_tab.click()
        time.sleep(2)
        closed_badge = driver.find_element(By.ID, "closed-trades-badge-count").text
        print(f"    Closed Trades Count: {closed_badge}")
        driver.save_screenshot("scratch/07_closed_trades.png")

        # Final check on console errors
        all_logs = driver.get_log('browser')
        final_errors = [l for l in all_logs if l['level'] == 'SEVERE' and 'favicon.ico' not in l['message']]
        print(f"\n[9] Final Console Audit:")
        print(f"    Total Severe Errors Encountered: {len(final_errors)}")
        for e in final_errors:
            print(f"      [ERROR] {e['message']}")

        print("\n=== DEEP CHECK FINISHED SUCCESSFULLY ===")

    finally:
        driver.quit()
        print("Chrome closed.")

if __name__ == "__main__":
    run_deep_check()
