from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import html
import os
import smtplib
import sys
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Iterable
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import feedparser
import requests
from dotenv import load_dotenv
from openai import OpenAI


DEFAULT_TOPICS = {
    "中国科创50": [
        "科创50",
        "上证科创板50",
        "STAR 50 Index",
    ],
    "纳斯达克指数": [
        "纳斯达克指数",
        "Nasdaq Composite",
        "Nasdaq 100",
    ],
    "中证500": [
        "中证500",
        "CSI 500",
    ],
    "沪深300": [
        "沪深300",
        "CSI 300",
    ],
    "人工智能": [
        "人工智能 投资",
        "AI 芯片",
        "生成式 AI",
        "artificial intelligence market",
    ],
    "黄金": [
        "黄金 金价",
        "gold price",
        "美联储 黄金",
    ],
}

MARKET_SYMBOLS = {
    "科创50": "000688.SS",
    "纳斯达克综合指数": "^IXIC",
    "中证500": "000905.SS",
    "沪深300": "000300.SS",
    "黄金期货": "GC=F",
}

PRIORITY_RSS_FEEDS = {
    "虎嗅": "https://www.huxiu.com/rss/0.xml",
}

PRIORITY_SITE_QUERIES = [
    (
        "华尔街见闻",
        "重点来源-华尔街见闻",
        "site:wallstreetcn.com 科创50 OR 纳斯达克 OR 中证500 OR 沪深300 OR 人工智能 OR 黄金",
    ),
    (
        "虎嗅",
        "重点来源-虎嗅",
        "site:huxiu.com 人工智能 OR AI OR 科技 OR 投资",
    ),
]


@dataclass(frozen=True)
class Article:
    topic: str
    title: str
    link: str
    source: str
    published: str
    summary: str


def env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or value == "":
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def should_send_now() -> bool:
    timezone = ZoneInfo(os.getenv("DIGEST_TIMEZONE", "America/Los_Angeles"))
    target_hour = int(os.getenv("DIGEST_HOUR", "11"))
    now = dt.datetime.now(timezone)
    return now.hour == target_hour


def google_news_rss_url(query: str, language: str = "zh-CN", region: str = "CN") -> str:
    encoded_query = quote_plus(f"{query} when:2d")
    return (
        "https://news.google.com/rss/search?"
        f"q={encoded_query}&hl={language}&gl={region}&ceid={region}:zh-Hans"
    )


