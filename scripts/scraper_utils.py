import logging
import re
import time
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait


def click_button(
    driver,
    button_search_type,
    button_search_key,
    wait_time,
    poll_frequency,
    wait_loading=True,
):
    """Wrapped click function"""
    if wait_loading:
        try:
            WebDriverWait(driver, 5, poll_frequency).until(
                EC.invisibility_of_element_located((By.CLASS_NAME, "el-loading-mask"))
            )
        except Exception:
            pass

    click_element = driver.find_element(button_search_type, button_search_key)
    WebDriverWait(driver, wait_time, poll_frequency).until(
        EC.element_to_be_clickable(click_element)
    )
    driver.execute_script("arguments[0].click();", click_element)


def get_user_ids(driver, wait_time, poll_frequency, retry_limit=3):
    logging.info("Calling get_user_ids from scraper_utils (v2 - el-select)")
    for attempt in range(1, retry_limit + 1):
        try:
            logging.info(f"Trying to get user IDs, attempt {attempt}...")
            logging.info(f"Current URL: {driver.current_url}")

            # 1. Wait for el-select to be present
            try:
                WebDriverWait(driver, wait_time, poll_frequency).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "el-select"))
                )
            except Exception as wait_e:
                logging.warning(
                    f"Wait for el-select failed: {wait_e}. Trying to find by ID span directly."
                )
                # If select not found, maybe just try to find the ID on page?
                pass

            # 2. Try to get ID from static text FIRST (most reliable if single user)
            # XPath: //span[contains(text(), '用电户号')]/following-sibling::span
            page_ids = []
            try:
                # Use a slightly loose match for '用电户号'
                labels = driver.find_elements(
                    By.XPATH, "//span[contains(text(), '用电户号')]"
                )
                for label in labels:
                    # Logic: label is "用电户号:", sibling has value " 130077... "
                    # Or parent has it.
                    # Based on HTML: <li class="righ"><span>用电户号:</span><span> 130077... </span></li>
                    try:
                        # find sibling
                        sibling = label.find_element(
                            By.XPATH, "following-sibling::span"
                        )
                        txt = sibling.text
                        nums = re.findall(r"\d+", txt)
                        if nums:
                            page_ids.append(nums[0])
                    except:
                        pass
            except Exception as e:
                logging.warning(f"Failed to scrape ID from page text: {e}")

            if page_ids:
                logging.info(f"Found IDs from page text: {page_ids}")
                # If we found IDs on page, we might just return them if we assume single user or pre-selected.
                # However, to be thorough, let's try the dropdown too.

            # 3. Try to click el-select to open dropdown
            dropdown_ids = []
            try:
                # Find the input wrapper for the select
                # <div class="el-select"> ... <input ... class="el-input__inner"> ... </div>
                select_input = driver.find_element(
                    By.CSS_SELECTOR, ".el-select .el-input__inner"
                )

                # Click it
                driver.execute_script("arguments[0].click();", select_input)

                # Wait for dropdown list
                WebDriverWait(driver, 5, poll_frequency).until(
                    EC.visibility_of_element_located(
                        (By.CLASS_NAME, "el-select-dropdown__list")
                    )
                )

                # Get items
                items = driver.find_elements(By.CLASS_NAME, "el-select-dropdown__item")
                for item in items:
                    txt = item.text
                    nums = re.findall(r"\d+", txt)
                    if nums:
                        dropdown_ids.append(nums[-1])
                    else:
                        # If no numbers in dropdown options (e.g. aliases), but we have page_ids,
                        # maybe we can't map them easily without selecting.
                        # For now, if dropdown gives no IDs, we rely on page_ids.
                        logging.info(f"Dropdown item '{txt}' has no numbers.")
            except Exception as e:
                logging.warning(f"Failed to interact with dropdown: {e}")

            # Combine results
            # If we found IDs in dropdown, trust them (likely multi-user capable).
            # If not, fall back to page_ids (likely single user or alias mode).
            if dropdown_ids:
                return list(set(dropdown_ids))
            elif page_ids:
                return list(set(page_ids))
            else:
                logging.warning(f"Attempt {attempt}: User ID list found empty.")

        except Exception as e:
            logging.warning(
                f"Webdriver exception in get_user_ids (attempt {attempt}): {e}"
            )

        if attempt < retry_limit:
            logging.info("Refreshing page to retry...")
            try:
                driver.refresh()
                time.sleep(3)  # Wait for refresh
            except Exception as refresh_error:
                logging.error(f"Failed to refresh page: {refresh_error}")

    logging.error(f"Failed to get user id list after {retry_limit} attempts.")
    return []
