import base64
import os
import sys
import asyncio
import json
import requests
import re

from playwright.async_api import async_playwright

def load_existing_tweets(file_url):
    """GitHub Pagesから既存のtweets.txtを読み込み、パースしてリストで返す"""
    if not file_url:
        return []
    
    print(f"Fetching existing tweets from {file_url}...")
    try:
        resp = requests.get(file_url, timeout=10)
        if resp.status_code != 200:
            print(f"No existing tweets found (Status: {resp.status_code}). Starting fresh.")
            return []
        
        content = resp.text
        tweets = []
        # 区切り線で分割
        raw_entries = content.split("-" * 20 + "\n")
        
        for entry in raw_entries:
            if not entry.strip():
                continue
            
            user_match = re.search(r"User: (.*)", entry)
            time_match = re.search(r"Time: (.*)", entry)
            # Textは複数行の可能性があるので、Timeの後から最後までを取得
            text_match = re.search(r"Text: ([\s\S]*)", entry)
            
            if user_match and time_match and text_match:
                tweets.append({
                    "user_info": user_match.group(1).strip(),
                    "timestamp": time_match.group(1).strip(),
                    "text": text_match.group(1).strip()
                })
        print(f"Loaded {len(tweets)} existing tweets.")
        return tweets
    except Exception as e:
        print(f"Failed to load existing tweets: {e}")
        return []

async def scrape_twitter(url: str, scroll_count: int = 5):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        
        # 認証情報の読み込みロジック
        storage_state = None
        if os.path.exists("auth.json"):
            print("Found auth.json, loading...")
            storage_state = "auth.json"
        elif os.environ.get("TWITTER_AUTH_JSON"):
            print("Found TWITTER_AUTH_JSON env var, loading...")
            try:
                # 環境変数が生JSONかBase64か判別してロード
                env_val = os.environ["TWITTER_AUTH_JSON"]
                try:
                    storage_state = json.loads(env_val)
                except json.JSONDecodeError:
                    decoded = base64.b64decode(env_val).decode("utf-8")
                    storage_state = json.loads(decoded)
            except Exception as e:
                print(f"Failed to load auth from env: {e}")

        if storage_state:
            context = await browser.new_context(
                storage_state=storage_state,
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                locale="ja-JP",
            )
        else:
            print("Warning: auth.json not found!")
            context = await browser.new_context(locale="ja-JP")
        
        page = await context.new_page()
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)

        try:
            print(f"Accessing: {url}")
            await page.goto(url, wait_until="commit", timeout=10000)
            try:
                await page.wait_for_selector("article", timeout=15000)
            except:
                print("Element wait timeout (Might be OK if content loaded)")

            tweets_data = []
            seen_tweet_sigs = set()

            for i in range(scroll_count):
                print(f"Scrolling {i+1}/{scroll_count}...")
                
                articles = await page.locator('article[data-testid="tweet"]').all()
                for article in articles:
                    try:
                        user_name_locator = article.locator('[data-testid="User-Name"]')
                        user_info = await user_name_locator.inner_text() if await user_name_locator.count() > 0 else "(No User)"
                        
                        text_locator = article.locator('[data-testid="tweetText"]')
                        text = await text_locator.inner_text() if await text_locator.count() > 0 else "(No Text)"
                        
                        time_locator = article.locator('time')
                        timestamp = await time_locator.get_attribute("datetime") if await time_locator.count() > 0 else "(No Time)"

                        sig = f"{user_info}_{timestamp}_{text}"
                        
                        if sig not in seen_tweet_sigs:
                            seen_tweet_sigs.add(sig)
                            tweets_data.append({
                                "user_info": user_info.replace("\n", " "),
                                "text": text,
                                "timestamp": timestamp
                            })
                    except Exception as e:
                        continue

                await page.mouse.wheel(0, 2000)
                await page.wait_for_timeout(2000)

            print(f"Total unique tweets collected in this session: {len(tweets_data)}")
            return tweets_data

        except Exception as e:
            print(f"Error: {e}")
            raise e
        finally:
            await browser.close()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        target_url = sys.argv[1]
        existing_data_url = os.getenv("EXISTING_DATA_URL", "") # 環境変数から既存データのURLを取得
        
        print(f"Running for URL: {target_url}")
        
        try:
            # 1. 新しいツイートを取得
            new_tweets = asyncio.run(scrape_twitter(target_url))
            
            # 2. 既存のツイートを取得
            existing_tweets = load_existing_tweets(existing_data_url)
            
            # 3. マージと重複排除
            # 既存のツイートを署名のセットに追加
            seen_sigs = set()
            for t in existing_tweets:
                sig = f"{t['user_info']}_{t['timestamp']}_{t['text']}"
                seen_sigs.add(sig)
            
            merged_tweets = list(existing_tweets)
            added_count = 0
            
            # 新しいツイートを追加（重複していないものだけ）
            # 新しいものほど上に来るようにしたい場合は、リストの先頭に追加するか、
            # タイムスタンプでソートする等の工夫が必要。今回は単純に追加。
            # 通常、スクレイピングは新しい順に取れることが多いので、
            # 既存リストの「前」に追加する形にする。
            
            for t in new_tweets:
                sig = f"{t['user_info']}_{t['timestamp']}_{t['text']}"
                if sig not in seen_sigs:
                    merged_tweets.insert(0, t) # 先頭に追加
                    seen_sigs.add(sig)
                    added_count += 1
            
            print(f"Merged {added_count} new tweets. Total: {len(merged_tweets)}")
            
            # 4. 保存
            if merged_tweets:
                output_dir = "public"
                os.makedirs(output_dir, exist_ok=True)
                
                output_path = os.path.join(output_dir, "tweets.txt")
                with open(output_path, "w", encoding="utf-8") as f:
                    for tweet in merged_tweets:
                        f.write(f"User: {tweet['user_info']}\n")
                        f.write(f"Time: {tweet['timestamp']}\n")
                        f.write(f"Text: {tweet['text']}\n")
                        f.write("-" * 20 + "\n")
                print(f"Saved total {len(merged_tweets)} tweets to {output_path}")
            else:
                print("No tweets to save.")

        except Exception as e:
            print(f"Failed: {e}")
            sys.exit(1)
    else:
        print("Usage: python twimg.py <URL>")
        sys.exit(1)