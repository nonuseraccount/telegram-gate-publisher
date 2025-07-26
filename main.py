# -*- coding: utf-8 -*-
"""
Telegram Proxy Publisher - Single File Edition
==============================================

Author: Unavailable User (Conceptual Request)
Developed by: Gemini
Date: 2025-07-26
Version: 5.1.0 (Stable with Secret Cleaning)

Project Overview:
-----------------
This script is a comprehensive, single-file pipeline for fetching, processing,
and publishing Telegram proxy configurations. It is designed for robustness,
clarity, and ease of deployment. It fetches structured JSON data, cleans invalid
characters from proxy secrets, filters out previously posted proxies, generates
QR codes for new ones, and posts them as a grouped media album. It then replies
to that post with a message containing the full details and an inline keyboard
of connect buttons. It includes a runtime manager to ensure it does not exceed
execution time limits.

Directory Structure:
--------------------
- /
  - main.py (This file)
  - requirements.txt
  - data/
    - preferences.json
    - subscription_urls.json
  - output/
    - archive_proxies.json

Required Libraries:
-------------------
pip install requests qrcode[pil]
"""

import json
import logging
import os
import re
import sys
import time
import io
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

try:
    import requests
    import qrcode
    from PIL import Image
except ImportError:
    print("Error: A required library is missing.")
    print("Please install all required libraries by running: pip install requests 'qrcode[pil]'")
    sys.exit(1)

# ================================================================
# 1. LOGGER SETUP
# ================================================================

class ColorFormatter(logging.Formatter):
    """A custom logging formatter that adds color to log levels for readability."""
    GREY = "\x1b[38;20m"
    YELLOW = "\x1b[33;20m"
    RED = "\x1b[31;20m"
    BOLD_RED = "\x1b[31;1m"
    RESET = "\x1b[0m"
    FORMAT = "%(asctime)s - [%(levelname)s] - %(message)s"
    FORMATS = {
        logging.DEBUG: GREY + FORMAT + RESET,
        logging.INFO: GREY + FORMAT + RESET,
        logging.WARNING: YELLOW + FORMAT + RESET,
        logging.ERROR: RED + FORMAT + RESET,
        logging.CRITICAL: BOLD_RED + FORMAT + RESET,
    }

    def format(self, record: logging.LogRecord) -> str:
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt, datefmt="%Y-%m-%d %H:%M:%S")
        return formatter.format(record)

def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """Sets up and configures a new logger instance."""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False
    if logger.hasHandlers():
        logger.handlers.clear()
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(ColorFormatter())
    logger.addHandler(console_handler)
    return logger

# ================================================================
# 2. CONFIGURATION & RUNTIME MANAGERS
# ================================================================

class ConfigManager:
    """Handles loading all necessary configuration files."""

    def __init__(self, log: logging.Logger, config_path: Path):
        self.log = log
        self.config_path = config_path
        self.config: Dict[str, Any] = {}

    def load(self) -> Dict[str, Any]:
        """Loads the main preferences.json, injects secrets, and validates critical values."""
        self.log.info("--- Stage: Configuration Loading ---")
        if not self.config_path.exists():
            self.log.critical(f"Configuration file not found at '{self.config_path}'. Aborting.")
            sys.exit(1)

        try:
            with self.config_path.open('r', encoding='utf-8') as f:
                self.config = json.load(f)
        except json.JSONDecodeError as e:
            self.log.critical(f"Error parsing '{self.config_path}': {e}. Aborting.")
            sys.exit(1)

        self._inject_environment_variables()
        self._validate_critical_configs()
        self.log.info("Configuration loaded successfully.")
        return self.config

    def _inject_environment_variables(self):
        """Overrides config with environment variables for security (e.g., GitHub Secrets)."""
        bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
        channel_id = os.environ.get('TELEGRAM_CHANNEL_ID')
        channel_handle = os.environ.get('TELEGRAM_CHANNEL_HANDLE')

        if bot_token:
            self.config['telegram']['bot_token'] = bot_token
            self.log.info("Loaded Telegram Bot Token from environment variable.")
        if channel_id:
            self.config['telegram']['channel_id'] = channel_id
            self.log.info("Loaded Telegram Channel ID from environment variable.")
        if channel_handle:
            self.config['posting']['channel_handle'] = channel_handle
            self.log.info("Loaded Telegram Channel Handle from environment variable.")


    def _validate_critical_configs(self):
        """Ensures that essential configuration values are present."""
        if not self.config.get('telegram', {}).get('bot_token'):
            self.log.critical("Telegram Bot Token is not configured. Please set the TELEGRAM_BOT_TOKEN environment variable/secret.")
            sys.exit(1)
        if not self.config.get('telegram', {}).get('channel_id'):
            self.log.critical("Telegram Channel ID is not configured. Please set the TELEGRAM_CHANNEL_ID environment variable/secret.")
            sys.exit(1)