def parse_published(entry: object) -> str:
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not parsed:
        return ""
    return dt.datetime(*parsed[:6], tzinfo=dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def clean_text(value: str) -> str:
    return " ".join(html.unescape(value or "").split())


def fetch_topic_articles(topic: str, queries: Iterable[str], limit_per_topic: int) -> list[Article]:
    articles: list[Article] = []
    seen_links: set[str] = set()

    for query in queries:
        feed = feedparser.parse(google_news_rss_url(query))
        for entry in feed.entries[:8]:
            title = clean_text(getattr(entry, "title", ""))
            link = getattr(entry, "link", "")
            if not title or not link or link in seen_links:
                continue

            source = ""
            if getattr(entry, "source", None):
                source = clean_text(getattr(entry.source, "title", ""))

            articles.append(
                Article(
                    topic=topic,
                    title=title,
                    link=link,
                    source=source or "Google News",
                    published=parse_published(entry),
                    summary=clean_text(getattr(entry, "summary", "")),
                )
            )
            seen_links.add(link)

            if len(articles) >= limit_per_topic:
                break

        if len(articles) >= limit_per_topic:
            break

    return articles


def article_from_feed_entry(topic: str, source: str, entry: object) -> Article | None:
    title = clean_text(getattr(entry, "title", ""))
    link = getattr(entry, "link", "")
    if not title or not link:
        return None

    return Article(
        topic=topic,
        title=title,
        link=link,
        source=source,
        published=parse_published(entry),
        summary=clean_text(getattr(entry, "summary", "")),
    )


def fetch_feed_articles(source: str, url: str, limit: int) -> list[Article]:
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    response.raise_for_status()
    feed = feedparser.parse(response.content)
    articles: list[Article] = []
    for entry in feed.entries[:limit]:
        article = article_from_feed_entry(f"重点来源-{source}", source, entry)
        if article:
            articles.append(article)
    return articles


def fetch_priority_articles(limit_per_source: int) -> list[Article]:
    articles: list[Article] = []

    for source, url in PRIORITY_RSS_FEEDS.items():
        try:
            articles.extend(fetch_feed_articles(source, url, limit_per_source))
        except Exception as exc:
            print(f"Warning: failed to fetch {source} RSS: {exc}", file=sys.stderr)

    for source, topic, query in PRIORITY_SITE_QUERIES:
        try:
            source_articles = fetch_topic_articles(topic, [query], limit_per_source)
            articles.extend(
                Article(
                    topic=article.topic,
                    title=article.title,
                    link=article.link,
                    source=source if article.source == "Google News" else article.source,
                    published=article.published,
                    summary=article.summary,
                )
                for article in source_articles
            )
        except Exception as exc:
            print(f"Warning: failed to fetch {source} site news: {exc}", file=sys.stderr)

    return articles


def dedupe_articles(articles: list[Article]) -> list[Article]:
    deduped: list[Article] = []
    seen_links: set[str] = set()
    seen_titles: set[str] = set()

    for article in articles:
        normalized_title = article.title.lower()
        if article.link in seen_links or normalized_title in seen_titles:
            continue
        deduped.append(article)
        seen_links.add(article.link)
        seen_titles.add(normalized_title)

    return deduped


def fetch_articles(limit_per_topic: int) -> list[Article]:
    all_articles: list[Article] = []
    priority_limit = int(os.getenv("PRIORITY_ARTICLES_PER_SOURCE", "6"))
    all_articles.extend(fetch_priority_articles(priority_limit))

    for topic, queries in DEFAULT_TOPICS.items():
        all_articles.extend(fetch_topic_articles(topic, queries, limit_per_topic))
    return dedupe_articles(all_articles)


def fetch_market_snapshot() -> list[dict[str, str]]:
    symbols = ",".join(MARKET_SYMBOLS.values())
    url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={quote_plus(symbols)}"
    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        results = response.json().get("quoteResponse", {}).get("result", [])
    except Exception as exc:
        print(f"Warning: failed to fetch market snapshot: {exc}", file=sys.stderr)
        return []

    by_symbol = {item.get("symbol"): item for item in results}
    snapshot: list[dict[str, str]] = []
    for name, symbol in MARKET_SYMBOLS.items():
        item = by_symbol.get(symbol, {})
        price = item.get("regularMarketPrice")
        change_percent = item.get("regularMarketChangePercent")
        if price is None:
            continue
        snapshot.append(
            {
                "name": name,
                "symbol": symbol,
                "price": f"{price:,.2f}",
                "change_percent": f"{change_percent:+.2f}%" if change_percent is not None else "N/A",
            }
        )
    return snapshot


def build_source_digest(articles: list[Article], market_snapshot: list[dict[str, str]]) -> str:
    market_lines = [
        f"- {item['name']} ({item['symbol']}): {item['price']} ({item['change_percent']})"
        for item in market_snapshot
    ]
    article_lines = [
        (
            f"- [{article.topic}] {article.title}\n"
            f"  来源: {article.source}; 时间: {article.published or '未知'}\n"
            f"  摘要: {article.summary[:300]}\n"
            f"  链接: {article.link}"
        )
        for article in articles
    ]

    return (
        "行情快照:\n"
        + ("\n".join(market_lines) if market_lines else "- 暂无行情数据")
        + "\n\n新闻素材:\n"
        + "\n".join(article_lines)
    )


def summarize_with_ai(source_digest: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return fallback_summary(source_digest)

    client = OpenAI(api_key=api_key)
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    prompt = f"""
你是一位擅长把复杂财经信息讲清楚的中文投资新闻助理。

请根据下面的新闻素材，生成一封中文财经日报。要求：
1. 语言简单易懂，不要堆砌术语。
2. 聚焦中国科创50、纳斯达克、中证500、沪深300、人工智能、黄金。
3. 区分“事实新闻”和“分析判断”，不要编造素材外的信息。
4. 给出今日最值得关注的 3-5 个要点。
5. 对风险因素做温和提醒，不构成投资建议。
6. 输出 HTML 片段，使用 h2/h3/ul/li/p，不要包含完整 html/body 标签。

新闻素材如下：
{source_digest}
""".strip()

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你只根据用户提供的素材生成中文财经新闻摘要。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
        )
        return response.choices[0].message.content or fallback_summary(source_digest)
    except Exception as exc:
        print(f"Warning: AI summary failed, sending fallback digest: {exc}", file=sys.stderr)
        return fallback_summary(source_digest, reason="AI 总结暂时失败，因此本次只发送原始新闻素材。")


def fallback_summary(source_digest: str, reason: str | None = None) -> str:
    escaped = html.escape(source_digest)
    message = reason or "未配置 OPENAI_API_KEY，因此本次只发送原始新闻素材，没有 AI 归纳分析。"
    return (
        "<h2>今日财经新闻素材</h2>"
        f"<p>{html.escape(message)}</p>"
        f"<pre style=\"white-space: pre-wrap; font-family: sans-serif;\">{escaped}</pre>"
    )


def build_email_html(ai_summary: str, articles: list[Article]) -> str:
    links = "\n".join(
        f"<li><strong>{html.escape(article.topic)}</strong>: "
        f"<a href=\"{html.escape(article.link)}\">{html.escape(article.title)}</a> "
        f"<span style=\"color:#666;\">{html.escape(article.source)}</span></li>"
        for article in articles
    )
    generated_at = dt.datetime.now(ZoneInfo(os.getenv("DIGEST_TIMEZONE", "America/Los_Angeles")))
    return f"""
<div style="font-family: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; line-height: 1.55; color: #111;">
  <p style="color:#666;">生成时间：{generated_at.strftime("%Y-%m-%d %H:%M %Z")}</p>
  {ai_summary}
  <hr>
  <h2>参考链接</h2>
  <ul>{links}</ul>
  <p style="color:#777;font-size:12px;">本邮件由自动化脚本生成，仅供信息整理，不构成投资建议。</p>
</div>
""".strip()


def send_email(subject: str, html_body: str) -> None:
    gmail_user = env("GMAIL_USER")
    gmail_app_password = "".join(env("GMAIL_APP_PASSWORD").split())
    to_email = env("TO_EMAIL", gmail_user)

    try:
        gmail_app_password.encode("ascii")
    except UnicodeEncodeError as exc:
        raise RuntimeError(
            "GMAIL_APP_PASSWORD must be the 16-character Gmail app password only. "
            "Please remove any Chinese labels, punctuation, or extra copied text."
        ) from exc

    message = EmailMessage()
    message["From"] = gmail_user
    message["To"] = to_email
    message["Subject"] = subject
    message["Date"] = email.utils.formatdate(localtime=True)
    message.set_content("你的邮件客户端不支持 HTML，请使用支持 HTML 的客户端查看。")
    message.add_alternative(html_body, subtype="html")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(gmail_user, gmail_app_password)
        smtp.send_message(message)


def run(send_if_due: bool) -> None:
    if send_if_due and not should_send_now():
        print("Not the configured digest hour yet. Skipping.")
        return

    limit_per_topic = int(os.getenv("ARTICLES_PER_TOPIC", "5"))
    articles = fetch_articles(limit_per_topic=limit_per_topic)
    if not articles:
        raise RuntimeError("No articles fetched. Please check network access or RSS sources.")

    market_snapshot = fetch_market_snapshot()
    source_digest = build_source_digest(articles, market_snapshot)
    ai_summary = summarize_with_ai(source_digest)

    timezone = ZoneInfo(os.getenv("DIGEST_TIMEZONE", "America/Los_Angeles"))
    today = dt.datetime.now(timezone).strftime("%Y-%m-%d")
    subject = f"每日财经新闻摘要 - {today}"
    html_body = build_email_html(ai_summary, articles)
    send_email(subject, html_body)
    print(f"Sent digest email with {len(articles)} articles.")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Daily finance news digest sender.")
    parser.add_argument(
        "--send-if-due",
        action="store_true",
        help="Only send when local time matches DIGEST_HOUR in DIGEST_TIMEZONE.",
    )
    parser.add_argument(
        "--send-now",
        action="store_true",
        help="Send immediately, ignoring the configured hour.",
    )
    args = parser.parse_args()

    if args.send_now:
        run(send_if_due=False)
    else:
        run(send_if_due=args.send_if_due)


if __name__ == "__main__":
    main()
