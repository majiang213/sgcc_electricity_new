
    def _get_user_ids(self, driver):
        for attempt in range(1, 4):
            try:
                logging.info(f"Trying to get user IDs, attempt {attempt}...")
                logging.info(f"Current URL: {driver.current_url}")
                
                # Check if we are on the right page
                # If we are not on BALANCE_URL, try to go there
                if "userAcc" not in driver.current_url and attempt > 1:
                     logging.info("Not on userAcc page, navigating...")
                     driver.get(BALANCE_URL)

                # 显式等待下拉菜单出现
                try:
                    WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME, self.POLL_FREQUENCY).until(
                        EC.visibility_of_element_located((By.CLASS_NAME, "el-dropdown"))
                    )
                except Exception as wait_e:
                    logging.warning(f"Wait for el-dropdown failed: {wait_e}")
                    # Dump page source for debugging
                    with open("debug_page_source.html", "w", encoding="utf-8") as f:
                        f.write(driver.page_source)
                    logging.info("Dumped page source to debug_page_source.html")
                    raise wait_e

                # click roll down button for user id
                # ... same as before ...
                self._click_button(
                    driver, By.XPATH, "//div[contains(@class, 'el-dropdown')]/span"
                )
                
                # ... rest of the code ...
                WebDriverWait(
                    driver, self.DRIVER_IMPLICITY_WAIT_TIME, self.POLL_FREQUENCY
                ).until(
                    EC.text_to_be_present_in_element(
                        (By.XPATH, "//ul[contains(@class, 'el-dropdown-menu')]/li"), ":"
                    )
                )

                dropdown_menu = driver.find_element(By.XPATH, "//ul[contains(@class, 'el-dropdown-menu')]")
                userid_elements = dropdown_menu.find_elements(By.TAG_NAME, "li")
                
                userid_list = []
                for element in userid_elements:
                    text = element.text
                    numbers = re.findall("[0-9]+", text)
                    if numbers:
                        userid_list.append(numbers[-1])
                
                if userid_list:
                    return userid_list
                else:
                    logging.warning(f"Attempt {attempt}: User ID list found empty.")

            except Exception as e:
                logging.warning(f"Webdriver exception in _get_user_ids (attempt {attempt}): {e}")
            
            if attempt < 3:
                logging.info("Refreshing page to retry...")
                try:
                    driver.refresh()
                    time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
                except Exception as refresh_error:
                    logging.error(f"Failed to refresh page: {refresh_error}")

        logging.error("Failed to get user id list after 3 attempts.")
        return []