class RuntimeManager:
    """Tracks script execution time to prevent timeouts."""
    def __init__(self, start_time: float, config: Dict[str, Any], log: logging.Logger):
        self.start_time = start_time
        self.log = log
        self.max_seconds = config.get('runtime', {}).get('max_execution_seconds', 3300)
        self.time_exceeded = False

    def is_time_exceeded(self) -> bool:
        """Checks if the maximum execution time has been reached."""
        if self.time_exceeded:
            return True
        elapsed_time = time.time() - self.start_time
        if elapsed_time > self.max_seconds:
            self.log.warning(f"Execution time limit of {self.max_seconds} seconds reached. Stopping operations.")
            self.time_exceeded = True
            return True
        return False

# ================================================================
# 3. UTILITY CLASSES (QRCODE GENERATOR)
# ================================================================

class QRCodeGenerator:
    """Generates QR code images from text data in memory."""
    def __init__(self, log: logging.Logger):
        self.log = log

    def generate(self, data: str) -> Optional[io.BytesIO]:
        """
        Generates a QR code for the given data string.

        Args:
            data: The string to encode in the QR code.

        Returns:
            An in-memory bytes buffer containing the PNG image, or None on failure.
        """
        if not data:
            return None
            
        try:
            qr_image = qrcode.make(data, border=2)
            img_buffer = io.BytesIO()
            qr_image.save(img_buffer, format='PNG')
            img_buffer.seek(0)  # Rewind the buffer to the beginning
            return img_buffer
        except Exception as e:
            self.log.error(f"Failed to generate QR code for data '{data[:30]}...': {e}")
            return None

# ================================================================
# 4. DATA LOADER
# ================================================================

