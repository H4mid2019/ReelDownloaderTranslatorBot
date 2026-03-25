"""
Truth Social monitor to fetch Donald Trump's posts and use Groq AI
to determine if they relate to Iran, sending an alert if they do.
"""
import os
import asyncio
import logging
import feedparser
import groq

from config import GROQ_API_KEY, TRUTH_ALERT_CHAT_ID, TRUTH_RSS_URL

logger = logging.getLogger(__name__)

LAST_POST_FILE = "last_truth_id.txt"

class TruthMonitor:
    def __init__(self):
        self.groq_client = groq.Groq(api_key=GROQ_API_KEY)
        self.rss_url = TRUTH_RSS_URL or "https://trumpstruth.org/feed"
        self.chat_id = TRUTH_ALERT_CHAT_ID
        self.model = "llama-3.3-70b-versatile"
        
    def _get_last_processed_id(self) -> str:
        if os.path.exists(LAST_POST_FILE):
            with open(LAST_POST_FILE, "r") as f:
                return f.read().strip()
        return ""

    def _save_last_processed_id(self, post_id: str):
        with open(LAST_POST_FILE, "w") as f:
            f.write(post_id)

    async def is_related_to_iran(self, text: str) -> bool:
        if not text or not text.strip():
            return False
            
        prompt = f"""Read the following post and determine if it mentions or relates to Iran.
Answer ONLY with exactly 'YES' or 'NO'. Nothing else.

Post:
"{text}"
"""
        try:
            response = await asyncio.to_thread(
                self.groq_client.chat.completions.create,
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a specialized AI designed to filter content."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0,
                max_tokens=10
            )
            result = response.choices[0].message.content.strip().upper()
            return "YES" in result
        except groq.RateLimitError:
            logger.warning("Groq rate limit exceeded while checking Truth Social post.")
            return False
        except Exception as e:
            logger.error(f"Error checking if post is related to Iran: {e}")
            return False

    async def check_feed(self, application) -> None:
        if not self.chat_id:
            # We don't want to log this spammy warning every 5 mins.
            # Only checking once and failing silently.
            return

        try:
            feed = await asyncio.to_thread(feedparser.parse, self.rss_url)
            
            if not feed.entries:
                return
                
            latest_post = feed.entries[0]
            # Use link or ID
            post_id = latest_post.id if hasattr(latest_post, 'id') else latest_post.link
            
            last_id = self._get_last_processed_id()
            
            if post_id and post_id != last_id:
                logger.info(f"New Truth Social post detected: {post_id}")
                
                content = latest_post.get('description', '') or latest_post.get('summary', '') or latest_post.get('title', '')
                
                is_target = await self.is_related_to_iran(content)
                
                if is_target:
                    logger.info("Post relates to Iran! Sending alert...")
                    
                    # Clean up basic HTML from RSS description
                    import re
                    clean_content = re.sub(r'<[^>]+>', ' ', content).replace('&nbsp;', ' ').strip()
                    
                    msg = f"🚨 **TRUTH ALERTS** 🚨\n\n"
                    msg += f"**New post from @realDonaldTrump relates to Iran:**\n\n"
                    msg += f"_{clean_content}_\n\n"
                    msg += f"🔗 [View Post]({latest_post.link})"
                    
                    try:
                        await application.bot.send_message(
                            chat_id=self.chat_id,
                            text=msg,
                            parse_mode="Markdown",
                            disable_web_page_preview=False
                        )
                    except Exception as e:
                        logger.error(f"Failed to send Telegram alert: {e}")
                
                # Save it so we don't process it again
                self._save_last_processed_id(post_id)

        except Exception as e:
            logger.error(f"Error checking Truth Social feed: {e}")

async def monitor_loop(application):
    """Background task to poll Truth Social periodically."""
    monitor = TruthMonitor()
    logger.info("Started Truth Social monitor loop.")
    
    # Wait a few seconds before first check to let bot initialize completely
    await asyncio.sleep(5)
    
    while True:
        try:
            await monitor.check_feed(application)
        except asyncio.CancelledError:
            logger.info("Truth Social monitor cancelled.")
            break
        except Exception as e:
            logger.error(f"Unexpected error in monitor loop: {e}")
        
        # Wait 5 minutes (300 seconds) before checking again
        await asyncio.sleep(300)
