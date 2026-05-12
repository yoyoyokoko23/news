from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import html
import os
import re
import smtplib
import sys
from collections import defaultdict
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
        "site:wallstreetcn.com (深度 OR 解读 OR 分析 OR 复盘) (科创50 OR 纳斯达克 OR 中证500 OR 沪深300 OR 人工智能 OR 黄金)",
    ),
    (
        "虎嗅",
        "重点来源-虎嗅",
        "site:huxiu.com (深度 OR 解读 OR 分析 OR 观察) (人工智能 OR AI OR 科技 OR 投资)",
    ),
]

POLICY_TOPICS = {
    "政策与地缘-美伊关系": [
        "美伊关系 伊朗 美国 制裁 核谈判 中东 最新",
        "US Iran relations sanctions nuclear talks Middle East",
    ],
    "政策与产业-半导体": [
        "半导体 政策 出口管制 芯片 中国 美国 最新",
        "semiconductor policy export controls chips China US",
    ],
    "政策与产业-AI": [
        "人工智能 政策 监管 AI 法规 中国 美国 最新",
        "AI regulation policy artificial intelligence China US",
    ],
}

DEEP_ARTICLE_KEYWORDS = [
    "深度",
    "解读",
    "分析",
    "复盘",
    "观察",
    "拆解",
    "专题",
    "研报",
    "长文",
    "趋势",
    "逻辑",
    "为什么",
    "影响",
    "背后",
    "政策",
    "监管",
    "制裁",
    "出口管制",
    "核谈判",
    "法规",
]

SHALLOW_ARTICLE_KEYWORDS = [
    "快讯",
    "异动",
    "早报",
    "午报",
    "收盘",
    "开盘",
    "涨幅",
    "跌幅",
    "拉升",
    "跳水",
    "一度",
    "简讯",
    "7x24",
]

CATEGORY_ORDER = [
    "重点来源-华尔街见闻",
    "重点来源-虎嗅",
    "政策与地缘-美伊关系",
    "政策与产业-半导体",
    "政策与产业-AI",
    "中国科创50",
    "沪深300",
    "中证500",
    "纳斯达克指数",
    "人工智能",
    "黄金",
]

CATEGORY_STYLES = {
    "重点来源-华尔街见闻": {"color": "#7c2d12", "background": "#fff7ed"},
    "重点来源-虎嗅": {"color": "#9a3412", "background": "#fffbeb"},
    "政策与地缘-美伊关系": {"color": "#991b1b", "background": "#fef2f2"},
    "政策与产业-半导体": {"color": "#1d4ed8", "background": "#eff6ff"},
    "政策与产业-AI": {"color": "#6d28d9", "background": "#f5f3ff"},
    "中国科创50": {"color": "#047857", "background": "#ecfdf5"},
    "沪深300": {"color": "#0369a1", "background": "#f0f9ff"},
    "中证500": {"color": "#0f766e", "background": "#f0fdfa"},
    "纳斯达克指数": {"color": "#4338ca", "background": "#eef2ff"},
    "人工智能": {"color": "#7e22ce", "background": "#faf5ff"},
    "黄金": {"color": "#a16207", "background": "#fefce8"},
}


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


def google_news_rss_url(query: str, language: str = "zh-CN", region: str = "CN", days: int = 2) -> str:
    encoded_query = quote_plus(f"{query} when:{days}d")
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
    without_tags = re.sub(r"<[^>]+>", " ", value or "")
    return " ".join(html.unescape(without_tags).split())


def slugify_category(category: str) -> str:
    return quote_plus(category)


def article_text(article: Article) -> str:
    return f"{article.title} {article.summary}"


def summarize_article_text(article: Article, max_chars: int = 220) -> str:
    summary = clean_text(article.summary)
    if not summary:
        return "暂无详细摘要，请点击原文查看。"
    if len(summary) <= max_chars:
        return summary
    return summary[:max_chars].rstrip() + "..."


def is_shallow_article(article: Article) -> bool:
    if article.topic.startswith("政策与"):
        return False

    text = article_text(article)
    summary_len = len(clean_text(article.summary))
    title_len = len(clean_text(article.title))
    has_shallow_signal = any(keyword in text for keyword in SHALLOW_ARTICLE_KEYWORDS)
    has_deep_signal = any(keyword in text for keyword in DEEP_ARTICLE_KEYWORDS)

    if has_deep_signal:
        return False
    if summary_len < 45 and title_len < 34:
        return True
    return has_shallow_signal and summary_len < 90


def article_depth_score(article: Article) -> int:
    text = article_text(article)
    score = min(len(clean_text(article.summary)), 300)
    score += sum(80 for keyword in DEEP_ARTICLE_KEYWORDS if keyword in text)
    score -= sum(60 for keyword in SHALLOW_ARTICLE_KEYWORDS if keyword in text)
    if article.topic.startswith("重点来源"):
        score += 80
    if article.topic.startswith("政策与"):
        score += 90
    if article.source in {"华尔街见闻", "虎嗅"}:
        score += 50
    return score


def fetch_topic_articles(topic: str, queries: Iterable[str], limit_per_topic: int, days: int = 2) -> list[Article]:
    articles: list[Article] = []
    seen_links: set[str] = set()

    for query in queries:
        feed = feedparser.parse(google_news_rss_url(query, days=days))
        for entry in feed.entries[:12]:
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


def rank_articles(articles: list[Article]) -> list[Article]:
    keep = [article for article in articles if not is_shallow_article(article)]
    if not keep:
        keep = articles
    return sorted(keep, key=article_depth_score, reverse=True)


