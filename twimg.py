import base64
import os
import sys
import asyncio
import json

from playwright.async_api import async_playwright

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
                    # JSONでなければBase64とみなしてデコード
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
                
                # --- ツイート抽出処理 (スクロールごとに実行) ---
                articles = await page.locator('article[data-testid="tweet"]').all()
                for article in articles:
                    try:
                        # User Name
                        user_name_locator = article.locator('[data-testid="User-Name"]')
                        user_info = await user_name_locator.inner_text() if await user_name_locator.count() > 0 else "(No User)"
                        
                        # Text
                        text_locator = article.locator('[data-testid="tweetText"]')
                        text = await text_locator.inner_text() if await text_locator.count() > 0 else "(No Text)"
                        
                        # Timestamp
                        time_locator = article.locator('time')
                        timestamp = await time_locator.get_attribute("datetime") if await time_locator.count() > 0 else "(No Time)"

                        # 重複チェック用の署名を作成
                        sig = f"{user_info}_{timestamp}_{text}"
                        
                        if sig not in seen_tweet_sigs:
                            seen_tweet_sigs.add(sig)
                            tweets_data.append({
                                "user_info": user_info.replace("\n", " "),
                                "text": text,
                                "timestamp": timestamp
                            })
                    except Exception as e:
                        # 要素が取得中に消えることもあるので無視して次へ
                        continue

                await page.mouse.wheel(0, 2000)
                await page.wait_for_timeout(2000)

            print(f"Total unique tweets collected: {len(tweets_data)}")
            return tweets_data

        except Exception as e:
            print(f"Error: {e}")
            raise e
        finally:
            await browser.close()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        url = sys.argv[1]
        print(f"Running for URL: {url}")
        try:
            tweets = asyncio.run(scrape_twitter(url))
            
            # ツイートを保存
            if tweets:
                with open("tweets.txt", "w", encoding="utf-8") as f:
                    for tweet in tweets:
                        f.write(f"User: {tweet['user_info']}\n")
                        f.write(f"Time: {tweet['timestamp']}\n")
                        f.write(f"Text: {tweet['text']}\n")
                        f.write("-" * 20 + "\n")
                print(f"Saved {len(tweets)} tweets to tweets.txt")
            else:
                print("No tweets found.")

        except Exception as e:
            print(f"Failed: {e}")
            sys.exit(1)
    else:
        print("Usage: python twimg.py <URL>")
        sys.exit(1)