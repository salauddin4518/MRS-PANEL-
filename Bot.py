# ═══════════════════════════════════════════
#   LINK BYPASSER BOT v2
#   Developed by Youname
# ═══════════════════════════════════════════
BOT_TOKEN = "8990216619:AAGVfg4gqcmltoE1LqlOUoLsLLXubQnnNxU"   # <-- paste your token here
# ═══════════════════════════════════════════

import os
import sys
import re
import asyncio
import logging
import requests
from urllib.parse import urlparse, urljoin, unquote
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

DEVELOPER = "yourname"
BOT_NAME = "Link Bypasser Bot"

REQUEST_TIMEOUT = 15
MAX_REDIRECTS = 15
MAX_HTML_HOPS = 5   # HTML/JS se nikalne wale deep links ke liye
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

SHORTENER_HOSTS = {
    "bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "is.gd",
    "buff.ly", "rebrand.ly", "cutt.ly", "shorturl.at", "rb.gy",
    "shorte.st", "adf.ly", "bc.vc", "ouo.io", "ouo.press",
    "shrinkme.io", "shrinkearn.com", "linkshrink.net", "adfoc.us",
    "za.gl", "za.uy", "gplinks.co", "gplinks.in", "droplink.co",
    "yoshort.in", "mdiskshortner.link", "clk.sh", "clicksfly.com",
    "try2link.com", "arolinks.com", "indianshortner.in",
    "urlshortx.com", "tnlink.in", "omg10.com", "gtlink.co",
    "filesdl.in", "filesdl.top", "filesdl.xyz", "filesdl.net",
}

URL_REGEX = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)

# Patterns to find real link inside HTML/JS
HTML_PATTERNS = [
    # <meta http-equiv="refresh" content="0;url=...">
    re.compile(r'<meta[^>]+http-equiv=["\']refresh["\'][^>]+content=["\'][^;]*;\s*url=([^"\']+)["\']', re.I),
    # "url":"https://..." or url: "https://..."
    re.compile(r'url\s*[:=]\s*["\'](https?://[^"\']+)["\']', re.I),
    # href="https://..." (skip generic nav links)
    re.compile(r'href\s*=\s*["\'](https?://[^"\']+)["\']', re.I),
    # data-url, data-href, data-link
    re.compile(r'data-(?:url|href|link|target)\s*=\s*["\'](https?://[^"\']+)["\']', re.I),
    # window.location = "https://..."
    re.compile(r'window\.location(?:\.href)?\s*=\s*["\'](https?://[^"\']+)["\']', re.I),
    # location.replace("https://...")
    re.compile(r'location\.replace\s*\(\s*["\'](https?://[^"\']+)["\']', re.I),
]

dp = Dispatcher()


def get_token() -> str:
    """Return token from BOT_TOKEN variable or env fallback; exit if empty."""
    token = (BOT_TOKEN or "").strip() or (os.getenv("BOT_TOKEN") or "").strip()
    if not token:
        print("[ERROR] BOT_TOKEN khali hai. File ke top pe apna token daalo.")
        sys.exit(1)
    return token


def is_url(text: str) -> bool:
    """Check if text contains a URL."""
    return bool(URL_REGEX.search(text or ""))


def extract_urls(text: str):
    """Return all URLs found in text."""
    return URL_REGEX.findall(text or "")


def is_shortener_host(host: str) -> bool:
    """Check if a host is in the known shortener list."""
    host = host.lower().lstrip("www.")
    return any(host == h or host.endswith("." + h) for h in SHORTENER_HOSTS)


def is_redirect_like(url1: str, url2: str) -> bool:
    """Check if two URLs point to the same path (likely same service)."""
    try:
        p1, p2 = urlparse(url1), urlparse(url2)
        return p1.path == p2.path and p1.netloc != p2.netloc
    except Exception:
        return False


def http_resolve(url: str):
    """Follow only HTTP redirects. Returns (final_url, chain, error)."""
    chain = [url]
    current = url
    try:
        for _ in range(MAX_REDIRECTS):
            r = requests.get(
                current,
                headers={"User-Agent": USER_AGENT},
                allow_redirects=False,
                timeout=REQUEST_TIMEOUT,
            )
            if r.status_code in (301, 302, 303, 307, 308):
                loc = r.headers.get("Location")
                if not loc:
                    break
                loc = urljoin(current, loc)
                current = loc
                chain.append(current)
            else:
                break
        return current, chain, None
    except requests.exceptions.Timeout:
        return current, chain, "Timeout."
    except Exception as e:
        return current, chain, f"Error: {e}"


def find_embedded_link(url: str):
    """Fetch page HTML and extract embedded redirect target."""
    try:
        r = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
        html = r.text or ""
        candidates = []
        for pat in HTML_PATTERNS:
            for m in pat.findall(html):
                candidates.append(m.strip())
        # Filter out obvious junk (css, js, images, same-host nav)
        host = urlparse(url).netloc
        cleaned = []
        for c in candidates:
            c = c.replace("\\/", "/")
            if any(c.lower().endswith(x) for x in (".css", ".js", ".png", ".jpg", ".svg", ".ico")):
                continue
            if urlparse(c).netloc == host:
                continue
            cleaned.append(c)
        # Prefer non-shortener first
        for c in cleaned:
            if not is_shortener_host(urlparse(c).netloc):
                return c
        return cleaned[0] if cleaned else None
    except Exception as e:
        logger.error(f"HTML extract error for {url}: {e}")
        return None