def fetch_policy_articles(limit_per_policy_topic: int) -> list[Article]:
    articles: list[Article] = []
    for topic, queries in POLICY_TOPICS.items():
        try:
            topic_articles = fetch_topic_articles(topic, queries, limit_per_policy_topic, days=1)
            articles.extend(topic_articles[:limit_per_policy_topic])
        except Exception as exc:
            print(f"Warning: failed to fetch {topic} policy news: {exc}", file=sys.stderr)
    return articles


def fetch_articles(limit_per_topic: int) -> list[Article]:
    all_articles: list[Article] = []
    priority_limit = int(os.getenv("PRIORITY_ARTICLES_PER_SOURCE", "6"))
    policy_limit = int(os.getenv("POLICY_ARTICLES_PER_TOPIC", "1"))
    all_articles.extend(fetch_priority_articles(priority_limit))
    all_articles.extend(fetch_policy_articles(policy_limit))

    for topic, queries in DEFAULT_TOPICS.items():
        all_articles.extend(fetch_topic_articles(topic, queries, limit_per_topic))
    return rank_articles(dedupe_articles(all_articles))


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
            f"  主要内容: {summarize_article_text(article, 360)}\n"
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
2. 聚焦中国科创50、纳斯达克、中证500、沪深300、人工智能、黄金，以及美伊关系、半导体、AI政策监管。
3. 区分“事实新闻”和“分析判断”，不要编造素材外的信息。
4. 给出今日最值得关注的 3-5 个要点。
5. 对风险因素做温和提醒，不构成投资建议。
6. 优先引用华尔街见闻、虎嗅等有分析价值的深度文章；少写只有一句标题的快讯。
7. 按“今日重点”“市场指数”“政策与地缘”“半导体与AI政策”“人工智能”“黄金与宏观”“风险提示”组织。
8. 每个分类下尽量包含：文章标题、主要内容、为什么重要、原文链接。
9. 输出 HTML 片段，使用 h2/h3/ul/li/p/a，不要包含完整 html/body 标签。

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


def grouped_articles(articles: list[Article]) -> dict[str, list[Article]]:
    groups: dict[str, list[Article]] = defaultdict(list)
    for article in articles:
        groups[article.topic].append(article)

    ordered: dict[str, list[Article]] = {}
    for category in CATEGORY_ORDER:
        if category in groups:
            ordered[category] = groups.pop(category)
    for category in sorted(groups):
        ordered[category] = groups[category]
    return ordered


def category_style(category: str) -> dict[str, str]:
    return CATEGORY_STYLES.get(category, {"color": "#374151", "background": "#f9fafb"})


def build_category_nav(articles: list[Article]) -> str:
    buttons = []
    for category in grouped_articles(articles):
        style = category_style(category)
        buttons.append(
            f"""
<a href="#{slugify_category(category)}" style="display:inline-block;margin:0 8px 8px 0;padding:8px 12px;border-radius:999px;background:{style['background']};color:{style['color']};border:1px solid {style['color']};text-decoration:none;font-size:13px;font-weight:600;">{html.escape(category)}</a>
""".strip()
        )
    return "\n".join(buttons)


def build_article_sections(articles: list[Article]) -> str:
    sections: list[str] = []
    for category, category_articles in grouped_articles(articles).items():
        style = category_style(category)
        category_id = slugify_category(category)
        cards: list[str] = []
        for article in category_articles:
            cards.append(
                f"""
<div style="border:1px solid {style['color']};border-left:6px solid {style['color']};border-radius:10px;padding:14px 16px;margin:10px 0;background:{style['background']};">
  <p style="margin:0 0 6px 0;color:#555;font-size:13px;">{html.escape(article.source)} · {html.escape(article.published or "时间未知")}</p>
  <h3 style="margin:0 0 8px 0;font-size:16px;line-height:1.35;">{html.escape(article.title)}</h3>
  <p style="margin:0 0 10px 0;color:#333;">{html.escape(summarize_article_text(article))}</p>
  <p style="margin:0;"><a href="{html.escape(article.link)}">阅读全文</a></p>
</div>
""".strip()
            )
        sections.append(
            f"""
<h2 id="{category_id}" style="background:{style['color']};color:#fff;border-radius:10px;padding:10px 14px;margin-top:26px;">{html.escape(category)}</h2>
{''.join(cards)}
""".strip()
        )
    return "\n".join(sections)


def build_email_html(ai_summary: str, articles: list[Article]) -> str:
    category_nav = build_category_nav(articles)
    article_sections = build_article_sections(articles)
    generated_at = dt.datetime.now(ZoneInfo(os.getenv("DIGEST_TIMEZONE", "America/Los_Angeles")))
    return f"""
<div style="font-family: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; line-height: 1.55; color: #111; background:#f8fafc; padding:20px;">
  <div style="max-width:760px;margin:0 auto;background:#ffffff;border-radius:14px;padding:22px;">
  <h1 style="margin:0 0 8px 0;font-size:24px;">每日财经新闻摘要</h1>
  <p style="color:#666;">生成时间：{generated_at.strftime("%Y-%m-%d %H:%M %Z")}</p>
  <div style="margin:16px 0 20px 0;padding:14px;border-radius:12px;background:#f3f4f6;">
    <p style="margin:0 0 10px 0;color:#374151;font-weight:700;">分类筛选 / 快速跳转</p>
    {category_nav}
  </div>
  {ai_summary}
  <hr>
  <h2>新闻素材与原文链接</h2>
  <p style="color:#666;">以下新闻素材放在邮件最后，按主题归类，并优先保留摘要更完整、分析性更强的内容。</p>
  {article_sections}
  <p style="color:#777;font-size:12px;">本邮件由自动化脚本生成，仅供信息整理，不构成投资建议。</p>
  </div>
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
