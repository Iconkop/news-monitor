#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, json, hashlib, smtplib, sys, feedparser, requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from email.utils import formataddr
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
import favicon

STATE_FILE = "state.json"
MAX_ITEMS_PER_RUN = 30


# 读取文本行
def load_lines(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


# 去掉 HTML 标签
def sanitize_html_to_text(html):
    soup = BeautifulSoup(html or "", "html.parser")
    return soup.get_text(" ", strip=True)


# 为每条新闻生成唯一 fingerprint
def entry_fingerprint(title, link):
    h = hashlib.sha256()
    h.update((title or "").encode("utf-8"))
    h.update((link or "").encode("utf-8"))
    return h.hexdigest()


# 读取已推送状态
def load_state():
    if not os.path.exists(STATE_FILE):
        return {"seen": set()}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {"seen": set(data.get("seen", []))}
    except Exception:
        return {"seen": set()}


# 保存状态
def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"seen": list(state["seen"])}, f, ensure_ascii=False, indent=2)


# 自动提取网站 favicon
def get_favicon_from_url(link):
    try:
        domain = urlparse(link).netloc
        if not domain:
            return None
        urls = favicon.get(f"https://{domain}")
        if urls:
            return urls[0].url
    except Exception:
        pass
    return None


# 抓取 RSS 新闻
def collect_latest_news(feeds):
    items = []
    for url in feeds:
        try:
            feed = feedparser.parse(url)
            source_name = feed.feed.get("title", urlparse(url).netloc)
            for e in feed.entries:
                title = e.get("title", "").strip()
                desc = sanitize_html_to_text(e.get("summary", "") or e.get("description", ""))
                link = e.get("link", "").strip()
                if not title or not link:
                    continue
                items.append({
                    "title": title,
                    "desc": desc,
                    "link": link,
                    "source": source_name,
                    "favicon": get_favicon_from_url(link)
                })
        except Exception as ex:
            print(f"[WARN] 无法解析 {url}: {ex}")
    return items


# HTML 邮件卡片模板
def format_html(items):
    card_html = ""
    for it in items:
        favicon_url = it.get("favicon") or "https://upload.wikimedia.org/wikipedia/commons/9/9a/Globe_icon.svg"
        source = it.get("source", "未知来源")
        card_html += f"""
        <div style="background:#fff;border-radius:12px;padding:18px 20px;
                    margin-bottom:16px;border:1px solid #e5e5e5;
                    box-shadow:0 2px 6px rgba(0,0,0,0.05);">
            <div style="display:flex;align-items:center;margin-bottom:10px;">
                <img src="{favicon_url}" alt="{source}" width="24" height="24"
                     style="margin-right:10px;object-fit:contain;border-radius:4px;">
                <span style="font-size:13px;color:#5f6368;">{source}</span>
            </div>
            <h3 style="margin:4px 0 8px 0;font-size:17px;color:#202124;line-height:1.4;">📰 {it['title']}</h3>
            <p style="color:#5f6368;font-size:14px;line-height:1.6;margin:0 0 10px 0;">
                {it['desc'][:200]}...
            </p>
            <a href="{it['link']}" target="_blank"
               style="display:inline-block;padding:8px 14px;background:#1a73e8;color:#fff;
                      text-decoration:none;border-radius:6px;font-size:13px;font-weight:500;">
               阅读原文 →
            </a>
        </div>
        """

    full_html = f"""
    <html>
      <body style="margin:0;padding:0;background:#f1f3f4;
                   font-family:'Segoe UI',Roboto,Arial,sans-serif;">
        <div style="max-width:720px;margin:40px auto;background:#fff;
                    border-radius:16px;padding:28px;
                    box-shadow:0 4px 12px rgba(0,0,0,0.05);">
          <h1 style="color:#1a73e8;margin:0 0 10px 0;">🗞️ 最新新闻更新</h1>
          <p style="color:#5f6368;font-size:15px;margin:0 0 28px 0;">
            以下是系统检测到的最新新闻摘要（共 {len(items)} 条）：
          </p>

          {card_html}

          <hr style="border:none;border-top:1px solid #e0e0e0;margin:30px 0;">
          <p style="text-align:center;color:#999;font-size:12px;margin:0;">
            本邮件由 GitHub Actions 自动发送 · {datetime.now().strftime("%Y-%m-%d %H:%M")}
          </p>
        </div>
      </body>
    </html>
    """
    return full_html


# ✅ 最终修复版：完全 UTF-8 安全发送
def send_mail(subject, html_body):
    smtp_server = os.environ.get("SMTP_SERVER")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USERNAME")
    smtp_pass = os.environ.get("SMTP_PASSWORD")
    to_email = os.environ.get("TO_EMAIL")
    from_email = os.environ.get("FROM_EMAIL", smtp_user)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr(("新闻监测机器人", from_email))
    msg["To"] = Header(to_email, "utf-8")
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(smtp_server, smtp_port, timeout=30) as s:
            s.ehlo()
            s.starttls()
            s.login(smtp_user, smtp_pass)
            # ✅ 用 send_message() 彻底避免 ASCII fallback
            s.send_message(msg)
        print("[INFO] 邮件发送成功 ✅")
    except Exception as e:
        print(f"[ERROR] 邮件发送失败: {e}")
        sys.exit(2)


def main():
    feeds = load_lines("feeds.txt")
    if not feeds:
        print("[ERROR] feeds.txt 为空")
        sys.exit(1)

    state = load_state()
    seen = state["seen"]
    all_news = collect_latest_news(feeds)

    new_items = []
    for it in all_news:
        fp = entry_fingerprint(it["title"], it["link"])
        if fp not in seen:
            new_items.append(it)
            seen.add(fp)

    if not new_items:
        print("[INFO] 无新内容。")
        save_state(state)
        return

    new_items = new_items[:MAX_ITEMS_PER_RUN]
    html_body = format_html(new_items)
    tz = timezone(timedelta(hours=8))
    now_str = datetime.now(tz).strftime("%Y-%m-%d %H:%M")
    subject = f"🗞️ 最新新闻更新（共 {len(new_items)} 条）· {now_str}"

    send_mail(subject, html_body)
    save_state(state)


if __name__ == "__main__":
    main()