class DataLoader:
    """Fetches proxy data from a flexible source."""
    def __init__(self, config: Dict[str, Any], log: logging.Logger, runtime: RuntimeManager):
        self.config = config
        self.log = log
        self.runtime = runtime
        self.initial_source_path = Path(self.config['paths']['subscriptions'])

    def _fetch_from_url(self, url: str) -> List[Dict[str, Any]]:
        """Fetches and parses a JSON list of proxies from a single URL."""
        self.log.info(f"Fetching proxies from URL: {url}")
        try:
            timeout = self.config.get('runtime', {}).get('request_timeout', 30)
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            proxies_from_url = response.json()
            if isinstance(proxies_from_url, list):
                self.log.info(f"Fetched {len(proxies_from_url)} proxies from {url}")
                return proxies_from_url
            else:
                self.log.warning(f"Expected a JSON list from {url}, but got {type(proxies_from_url)}. Skipping.")
                return []
        except requests.exceptions.RequestException as e:
            self.log.error(f"Error fetching from {url}: {e}")
            return []
        except json.JSONDecodeError:
            self.log.error(f"Failed to decode JSON from {url}.")
            return []

    def _read_proxy_list_from_file(self, file_path: Path) -> List[Dict[str, Any]]:
        """Reads a file that is expected to contain a direct list of proxies."""
        self.log.info(f"Reading proxy list from local file: {file_path}")
        try:
            with file_path.open('r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, list):
                self.log.info(f"Read {len(data)} proxies from {file_path}")
                return data
            else:
                self.log.warning(f"Expected a JSON list in '{file_path}', but got {type(data)}. Skipping.")
                return []
        except (json.JSONDecodeError, IOError) as e:
            self.log.error(f"Could not read or parse proxy list file '{file_path}': {e}")
            return []

    def _get_sources_from_config_file(self, file_path: Path) -> List[str]:
        """Reads a file that contains a list of subscription source strings."""
        self.log.info(f"Reading subscription sources from config file: {file_path}")
        try:
            with file_path.open('r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict) and "subscriptions" in data and isinstance(data["subscriptions"], list):
                return data["subscriptions"]
            else:
                self.log.warning(f"Expected a dict with a 'subscriptions' list in '{file_path}'.")
                return []
        except (json.JSONDecodeError, IOError) as e:
            self.log.error(f"Could not read or parse sources file '{file_path}': {e}")
            return []

    def fetch_proxies(self) -> List[Dict[str, Any]]:
        """Public method to start the fetching process."""
        self.log.info("--- Stage: Data Loading ---")
        if not self.initial_source_path.is_file():
            self.log.error(f"Initial subscription path must be a file.")
            return []

        sources = self._get_sources_from_config_file(self.initial_source_path)
        if not sources:
            self.log.warning("No subscription sources found in the config file.")
            return []
            
        self.log.info(f"Found {len(sources)} sources to process.")
        all_proxies: List[Dict[str, Any]] = []

        for source_str in sources:
            if self.runtime.is_time_exceeded():
                self.log.warning("Stopping data loading due to execution time limit.")
                break
            if source_str.lower().startswith(('http://', 'https://')):
                all_proxies.extend(self._fetch_from_url(source_str))
            else:
                local_file_path = Path(source_str)
                if local_file_path.exists() and local_file_path.is_file():
                    all_proxies.extend(self._read_proxy_list_from_file(local_file_path))
                else:
                    self.log.warning(f"Local source file '{local_file_path}' not found. Skipping.")

        self.log.info(f"Total raw proxies fetched: {len(all_proxies)}")
        return all_proxies

# ================================================================
# 5. PROXY PROCESSOR
# ================================================================

class ProxyProcessor:
    """Handles loading the archive, cleaning secrets, and filtering proxies."""
    def __init__(self, config: Dict[str, Any], log: logging.Logger):
        self.config = config
        self.log = log
        self.archive_path = Path(self.config['paths']['archive'])
        self.invalid_chars_pattern = r'[@!#$%^&*()+:"\'\[\]{}]'

    def _load_archive(self) -> Set[str]:
        """Loads the set of previously posted proxy links from the archive file."""
        self.log.info(f"Loading archive from '{self.archive_path}'...")
        if not self.archive_path.exists():
            self.log.warning("Archive file not found. Starting with an empty archive.")
            return set()
        try:
            with self.archive_path.open('r', encoding='utf-8') as f:
                archive_data = json.load(f)
            archived_links = {proxy.get('tg_link') for proxy in archive_data if proxy.get('tg_link')}
            self.log.info(f"Loaded {len(archived_links)} unique proxies from archive.")
            return archived_links
        except (json.JSONDecodeError, IOError) as e:
            self.log.error(f"Could not load or parse archive file '{self.archive_path}': {e}")
            return set()

    def _clean_string(self, input_string: str) -> str:
        """Removes invalid characters from a string."""
        if not isinstance(input_string, str):
            return ""
        return re.sub(self.invalid_chars_pattern, '', input_string)

    def find_new_proxies(self, fetched_proxies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Cleans secrets and filters the list of fetched proxies against the archive."""
        self.log.info("--- Stage: Processing and Filtering Proxies ---")
        archived_links = self._load_archive()
        new_proxies: List[Dict[str, Any]] = []
        seen_links_in_this_run: Set[str] = set()

        for proxy in fetched_proxies:
            # --- Step 1: Clean the secret field ---
            original_secret = proxy.get("secret")
            if original_secret:
                proxy["secret"] = self._clean_string(original_secret)
            
            # --- Step 2: Clean the entire tg_link field ---
            original_tg_link = proxy.get("tg_link")
            if original_tg_link:
                proxy["tg_link"] = self._clean_string(original_tg_link)

            # --- Step 3: Rebuild tg_link from components to ensure consistency ---
            ip = proxy.get("ip")
            port = proxy.get("port")
            secret = proxy.get("secret")
            if ip and port and secret:
                rebuilt_link = f"tg://proxy?server={ip}&port={port}&secret={secret}"
                if rebuilt_link != proxy.get("tg_link"):
                    self.log.debug(f"Rebuilt tg_link for proxy {ip} to ensure consistency.")
                    proxy["tg_link"] = rebuilt_link
            
            # --- Step 4: Continue with filtering logic using the cleaned link ---
            final_tg_link = proxy.get("tg_link")
            if not final_tg_link:
                self.log.debug(f"Skipping proxy with no valid 'tg_link': {proxy}")
                continue
            
            if final_tg_link not in archived_links and final_tg_link not in seen_links_in_this_run:
                new_proxies.append(proxy)
                seen_links_in_this_run.add(final_tg_link)

        self.log.info(f"Found {len(new_proxies)} new, unique proxies to post after cleaning and filtering.")
        return new_proxies

# ================================================================
# 6. TELEGRAM POSTER
# ================================================================

class TelegramPoster:
    """Formats and posts proxies to the Telegram channel with QR codes."""
    def __init__(self, config: Dict[str, Any], log: logging.Logger, runtime: RuntimeManager):
        self.config = config
        self.log = log
        self.runtime = runtime
        self.qr_generator = QRCodeGenerator(log)
        self.bot_token = self.config['telegram']['bot_token']
        self.channel_id = self.config['telegram']['channel_id']
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}"

    def _escape_markdown_v2(self, text: str) -> str:
        """Escapes MarkdownV2 special characters."""
        escape_chars = r'_*[]()~`>#+-=|{}.!'
        return re.sub(r'([%s])' % re.escape(escape_chars), r'\\\1', str(text))

    def _post_chunk_with_qrcodes(self, proxies_chunk: List[Dict[str, Any]]) -> bool:
        """
        Posts a chunk of proxies by sending a media group with QR codes, then
        replying to it with a message containing the full details and inline keyboard.
        """
        if not proxies_chunk:
            return False

        media_group = []
        files_to_upload = {}
        
        for i, proxy in enumerate(proxies_chunk):
            tg_link = proxy.get('tg_link')
            if not tg_link:
                continue

            qr_buffer = self.qr_generator.generate(tg_link)
            if not qr_buffer:
                self.log.warning(f"Skipping proxy due to QR code generation failure: {tg_link}")
                continue
            
            file_key = f"qr_code_{i}"
            files_to_upload[file_key] = ('qr_code.png', qr_buffer, 'image/png')
            media_item = {'type': 'photo', 'media': f'attach://{file_key}'}
            media_group.append(media_item)
            
        if not media_group:
            self.log.warning("No valid QR codes were generated for this chunk.")
            return False

        # --- Step 1: Send the Media Group WITHOUT a caption ---
        send_media_url = f'{self.api_url}/sendMediaGroup'
        media_payload = {'chat_id': self.channel_id, 'media': json.dumps(media_group)}
        
        try:
            media_response = requests.post(send_media_url, data=media_payload, files=files_to_upload, timeout=45)
            media_response.raise_for_status()
            self.log.info(f"Successfully posted a media group of {len(proxies_chunk)} proxies.")
            
            # --- Step 2: Send the Text and Inline Keyboard as a Reply ---
            response_data = media_response.json()
            if response_data.get('ok') and response_data.get('result'):
                reply_to_message_id = response_data['result'][0]['message_id']
                
                # Build the full text for the reply message
                caption_lines = []
                for i, proxy in enumerate(proxies_chunk):
                    ip_port = f"{proxy.get('ip', 'N/A')}:{proxy.get('port', 'N/A')}"
                    display_name = proxy.get('country_name', proxy.get('country_code', 'NA'))
                    country_flag = proxy.get('country_flag', '🏴‍☠️')
                    tg_link = proxy.get('tg_link', '')
                    ip_port_escaped = self._escape_markdown_v2(ip_port)
                    tg_link_escaped = self._escape_markdown_v2(tg_link)
                    address_line = f"🔒 *Address:* [{ip_port_escaped}]({tg_link_escaped})"
                    country_line = f"🌎 *Country:* {country_flag} {self._escape_markdown_v2(display_name)}"
                    caption_lines.append(address_line)
                    caption_lines.append(country_line)
                    if i < len(proxies_chunk) - 1:
                        caption_lines.append("")
                
                channel_handle = self.config.get('posting', {}).get('channel_handle')
                if channel_handle:
                    caption_lines.append(f"\n{self._escape_markdown_v2(channel_handle)}")
                full_text = "\n".join(caption_lines)

                # Build the inline keyboard
                inline_buttons = [{'text': "Connect", 'url': p['tg_link']} for p in proxies_chunk if p.get('tg_link')]
                keyboard = [inline_buttons[i:i + 3] for i in range(0, len(inline_buttons), 3)]
                reply_markup = json.dumps({'inline_keyboard': keyboard})

                send_keys_url = f'{self.api_url}/sendMessage'
                keys_payload = {
                    'chat_id': self.channel_id,
                    'text': full_text,
                    'parse_mode': 'MarkdownV2',
                    'reply_to_message_id': reply_to_message_id,
                    'reply_markup': reply_markup,
                    'disable_web_page_preview': True
                }
                keys_response = requests.post(send_keys_url, json=keys_payload, timeout=15)
                keys_response.raise_for_status()
                self.log.info(f"Successfully sent details and inline keyboard as a reply.")
                return True
            else:
                self.log.error("sendMediaGroup call did not return expected result for replying.")
                return False

        except requests.exceptions.RequestException as e:
            self.log.error(f"Error during posting process: {e}")
            if hasattr(e, 'response') and e.response is not None:
                self.log.error(f"Telegram API response: {e.response.text}")
            return False

    def post_all(self, proxies_to_post: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Posts all new proxies in chunks, with delays, and returns the list of successfully posted proxies."""
        self.log.info("--- Stage: Posting to Telegram ---")
        if not proxies_to_post:
            self.log.info("No new proxies to post.")
            return []

        proxies_per_post = self.config.get('posting', {}).get('proxies_per_post', 10) # Max 10 for media group
        delay_seconds = self.config.get('posting', {}).get('delay_seconds', 600)
        
        posted_proxies: List[Dict[str, Any]] = []
        
        for i in range(0, len(proxies_to_post), proxies_per_post):
            if self.runtime.is_time_exceeded():
                self.log.warning("Stopping posting due to execution time limit.")
                break
                
            chunk = proxies_to_post[i:i + proxies_per_post]
            self.log.info(f"Processing chunk {i // proxies_per_post + 1}...")
            
            success = self._post_chunk_with_qrcodes(chunk)
            if success:
                posted_proxies.extend(chunk)
                if (i + proxies_per_post) < len(proxies_to_post):
                    if self.runtime.is_time_exceeded():
                        self.log.warning("Stopping posting due to execution time limit (before delay).")
                        break
                    self.log.info(f"Waiting {delay_seconds} seconds before next post...")
                    time.sleep(delay_seconds)
            else:
                self.log.warning(f"Failed to post chunk. Skipping to next.")
        
        self.log.info(f"Finished posting. Total proxies successfully posted: {len(posted_proxies)}")
        return posted_proxies

# ================================================================
# 7. ARCHIVE MANAGER
# ================================================================

class ArchiveManager:
    """Handles saving the updated archive file."""
    def __init__(self, config: Dict[str, Any], log: logging.Logger):
        self.config = config
        self.log = log
        self.archive_path = Path(self.config['paths']['archive'])

    def update_archive(self, posted_proxies: List[Dict[str, Any]]):
        """Adds newly posted proxies to the archive file."""
        self.log.info("--- Stage: Updating Archive ---")
        if not posted_proxies:
            self.log.info("No proxies were posted, archive remains unchanged.")
            return

        self.archive_path.parent.mkdir(parents=True, exist_ok=True)
        
        existing_proxies = []
        if self.archive_path.exists():
            try:
                with self.archive_path.open('r', encoding='utf-8') as f:
                    existing_proxies = json.load(f)
            except (json.JSONDecodeError, IOError):
                self.log.error(f"Could not read existing archive file. It will be overwritten.")

        updated_archive = existing_proxies + posted_proxies
        final_archive = list({p['tg_link']: p for p in updated_archive}.values())

        try:
            with self.archive_path.open('w', encoding='utf-8') as f:
                json.dump(final_archive, f, indent=4, ensure_ascii=False)
            self.log.info(f"Successfully updated archive with {len(posted_proxies)} new proxies. Total in archive: {len(final_archive)}")
        except IOError as e:
            self.log.error(f"Failed to write to archive file '{self.archive_path}': {e}")

# ================================================================
# 8. MAIN EXECUTION
# ================================================================

def main():
    """Main function to orchestrate the entire pipeline."""
    start_time = time.time()
    log = setup_logger("ProxyPublisher")
    log.info("====== Starting Telegram Proxy Publisher Pipeline ======")

    try:
        config_manager = ConfigManager(log, Path("data/preferences.json"))
        config = config_manager.load()
        runtime_manager = RuntimeManager(start_time, config, log)

        data_loader = DataLoader(config, log, runtime_manager)
        fetched_proxies = data_loader.fetch_proxies()
        if not fetched_proxies:
            self.log.info("No proxies were fetched. Exiting.")
            return

        processor = ProxyProcessor(config, log)
        new_proxies = processor.find_new_proxies(fetched_proxies)
        if not new_proxies:
            self.log.info("No new proxies found after filtering. Exiting.")
            return

        poster = TelegramPoster(config, log, runtime_manager)
        posted_proxies = poster.post_all(new_proxies)

        archive_manager = ArchiveManager(config, log)
        archive_manager.update_archive(posted_proxies)

    except Exception as e:
        log.critical(f"An unhandled exception occurred in the pipeline: {e}", exc_info=True)
    finally:
        elapsed_time = time.time() - start_time
        log.info(f"====== Pipeline Finished in {elapsed_time:.2f} seconds ======")

if __name__ == "__main__":
    main()
