import logging
import os
import re
import time

import random
import base64
import sqlite3
from datetime import datetime
from selenium import webdriver
from selenium.webdriver import ActionChains
from selenium.webdriver.edge.service import Service as EdgeService
from webdriver_manager.microsoft import EdgeChromiumDriverManager
from selenium.webdriver.firefox.service import Service as FirefoxService
from webdriver_manager.firefox import GeckoDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait
from sensor_updator import SensorUpdator
from error_watcher import ErrorWatcher

from const import *

import numpy as np

# import cv2
from io import BytesIO
from PIL import Image
from onnx import ONNX
import platform


def base64_to_PLI(base64_str: str):
    base64_data = re.sub("^data:image/.+;base64,", "", base64_str)
    byte_data = base64.b64decode(base64_data)
    image_data = BytesIO(byte_data)
    img = Image.open(image_data)
    return img


def get_transparency_location(image):
    """获取基于透明元素裁切图片的左上角、右下角坐标

    :param image: cv2加载好的图像
    :return: (left, upper, right, lower)元组
    """
    # 1. 扫描获得最左边透明点和最右边透明点坐标
    height, width, channel = image.shape  # 高、宽、通道数
    assert channel == 4  # 无透明通道报错
    first_location = None  # 最先遇到的透明点
    last_location = None  # 最后遇到的透明点
    first_transparency = []  # 从左往右最先遇到的透明点，元素个数小于等于图像高度
    last_transparency = []  # 从左往右最后遇到的透明点，元素个数小于等于图像高度
    for y, rows in enumerate(image):
        for x, BGRA in enumerate(rows):
            alpha = BGRA[3]
            if alpha != 0:
                if (
                    not first_location or first_location[1] != y
                ):  # 透明点未赋值或为同一列
                    first_location = (x, y)  # 更新最先遇到的透明点
                    first_transparency.append(first_location)
                last_location = (x, y)  # 更新最后遇到的透明点
        if last_location:
            last_transparency.append(last_location)

    # 2. 矩形四个边的中点
    top = first_transparency[0]
    bottom = first_transparency[-1]
    left = None
    right = None
    for first, last in zip(first_transparency, last_transparency):
        if not left:
            left = first
        if not right:
            right = last
        if first[0] < left[0]:
            left = first
        if last[0] > right[0]:
            right = last

    # 3. 左上角、右下角
    upper_left = (left[0], top[1])  # 左上角
    bottom_right = (right[0], bottom[1])  # 右下角

    return upper_left[0], upper_left[1], bottom_right[0], bottom_right[1]