def smart_bypass(url: str):
    """
    Multi-stage bypass:
      1. Follow HTTP redirects.
      2. If still on a shortener host, fetch HTML and pull embedded link.
      3. Repeat up to MAX_HTML_HOPS.
    Returns (final_url, chain, error).
    """
    chain = [url]
    current = url

    # Stage 1: HTTP redirects
    current, http_chain, err = http_resolve(current)
    chain.extend(http_chain[1:])
    if err:
        return current, chain, err

    # Stage 2: HTML-embedded link extraction
    for _ in range(MAX_HTML_HOPS):
        host = urlparse(current).netloc
        if not is_shortener_host(host):
            break
        embedded = find_embedded_link(current)
        if not embedded or embedded == current:
            break
        # If embedded is a known shortener, resolve it via HTTP first
        if is_shortener_host(urlparse(embedded).netloc):
            embedded, inner_chain, _ = http_resolve(embedded)
            chain.extend(inner_chain[1:])
        chain.append(embedded)
        current = embedded

    # Strip tracking params from final
    if "?" in current:
        p = urlparse(current)
        cleaned = p._replace(query="", fragment="").geturl()
    else:
        cleaned = current

    return cleaned, chain, None


@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Handle /start command."""
    await message.answer(
        f"🔗 **{BOT_NAME}**\n"
        f"👨‍💻 Developed by **{DEVELOPER}**\n\n"
        "Short ya ad-link bhejo — real destination nikal ke dunga.\n"
        "Ab JavaScript aur meta-refresh redirects bhi handle karta hai.\n\n"
        "**Commands:**\n"
        "• `/resolve <url>` — manually resolve\n"
        "• `/hosts` — supported shorteners\n"
        "• `/help` — info",
        parse_mode="Markdown",
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    """Handle /help command."""
    await message.answer(
        f"📖 **Help — {BOT_NAME}**\n"
        f"Developed by **{DEVELOPER}**\n\n"
        "Bot teen tareeke se link resolve karta hai:\n"
        "1. HTTP 301/302 redirects\n"
        "2. HTML mein `<meta refresh>` aur `data-url` links\n"
        "3. Inline JavaScript (`window.location`, `location.replace`)\n\n"
        "**Commands:** `/resolve`, `/hosts`, `/start`\n\n"
        "⚠️ Sirf public links pe use karo.",
        parse_mode="Markdown",
    )


@dp.message(Command("hosts"))
async def cmd_hosts(message: Message):
    """List supported shortener hosts."""
    hosts = "\n".join(f"• `{h}`" for h in sorted(SHORTENER_HOSTS))
    await message.answer(
        f"🌐 **Supported Hosts ({len(SHORTENER_HOSTS)}):**\n\n{hosts}",
        parse_mode="Markdown",
    )


@dp.message(Command("resolve"))
async def cmd_resolve(message: Message):
    """Handle /resolve <url> command."""
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not is_url(parts[1]):
        await message.answer("❌ Usage: `/resolve <url>`", parse_mode="Markdown")
        return
    await process_url(message, parts[1].strip())


@dp.message(F.text)
async def handle_message(message: Message):
    """Handle any message containing a URL."""
    urls = extract_urls(message.text)
    if not urls:
        await message.answer(
            f"🔗 Link bhejo bypass karne ke liye.\n\n_Developed by {DEVELOPER}_",
            parse_mode="Markdown",
        )
        return
    await process_url(message, urls[0])


async def process_url(message: Message, url: str):
    """Resolve a URL and reply with result."""
    status = await message.answer("⏳ Resolving (deep scan)...")

    final, chain, err = smart_bypass(url)

    if err:
        await status.edit_text(
            f"❌ **Bypass failed**\n\n"
            f"🔗 Input: `{url}`\n"
            f"⚠️ Reason: {err}\n\n"
            f"_Developed by {DEVELOPER}_",
            parse_mode="Markdown",
        )
        return

    chain_text = ""
    if len(chain) > 1:
        chain_text = "\n\n🔁 **Chain:**\n" + "\n".join(
            f"{i+1}. `{u}`" for i, u in enumerate(chain[:12])
        )
        if len(chain) > 12:
            chain_text += f"\n... +{len(chain) - 12} more"

    # Alert: if final == input, mention why
    if final == url:
        note = "\n\n⚠️ Ye link ne koi redirect nahi diya (direct URL hai ya server ne block kiya)."
    elif is_shortener_host(urlparse(final).netloc):
        note = "\n\n⚠️ Final link abhi bhi shortener hai — captcha ya JS challenge ke wajah se aage nahi ja saka. Manually browser mein open karke dekh."
    else:
        note = ""

    await status.edit_text(
        f"✅ **Bypassed!**\n\n"
        f"🔗 **Original:** `{url}`\n"
        f"🎯 **Final:** `{final}`"
        f"{chain_text}{note}\n\n"
        f"_Developed by {DEVELOPER}_",
        parse_mode="Markdown",
    )
    logger.info(f"Resolved: {url} -> {final}")


async def main():
    """Main entry point."""
    token = get_token()
    logger.info(f"Token loaded: {token[:6]}...{token[-4:]}")
    print(f"[BOT STARTED] {BOT_NAME} by {DEVELOPER}")

    bot = Bot(token=token)
    try:
        me = await bot.get_me()
        logger.info(f"Bot: @{me.username}")
    except Exception as e:
        logger.error(f"Bot init error: {e}")
        sys.exit(1)

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("[BOT STOPPED]")