class DataFetcher:
    def __init__(self, username: str, password: str):
        if "PYTHON_IN_DOCKER" not in os.environ:
            import dotenv

            dotenv.load_dotenv(verbose=True)
        self._username = username
        self._password = password
        onnx_path = os.path.join(os.path.dirname(__file__), "captcha.onnx")
        self.onnx = ONNX(onnx_path)

        # 获取 ENABLE_DATABASE_STORAGE 的值，默认为 False
        self.enable_database_storage = (
            os.getenv("ENABLE_DATABASE_STORAGE", "false").lower() == "true"
        )
        self.DRIVER_IMPLICITY_WAIT_TIME = int(
            os.getenv("DRIVER_IMPLICITY_WAIT_TIME", 20)
        )
        self.RETRY_TIMES_LIMIT = int(os.getenv("RETRY_TIMES_LIMIT", 5))
        self.LOGIN_EXPECTED_TIME = int(os.getenv("LOGIN_EXPECTED_TIME", 10))
        self.RETRY_WAIT_TIME_OFFSET_UNIT = int(
            os.getenv("RETRY_WAIT_TIME_OFFSET_UNIT", 3)
        )
        self.POLL_FREQUENCY = (
            0.5  # 针对树莓派平衡：既不过快占用 CPU，又能及时捕捉 UI 变化
        )
        self.IGNORE_USER_ID = os.getenv("IGNORE_USER_ID", "xxxxx,xxxxx").split(",")

    # @staticmethod
    def _click_button(
        self, driver, button_search_type, button_search_key, wait_loading=True
    ):
        """wrapped click function, click only when the element is clickable"""
        if wait_loading:
            try:
                # 等待可能存在的 loading 遮罩消失
                WebDriverWait(driver, 5, self.POLL_FREQUENCY).until(
                    EC.invisibility_of_element_located(
                        (By.CLASS_NAME, "el-loading-mask")
                    )
                )
            except Exception:
                # 如果 5 秒内还没消失，可能是它根本没出现，或者卡住了，尝试继续
                pass

        click_element = driver.find_element(button_search_type, button_search_key)
        # logging.info(f"click_element:{button_search_key}.is_displayed() = {click_element.is_displayed()}\r")
        # logging.info(f"click_element:{button_search_key}.is_enabled() = {click_element.is_enabled()}\r")
        WebDriverWait(
            driver, self.DRIVER_IMPLICITY_WAIT_TIME, self.POLL_FREQUENCY
        ).until(EC.element_to_be_clickable(click_element))
        driver.execute_script("arguments[0].click();", click_element)

        return True

    # @staticmethod
    def _sliding_track(self, driver, distance):  # 机器模拟人工滑动轨迹
        # 获取按钮
        slider = driver.find_element(By.CLASS_NAME, "slide-verify-slider-mask-item")
        ActionChains(driver).click_and_hold(slider).perform()
        # 获取轨迹
        # tracks = _get_tracks(distance)
        # for t in tracks:
        yoffset_random = random.uniform(-2, 4)
        ActionChains(driver).move_by_offset(
            xoffset=distance, yoffset=yoffset_random
        ).perform()
        # time.sleep(0.2)
        ActionChains(driver).release().perform()

    def connect_user_db(self, user_id):
        """创建数据库集合，db_name = electricity_daily_usage_{user_id}
        :param user_id: 用户ID"""
        try:
            # 创建数据库
            DB_NAME = os.getenv("DB_NAME", "homeassistant.db")
            if "PYTHON_IN_DOCKER" in os.environ:
                DB_NAME = "/data/" + DB_NAME
            self.connect = sqlite3.connect(DB_NAME)
            self.connect.cursor()
            logging.info(f"Database of {DB_NAME} created successfully.")
            # 创建表名
            self.table_name = f"daily{user_id}"
            sql = f"""CREATE TABLE IF NOT EXISTS {self.table_name} (
                    date DATE PRIMARY KEY NOT NULL, 
                    usage REAL NOT NULL)"""
            self.connect.execute(sql)
            logging.info(f"Table {self.table_name} created successfully")

            # 创建data表名
            self.table_expand_name = f"data{user_id}"
            sql = f"""CREATE TABLE IF NOT EXISTS {self.table_expand_name} (
                    name TEXT PRIMARY KEY NOT NULL,
                    value TEXT NOT NULL)"""
            self.connect.execute(sql)
            logging.info(f"Table {self.table_expand_name} created successfully")

        # 如果表已存在，则不会创建
        except sqlite3.Error as e:
            logging.debug(f"Create db or Table error:{e}")
            return False
        return True

    def insert_data(self, data: dict):
        if self.connect is None:
            logging.error("Database connection is not established.")
            return
        # 创建索引
        try:
            sql = f"INSERT OR REPLACE INTO {self.table_name} VALUES(strftime('%Y-%m-%d','{data['date']}'),{data['usage']});"
            self.connect.execute(sql)
            self.connect.commit()
        except BaseException as e:
            logging.debug(f"Data update failed: {e}")

    def insert_expand_data(self, data: dict):
        if self.connect is None:
            logging.error("Database connection is not established.")
            return
        # 创建索引
        try:
            sql = f"INSERT OR REPLACE INTO {self.table_expand_name} VALUES('{data['name']}','{data['value']}');"
            self.connect.execute(sql)
            self.connect.commit()
        except BaseException as e:
            logging.debug(f"Data update failed: {e}")

    def _get_webdriver(self):
        if platform.system() == "Windows":
            driver = webdriver.Edge(
                service=EdgeService(EdgeChromiumDriverManager().install())
            )
        else:
            firefox_options = webdriver.FirefoxOptions()
            firefox_options.add_argument("--incognito")
            firefox_options.add_argument("--headless")
            firefox_options.add_argument("--no-sandbox")
            firefox_options.add_argument("--disable-gpu")
            firefox_options.add_argument("--disable-dev-shm-usage")
            firefox_options.add_argument("--window-size=1280,720")
            firefox_options.add_argument("--disable-software-rasterizer")
            firefox_options.add_argument("--memory-pressure-thresholds=10,50")

            # 针对树莓派优化：禁用图片加载以节省 CPU 和内存
            firefox_options.set_preference("permissions.default.image", 2)
            # 针对树莓派优化：禁用 Flash（如果还存在的话）
            firefox_options.set_preference(
                "dom.ipc.plugins.enabled.libflashplayer.so", "false"
            )

            logging.info("Open Firefox.\r")
            driver = webdriver.Firefox(
                options=firefox_options, service=FirefoxService()
            )
            # 设置页面加载超时
            driver.set_page_load_timeout(30)
            # 【关键】针对生产环境彻底弃用隐式等待，完全依赖显式等待以避免冲突导致的“死亡等待”
            driver.implicitly_wait(0)
        return driver

    @ErrorWatcher.watch
    def _login(self, driver, phone_code=False):
        try:
            logging.info(f"Open LOGIN_URL:{LOGIN_URL} ...\r")
            driver.get(LOGIN_URL)
            # 等待核心元素出现
            WebDriverWait(driver, 20, self.POLL_FREQUENCY).until(
                EC.visibility_of_element_located((By.CLASS_NAME, "user"))
            )
        except Exception as e:
            logging.error(f"Login timeout or failed: {e}")
            return False

        self._click_button(driver, By.CLASS_NAME, "user")
        logging.info("Click 'user' button done.\r")
        time.sleep(1)  # 防风控：模拟人工操作间隔
        # 仅仅在第一次尝试时点击切换到账号登录，后续重试应直接在原位刷新
        self._click_button(
            driver, By.XPATH, '//*[@id="login_box"]/div[1]/div[1]/div[2]/span'
        )
        time.sleep(1)  # 防风控：模拟人工操作间隔
        # click agree button
        self._click_button(
            driver,
            By.XPATH,
            '//*[@id="login_box"]/div[2]/div[1]/form/div[1]/div[3]/div/span[2]',
        )
        logging.info("Click the Agree option.\r")
        time.sleep(1)  # 防风控：模拟人工操作间隔
        if phone_code:
            self._click_button(
                driver, By.XPATH, '//*[@id="login_box"]/div[1]/div[1]/div[3]/span'
            )
            input_elements = driver.find_elements(By.CLASS_NAME, "el-input__inner")
            input_elements[2].send_keys(self._username)
            logging.info(f"input_elements username : {self._username}\r")
            self._click_button(
                driver,
                By.XPATH,
                '//*[@id="login_box"]/div[2]/div[2]/form/div[1]/div[2]/div[2]/div/a',
            )
            code = input("Input your phone verification code: ")
            input_elements[3].send_keys(code)
            logging.info(f"input_elements verification code: {code}.\r")
            # click login button
            self._click_button(
                driver,
                By.XPATH,
                '//*[@id="login_box"]/div[2]/div[2]/form/div[2]/div/button/span',
            )
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT * 2)
            logging.info("Click login button.\r")

            return True
        else:
            # input username and password
            input_elements = driver.find_elements(By.CLASS_NAME, "el-input__inner")
            input_elements[0].send_keys(self._username)
            logging.info(f"input_elements username : {self._username}\r")
            time.sleep(0.5)  # 防风控：输入账号后短暂等待
            input_elements[1].send_keys(self._password)
            logging.info(f"input_elements password : {self._password}\r")
            time.sleep(1)  # 防风控：输入密码后等待

            # click login button
            self._click_button(driver, By.CLASS_NAME, "el-button.el-button--primary")
            logging.info("Click login button.\r")
            # sometimes ddddOCR may fail, so add retry logic)
            for retry_times in range(1, self.RETRY_TIMES_LIMIT + 1):
                # 移除此处循环内的 tab 切换点击，它可能导致登录框重置或消失
                # get canvas image
                background_JS = 'return document.getElementById("slideVerify").childNodes[0].toDataURL("image/png");'
                # targe_JS = 'return document.getElementsByClassName("slide-verify-block")[0].toDataURL("image/png");'
                # get base64 image data
                im_info = driver.execute_script(background_JS)
                background = im_info.split(",")[1]
                background_image = base64_to_PLI(background)
                logging.info(f"Get electricity canvas image successfully.\r")
                distance = self.onnx.get_distance(background_image)
                logging.info(f"Image CaptCHA distance is {distance}.\r")

                self._sliding_track(driver, round(distance * 1.06))  # 1.06是补偿

                # [树莓派优化] 替换原来的 time.sleep(2)。
                # 给足 10 秒等待后端验证和页面跳转。如果 10 秒内 URL 变了，立即返回成功；
                # 如果 10 秒后还在老 URL，才判定为失败。
                try:
                    WebDriverWait(driver, 10, self.POLL_FREQUENCY).until(
                        EC.url_changes(LOGIN_URL)
                    )
                    return True  # URL 变了，说明登录成功
                except Exception:
                    # 获取超时，说明 URL 没变，认定为验证失败
                    pass

                if driver.current_url == LOGIN_URL:  # if login not success
                    try:
                        logging.info(
                            f"Sliding CAPTCHA recognition failed and reloaded.\r"
                        )
                        self._click_button(
                            driver, By.CLASS_NAME, "el-button.el-button--primary"
                        )
                        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT * 2)
                        continue
                    except Exception:
                        logging.debug(
                            f"Login failed, maybe caused by invalid captcha, {self.RETRY_TIMES_LIMIT - retry_times} retry times left."
                        )
                else:
                    return True
            logging.error(
                f"Login failed, maybe caused by Sliding CAPTCHA recognition failed"
            )
        return False

        raise Exception(
            "Login failed, maybe caused by 1.incorrect phone_number and password, please double check. or 2. network, please mnodify LOGIN_EXPECTED_TIME in .env and run docker compose up --build."
        )

    def fetch(self):
        """main logic here"""

        driver = self._get_webdriver()
        ErrorWatcher.instance().set_driver(driver)

        logging.info("Webdriver initialized.")
        updator = SensorUpdator()

        try:
            if os.getenv("DEBUG_MODE", "false").lower() == "true":
                if self._login(driver, phone_code=True):
                    logging.info("login successed !")
                else:
                    logging.info("login unsuccessed !")
                    raise Exception("login unsuccessed")
            else:
                if self._login(driver):
                    logging.info("login successed !")
                else:
                    logging.info("login unsuccessed !")
                    raise Exception("login unsuccessed")
        except Exception as e:
            logging.error(
                f"Webdriver quit abnormly, reason: {e}. {self.RETRY_TIMES_LIMIT} retry times left."
            )
            driver.quit()
            return

        logging.info(f"Login successfully on {LOGIN_URL}")

        # 登录成功后先跳转到余额页面，确保户号下拉菜单可用
        logging.info(f"Navigating to BALANCE_URL to load user dropdown...")
        try:
            driver.get(BALANCE_URL)
            WebDriverWait(
                driver, self.DRIVER_IMPLICITY_WAIT_TIME, self.POLL_FREQUENCY
            ).until(EC.presence_of_element_located((By.CLASS_NAME, "el-dropdown")))
        except Exception as e:
            logging.warning(
                f"Failed to navigate to BALANCE_URL: {e}, will try to get userid anyway."
            )

        logging.info(f"Try to get the userid list")
        import importlib
        import scraper_utils

        # Initial fetch attempt
        user_id_list = scraper_utils.get_user_ids(
            driver, self.DRIVER_IMPLICITY_WAIT_TIME, self.POLL_FREQUENCY
        )

        # Interactive retry loop to avoid re-login
        while not user_id_list:
            logging.error(
                "Failed to get user id list. Entering interactive debug mode."
            )
            print("\n" + "!" * 50)
            print("ERROR: Could not fetch user IDs.")
            print("Options:")
            print("  [r] Retry fetching user IDs (refresh page)")
            print("  [u] Update/Reload code (updates scraper_utils.py)")
            print("  [d] Dump page source to 'debug_manual.html'")
            print("  [i] Inspect (just wait 60s)")
            print("  [q] Quit (closes browser)")
            choice = input("Enter choice: ").strip().lower()

            if choice == "r":
                logging.info("User chose to retry...")
                driver.refresh()
                time.sleep(5)
                user_id_list = scraper_utils.get_user_ids(
                    driver, self.DRIVER_IMPLICITY_WAIT_TIME, self.POLL_FREQUENCY
                )
            elif choice == "u":
                logging.info("Reloading scraper_utils module...")
                importlib.reload(scraper_utils)
                logging.info("Module reloaded. Retrying...")
                driver.refresh()
                time.sleep(5)
                # Use the reloaded module
                user_id_list = scraper_utils.get_user_ids(
                    driver, self.DRIVER_IMPLICITY_WAIT_TIME, self.POLL_FREQUENCY
                )
            elif choice == "d":
                with open("debug_manual.html", "w", encoding="utf-8") as f:
                    f.write(driver.page_source)
                print("Saved to debug_manual.html")
            elif choice == "i":
                print("Waiting 60 seconds... you can inspect the browser if visible.")
                time.sleep(60)
            elif choice == "q":
                break

        if not user_id_list:
            logging.error("Failed to get user id list, and user chose to quit.")
            if driver:
                driver.quit()
            return

        logging.info(
            f"Here are a total of {len(user_id_list)} userids, which are {user_id_list} among which {self.IGNORE_USER_ID} will be ignored."
        )

        for userid_index, user_id in enumerate(user_id_list):
            try:
                # switch to electricity charge balance page
                driver.get(BALANCE_URL)
                # 等待页面中的核心元素出现，避免死等
                WebDriverWait(
                    driver, self.DRIVER_IMPLICITY_WAIT_TIME, self.POLL_FREQUENCY
                ).until(EC.presence_of_element_located((By.CLASS_NAME, "num")))
                self._choose_current_userid(driver, userid_index)
                time.sleep(1)  # 切换用户后的 DOM 更新缓冲
                current_userid = self._get_current_userid(driver)
                if current_userid in self.IGNORE_USER_ID:
                    logging.info(
                        f"The user ID {current_userid} will be ignored in user_id_list"
                    )
                    continue
                else:
                    ### get data
                    (
                        balance,
                        last_daily_date,
                        last_daily_usage,
                        yearly_charge,
                        yearly_usage,
                        month_charge,
                        month_usage,
                    ) = self._get_all_data(driver, user_id, userid_index)
                    updator.update_one_userid(
                        user_id,
                        balance,
                        last_daily_date,
                        last_daily_usage,
                        yearly_charge,
                        yearly_usage,
                        month_charge,
                        month_usage,
                    )

                    time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
            except Exception as e:
                if userid_index != len(user_id_list):
                    logging.info(
                        f"The current user {user_id} data fetching failed {e}, the next user data will be fetched."
                    )
                else:
                    logging.info(f"The user {user_id} data fetching failed, {e}")
                    logging.info("Webdriver quit after fetching data successfully.")
                continue

        driver.quit()

    def _get_current_userid(self, driver):
        current_userid = driver.find_element(
            By.XPATH,
            '//*[@id="app"]/div/div/article/div/div/div[2]/div/div/div[1]/div[2]/div/div/div/div[2]/div/div[1]/div/ul/div/li[1]/span[2]',
        ).text
        return current_userid

    def _choose_current_userid(self, driver, userid_index):
        elements = driver.find_elements(By.CLASS_NAME, "button_confirm")
        if elements:
            self._click_button(
                driver,
                By.XPATH,
                f"""//*[@id="app"]/div/div[2]/div/div/div/div[2]/div[2]/div/button""",
            )
        self._click_button(driver, By.CLASS_NAME, "el-input__suffix")
        self._click_button(
            driver,
            By.XPATH,
            f"/html/body/div[2]/div[1]/div[1]/ul/li[{userid_index + 1}]/span",
        )

    def _get_all_data(self, driver, user_id, userid_index):
        balance = self._get_electric_balance(driver)
        if balance is None:
            logging.info(f"Get electricity charge balance for {user_id} failed, Pass.")
        else:
            logging.info(
                f"Get electricity charge balance for {user_id} successfully, balance is {balance} CNY."
            )
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
        # swithc to electricity usage page
        driver.get(ELECTRIC_USAGE_URL)
        # 等待页面加载完成
        WebDriverWait(
            driver, self.DRIVER_IMPLICITY_WAIT_TIME, self.POLL_FREQUENCY
        ).until(EC.presence_of_element_located((By.CLASS_NAME, "el-tabs__header")))
        self._choose_current_userid(driver, userid_index)
        time.sleep(1)
        # get data for each user id
        yearly_usage, yearly_charge = self._get_yearly_data(driver)

        if yearly_usage is None:
            logging.error(f"Get year power usage for {user_id} failed, pass")
        else:
            logging.info(
                f"Get year power usage for {user_id} successfully, usage is {yearly_usage} kwh"
            )
        if yearly_charge is None:
            logging.error(f"Get year power charge for {user_id} failed, pass")
        else:
            logging.info(
                f"Get year power charge for {user_id} successfully, yealrly charge is {yearly_charge} CNY"
            )

        # 按月获取数据
        month, month_usage, month_charge = self._get_month_usage(driver)
        if month is None:
            logging.error(f"Get month power usage for {user_id} failed, pass")
        else:
            for m in range(len(month)):
                logging.info(
                    f"Get month power charge for {user_id} successfully, {month[m]} usage is {month_usage[m]} KWh, charge is {month_charge[m]} CNY."
                )
        # get yesterday usage
        last_daily_date, last_daily_usage = self._get_yesterday_usage(driver)
        if last_daily_usage is None:
            logging.error(f"Get daily power consumption for {user_id} failed, pass")
        else:
            logging.info(
                f"Get daily power consumption for {user_id} successfully, , {last_daily_date} usage is {last_daily_usage} kwh."
            )
        if month is None:
            logging.error(f"Get month power usage for {user_id} failed, pass")

        # 新增储存用电量
        if self.enable_database_storage:
            # 将数据存储到数据库
            logging.info(
                "enable_database_storage is true, we will store the data to the database."
            )
            # 按天获取数据 7天/30天
            date, usages = self._get_daily_usage_data(driver)
            self._save_user_data(
                user_id,
                balance,
                last_daily_date,
                last_daily_usage,
                date,
                usages,
                month,
                month_usage,
                month_charge,
                yearly_charge,
                yearly_usage,
            )
        else:
            logging.info(
                "enable_database_storage is false, we will not store the data to the database."
            )

        if month_charge:
            month_charge = month_charge[-1]
        else:
            month_charge = None
        if month_usage:
            month_usage = month_usage[-1]
        else:
            month_usage = None

        return (
            balance,
            last_daily_date,
            last_daily_usage,
            yearly_charge,
            yearly_usage,
            month_charge,
            month_usage,
        )

    def _get_user_ids(self, driver):
        for attempt in range(1, 4):
            try:
                logging.info(f"Trying to get user IDs, attempt {attempt}...")
                logging.info(f"Current URL: {driver.current_url}")
                # 显式等待下拉菜单出现
                try:
                    WebDriverWait(
                        driver, self.DRIVER_IMPLICITY_WAIT_TIME, self.POLL_FREQUENCY
                    ).until(
                        EC.visibility_of_element_located((By.CLASS_NAME, "el-dropdown"))
                    )
                except Exception as wait_e:
                    logging.warning(f"Wait for el-dropdown failed: {wait_e}")
                    try:
                        with open("debug_page_source.html", "w", encoding="utf-8") as f:
                            f.write(driver.page_source)
                        logging.info("Dumped page source to debug_page_source.html")
                    except Exception:
                        pass
                    raise wait_e
                # click roll down button for user id
                self._click_button(
                    driver, By.XPATH, "//div[contains(@class, 'el-dropdown')]/span"
                )
                logging.debug(
                    f"""self._click_button(driver, By.XPATH, "//div[contains(@class, 'el-dropdown')]/span")"""
                )

                # 等待下拉内容加载
                WebDriverWait(
                    driver, self.DRIVER_IMPLICITY_WAIT_TIME, self.POLL_FREQUENCY
                ).until(
                    EC.text_to_be_present_in_element(
                        (By.XPATH, "//ul[contains(@class, 'el-dropdown-menu')]/li"), ":"
                    )
                )

                # get user id one by one
                # 使用更宽泛的选择器避免精确匹配 class 失败
                dropdown_menu = driver.find_element(
                    By.XPATH, "//ul[contains(@class, 'el-dropdown-menu')]"
                )
                userid_elements = dropdown_menu.find_elements(By.TAG_NAME, "li")

                userid_list = []
                for element in userid_elements:
                    text = element.text
                    # 确保提取到的是包含数字的有效文本
                    numbers = re.findall("[0-9]+", text)
                    if numbers:
                        userid_list.append(numbers[-1])

                if userid_list:
                    return userid_list
                else:
                    logging.warning(f"Attempt {attempt}: User ID list found empty.")

            except Exception as e:
                logging.warning(
                    f"Webdriver exception in _get_user_ids (attempt {attempt}): {e}"
                )

            # 如果失败，且不是最后一次尝试，则刷新页面重试
            if attempt < 3:
                logging.info("Refreshing page to retry...")
                try:
                    driver.refresh()
                    # 刷新后需要等待页面重新加载完毕
                    time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
                except Exception as refresh_error:
                    logging.error(f"Failed to refresh page: {refresh_error}")

        logging.error("Failed to get user id list after 3 attempts.")
        return []

    def _get_electric_balance(self, driver):
        try:
            # 等待余额数值可见
            WebDriverWait(
                driver, self.DRIVER_IMPLICITY_WAIT_TIME, self.POLL_FREQUENCY
            ).until(EC.visibility_of_element_located((By.CLASS_NAME, "num")))
            balance = driver.find_element(By.CLASS_NAME, "num").text
            balance_text = driver.find_element(By.CLASS_NAME, "amttxt").text
            if "欠费" in balance_text:
                return -float(balance)
            else:
                return float(balance)
        except Exception:
            return None

    def _get_yearly_data(self, driver):
        try:
            if datetime.now().month == 1:
                try:
                    self._click_button(
                        driver,
                        By.XPATH,
                        '//*[@id="pane-first"]/div[1]/div/div[1]/div/div/input',
                    )
                    year_val = str(datetime.now().year - 1)
                    span_element = WebDriverWait(
                        driver, self.DRIVER_IMPLICITY_WAIT_TIME, self.POLL_FREQUENCY
                    ).until(
                        EC.element_to_be_clickable(
                            (By.XPATH, f"//span[contains(text(), '{year_val}')]")
                        )
                    )
                    span_element.click()
                    time.sleep(1)
                except Exception as e:
                    logging.warning(
                        f"Failed to switch to previous year data: {e}. Continuing with current view."
                    )
            self._click_button(
                driver,
                By.XPATH,
                "//div[@class='el-tabs__nav is-top']/div[@id='tab-first']",
            )
            # wait for data displayed
            WebDriverWait(
                driver, self.DRIVER_IMPLICITY_WAIT_TIME, self.POLL_FREQUENCY
            ).until(EC.visibility_of_element_located((By.CLASS_NAME, "total")))
        except Exception as e:
            logging.error(f"The yearly data get failed : {e}")
            return None, None

        # get data
        try:
            yearly_usage = driver.find_element(
                By.XPATH, "//ul[@class='total']/li[1]/span"
            ).text
        except Exception as e:
            logging.error(f"The yearly_usage data get failed : {e}")
            yearly_usage = None

        try:
            yearly_charge = driver.find_element(
                By.XPATH, "//ul[@class='total']/li[2]/span"
            ).text
        except Exception as e:
            logging.error(f"The yearly_charge data get failed : {e}")
            yearly_charge = None

        return yearly_usage, yearly_charge

    def _get_yesterday_usage(self, driver):
        """获取最近一次用电量"""
        try:
            # 点击日用电量
            self._click_button(
                driver,
                By.XPATH,
                "//div[@class='el-tabs__nav is-top']/div[@id='tab-second']",
            )
            # wait for data displayed
            WebDriverWait(
                driver, self.DRIVER_IMPLICITY_WAIT_TIME, self.POLL_FREQUENCY
            ).until(
                EC.visibility_of_element_located(
                    (
                        By.XPATH,
                        "//div[@class='el-tab-pane dayd']//div[@class='el-table__body-wrapper is-scrolling-none']/table/tbody/tr[1]/td[2]/div",
                    )
                )
            )  # 等待用电量出现
            usage_element = driver.find_element(
                By.XPATH,
                "//div[@class='el-tab-pane dayd']//div[@class='el-table__body-wrapper is-scrolling-none']/table/tbody/tr[1]/td[2]/div",
            )

            # 增加是哪一天
            date_element = driver.find_element(
                By.XPATH,
                "//div[@class='el-tab-pane dayd']//div[@class='el-table__body-wrapper is-scrolling-none']/table/tbody/tr[1]/td[1]/div",
            )
            last_daily_date = date_element.text  # 获取最近一次用电量的日期
            return last_daily_date, float(usage_element.text)
        except Exception as e:
            logging.error(f"The yesterday data get failed : {e}")
            return None

    def _get_month_usage(self, driver):
        """获取每月用电量"""

        try:
            self._click_button(
                driver,
                By.XPATH,
                "//div[@class='el-tabs__nav is-top']/div[@id='tab-first']",
            )
            if datetime.now().month == 1:
                try:
                    self._click_button(
                        driver,
                        By.XPATH,
                        '//*[@id="pane-first"]/div[1]/div/div[1]/div/div/input',
                    )
                    year_val = str(datetime.now().year - 1)
                    span_element = WebDriverWait(
                        driver, self.DRIVER_IMPLICITY_WAIT_TIME, self.POLL_FREQUENCY
                    ).until(
                        EC.element_to_be_clickable(
                            (By.XPATH, f"//span[contains(text(), '{year_val}')]")
                        )
                    )
                    span_element.click()
                    time.sleep(1)
                except Exception as e:
                    logging.warning(
                        f"Failed to switch to previous year for month data: {e}"
                    )
            # wait for month displayed
            WebDriverWait(
                driver, self.DRIVER_IMPLICITY_WAIT_TIME, self.POLL_FREQUENCY
            ).until(EC.visibility_of_element_located((By.CLASS_NAME, "total")))
            month_element = driver.find_element(
                By.XPATH,
                "//*[@id='pane-first']/div[1]/div[2]/div[2]/div/div[3]/table/tbody",
            ).text
            month_element = month_element.split("\n")
            month_element.remove("MAX")
            month_element = np.array(month_element).reshape(-1, 3)
            # 将每月的用电量保存为List
            month = []
            usage = []
            charge = []
            for i in range(len(month_element)):
                month.append(month_element[i][0])
                usage.append(month_element[i][1])
                charge.append(month_element[i][2])
            return month, usage, charge
        except Exception as e:
            logging.error(f"The month data get failed : {e}")
            return [], [], []

    # 增加获取每日用电量的函数
    def _get_daily_usage_data(self, driver):
        """储存指定天数的用电量"""
        retention_days = int(os.getenv("DATA_RETENTION_DAYS", 7))  # 默认值为7天
        self._click_button(
            driver,
            By.XPATH,
            "//div[@class='el-tabs__nav is-top']/div[@id='tab-second']",
        )
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)

        # 7 天在第一个 label, 30 天 开通了智能缴费之后才会出现在第二个, (sb sgcc)
        if retention_days == 7:
            self._click_button(
                driver, By.XPATH, "//*[@id='pane-second']/div[1]/div/label[1]/span[1]"
            )
        elif retention_days == 30:
            self._click_button(
                driver, By.XPATH, "//*[@id='pane-second']/div[1]/div/label[2]/span[1]"
            )
        else:
            logging.error(f"Unsupported retention days value: {retention_days}")
            return

        # 等待用电量的数据出现
        WebDriverWait(
            driver, self.DRIVER_IMPLICITY_WAIT_TIME, self.POLL_FREQUENCY
        ).until(
            EC.visibility_of_element_located(
                (
                    By.XPATH,
                    "//div[@class='el-tab-pane dayd']//div[@class='el-table__body-wrapper is-scrolling-none']/table/tbody/tr[1]/td[2]/div",
                )
            )
        )

        # 获取用电量的数据
        days_element = driver.find_elements(
            By.XPATH,
            "//*[@id='pane-second']/div[2]/div[2]/div[1]/div[3]/table/tbody/tr",
        )  # 用电量值列表
        date = []
        usages = []
        # 将用电量保存为字典
        for i in days_element:
            day = i.find_element(By.XPATH, "td[1]/div").text
            usage = i.find_element(By.XPATH, "td[2]/div").text
            if usage != "":
                usages.append(usage)
                date.append(day)
            else:
                logging.info(f"The electricity consumption of {usage} get nothing")
        return date, usages

    def _save_user_data(
        self,
        user_id,
        balance,
        last_daily_date,
        last_daily_usage,
        date,
        usages,
        month,
        month_usage,
        month_charge,
        yearly_charge,
        yearly_usage,
    ):
        # 连接数据库集合
        if self.connect_user_db(user_id):
            # 写入当前户号
            dic = {"name": "user", "value": f"{user_id}"}
            self.insert_expand_data(dic)
            # 写入剩余金额
            dic = {"name": "balance", "value": f"{balance}"}
            self.insert_expand_data(dic)
            # 写入最近一次更新时间
            dic = {"name": f"daily_date", "value": f"{last_daily_date}"}
            self.insert_expand_data(dic)
            # 写入最近一次更新时间用电量
            dic = {"name": f"daily_usage", "value": f"{last_daily_usage}"}
            self.insert_expand_data(dic)

            # 写入年用电量
            dic = {"name": "yearly_usage", "value": f"{yearly_usage}"}
            self.insert_expand_data(dic)
            # 写入年用电电费
            dic = {"name": "yearly_charge", "value": f"{yearly_charge} "}
            self.insert_expand_data(dic)

            if date:
                for index in range(len(date)):
                    dic = {"date": date[index], "usage": float(usages[index])}
                    # 插入到数据库
                    try:
                        self.insert_data(dic)
                        logging.info(
                            f"The electricity consumption of {usages[index]}KWh on {date[index]} has been successfully deposited into the database"
                        )
                    except Exception as e:
                        logging.debug(
                            f"The electricity consumption of {date[index]} failed to save to the database, which may already exist: {str(e)}"
                        )

            if month:
                for index in range(len(month)):
                    try:
                        dic = {
                            "name": f"{month[index]}usage",
                            "value": f"{month_usage[index]}",
                        }
                        self.insert_expand_data(dic)
                        dic = {
                            "name": f"{month[index]}charge",
                            "value": f"{month_charge[index]}",
                        }
                        self.insert_expand_data(dic)
                    except Exception as e:
                        logging.debug(
                            f"The electricity consumption of {month[index]} failed to save to the database, which may already exist: {str(e)}"
                        )
            if month_charge:
                month_charge = month_charge[-1]
            else:
                month_charge = None

            if month_usage:
                month_usage = month_usage[-1]
            else:
                month_usage = None
            # 写入本月电量
            dic = {"name": f"month_usage", "value": f"{month_usage}"}
            self.insert_expand_data(dic)
            # 写入本月电费
            dic = {"name": f"month_charge", "value": f"{month_charge}"}
            self.insert_expand_data(dic)
            # dic = {'date': month[index], 'usage': float(month_usage[index]), 'charge': float(month_charge[index])}
            self.connect.close()
        else:
            logging.info(
                "The database creation failed and the data was not written correctly."
            )
            return


if __name__ == "__main__":
    with open("bg.jpg", "rb") as f:
        test1 = f.read()
        print(type(test1))
        print(test1)
