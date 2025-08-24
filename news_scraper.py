# -*- coding: utf-8 -*-
"""
news_scraper.py — 24小時內新文（星島《波盤王》／am730《波經》／東網足球）
需求：
- 星島、am730：每篇 2–3 句粵語摘要＋（如有）貼士
- 東網：只顯示「標題＋連結」，不出摘要／貼士
- 每來源最多 12 篇；過去 24 小時（HKT）
（本版：強化 on.cc 標題抽取：og:title -> <h1> -> .articleTitle -> 任何 class/id 含 title 的元素 -> <title>）
"""

import sys
import re
import datetime
from datetime import timezone, timedelta
import requests
import chardet
from bs4 import BeautifulSoup

HK_TZ = timezone(timedelta(hours=8))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/123.0 Safari/537.36"
}

SRC_STHEADLINE = ("stheadline", "星島頭條《波盤王》", "https://www.stheadline.com/football-betting/%E6%B3%A2%E7%9B%A4%E7%8E%8B")
SRC_AM730      = ("am730",      "am730《波經》",       "https://www.am730.com.hk/%E9%AB%94%E8%82%B2/%E6%B3%A2%E7%B6%93")
SRC_ONCC       = ("oncc",       "東網《足球快訊》",     None)

MAX_PER_SOURCE = 12

# ---------------- Utility ----------------

def hk_now():
    return datetime.datetime.now(HK_TZ)

def hk_today():
    return hk_now().date()

def fetch_html(url):
    resp = requests.get(url, headers=HEADERS, timeout=15)
    if "football.on.cc" in url:
        enc = (chardet.detect(resp.content).get("encoding") or "").lower()
        resp.encoding = "big5" if ("big5" in enc or not enc) else enc
    else:
        resp.encoding = resp.apparent_encoding or "utf-8"
    resp.raise_for_status()
    return resp.text

def _oncc_title_from_soup(soup):
    # 1) og:title
    ogt = soup.find("meta", property="og:title")
    if ogt and ogt.get("content"):
        t = ogt["content"].strip()
        if t: return t
    # 2) <h1>
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)
    # 3) 常見 class
    for sel in [".articleTitle", ".artTitle", ".newsTitle", ".title", "[class*='title']", "[id*='title']"]:
        node = soup.select_one(sel)
        if node:
            txt = node.get_text(" ", strip=True)
            if txt:
                return txt
    # 4) <title>
    if soup.title and soup.title.text.strip():
        return soup.title.text.strip()
    return None

def parse_title_summary(html, url):
    soup = BeautifulSoup(html, "lxml")
    if "football.on.cc" in url:
        title = _oncc_title_from_soup(soup) or url
    else:
        ogt = soup.find("meta", property="og:title")
        title = ogt.get("content").strip() if ogt and ogt.get("content") else (soup.title.text.strip() if soup.title else url)

    md = soup.find("meta", attrs={"name":"description"})
    desc = md.get("content").strip() if md and md.get("content") else None
    if not desc:
        p = soup.find("p")
        if p:
            desc = re.sub(r"\s+", " ", p.get_text(" ", strip=True)).strip()
    if not desc:
        desc = "（未能擷取摘要）"
    return title, desc, soup

DATE_PAT = re.compile(r"(20\d{2})[-/年\.](\d{1,2})[-/月\.](\d{1,2})")

def try_parse_any_date(s):
    s = (s or "").strip()
    try:
        if "T" in s:
            return datetime.datetime.fromisoformat(s.replace("Z","+00:00")).astimezone(HK_TZ)
    except Exception:
        pass
    m = DATE_PAT.search(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime.datetime(y, mo, d, tzinfo=HK_TZ)
        except Exception:
            pass
    return None

def try_parse_date_from_html(soup):
    m = soup.find("meta", {"property":"article:published_time"}) or soup.find("meta", {"name":"article:published_time"})
    if m and m.get("content"):
        try:
            return datetime.datetime.fromisoformat(m["content"].replace("Z","+00:00")).astimezone(HK_TZ)
        except Exception:
            pass
    for sel in ["time[datetime]", "meta[itemprop='datePublished']", "span.time", "span.date", "div.date", "p.date"]:
        node = soup.select_one(sel)
        if node:
            txt = (node.get("datetime") or node.get("content") or node.get_text(" ", strip=True) or "").strip()
            dt = try_parse_any_date(txt)
            if dt:
                return dt
    txt = soup.get_text(" ", strip=True)
    dt = try_parse_any_date(txt)
    return dt

def try_parse_date_from_url(url):
    m = DATE_PAT.search(url)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime.datetime(y, mo, d, tzinfo=HK_TZ)
        except Exception:
            pass
    return None

def in_last_24h(dt_hk, now_hk):
    if not dt_hk:
        return False
    return (now_hk - dt_hk) <= datetime.timedelta(hours=24) and dt_hk <= now_hk

# ---------------- Extraction & Summaries ----------------

LEAGUE_WORDS = r"(英超|英冠|西甲|意甲|德甲|法甲|港超|沙特超|歐聯|歐霸|足總盃|聯賽盃|亞冠|世盃外|歐國盃|友賽|季前賽|日職|韓K)"
TIME_PAT = re.compile(r"(\d{1,2}[:：]\d{2})\s*(HKT|香港時間|本港時間)?", re.I)
VS_PAT = re.compile(r"([A-Za-z\u4e00-\u9fa5\u3400-\u4dbf\.·]+)\s*(?:vs\.?|對|鬥|迎戰|對陣)\s*([A-Za-z\u4e00-\u9fa5\u3400-\u4dbf\.·]+)")

TIP_PATTERNS = [
    (re.compile(r"主勝|主隊勝|坐和望贏"), "主勝"),
    (re.compile(r"客勝|客隊勝"), "客勝"),
    (re.compile(r"讓[一二兩]球半?|受讓|受[一二兩]球|上盤|下盤|讓球|受讓盤"), "讓球/受讓"),
    (re.compile(r"大\s*2\.5|大盤|大波|入球.*多"), "大 2.5"),
    (re.compile(r"小\s*2\.5|小盤|小波|入球.*少"), "小 2.5"),
]
INJ_PAT = re.compile(r"(傷缺|傷停|停賽|傷患|復出|傷癒|缺陣|掛牌|紅牌|黃牌累積)")

def extract_tips(text):
    tips = []
    for pat, tag in TIP_PATTERNS:
        if pat.search(text) and tag not in tips:
            tips.append(tag)
    return tips

def build_cn_summary(title, desc, full_text):
    src = (title or "") + "｜" + (desc or "") + "｜" + (full_text or "")
    vs = None
    m = VS_PAT.search(src)
    if m:
        vs = f"{m.group(1).strip()} 對 {m.group(2).strip()}"
    league = None
    m2 = re.search(LEAGUE_WORDS, src)
    if m2:
        league = m2.group(1)
    kickoff = None
    m3 = TIME_PAT.search(src)
    if m3:
        kickoff = m3.group(1)
    tips = extract_tips(src)
    inj = None
    if INJ_PAT.search(src):
        inj = "文中提及陣容／傷停因素。"

    sents = []
    if vs and league:
        sents.append(f"{league} 對碰：{vs}。")
    elif vs:
        sents.append(f"對碰：{vs}。")
    elif league:
        sents.append(f"賽事：{league}。")
    if kickoff:
        sents.append(f"開賽時間（本港）：{kickoff}。")

    core = desc if desc and desc != "（未能擷取摘要）" else ""
    if core:
        core = re.sub(r"\s+", " ", core).strip()
        if len(core) > 180:
            cut = core.rfind("。", 100, 180)
            core = (core[:cut] if cut != -1 else core[:160]).strip() + "…"
        sents.append(core)

    if tips:
        sents.append("貼士傾向：" + "、".join(tips) + "。")
    if inj and len(sents) < 3:
        sents.append("另有提及傷停／人腳變動。")

    if not sents:
        sents = [ (desc or "（未能擷取摘要）") ]
    summary = " ".join([x for x in sents if x]).strip()
    return summary, tips

# ---------------- Site scrapers ----------------

def grab_stheadline(now_hk):
    url = SRC_STHEADLINE[2]
    out = []
    try:
        soup = BeautifulSoup(fetch_html(url), "lxml")
        anchors = soup.find_all("a", href=True)
        links, seen = [], set()
        for a in anchors:
            href = a["href"]; text = a.get_text(strip=True)
            if not text: continue
            if "/football-betting/" in href:
                if href.startswith("/"):
                    href = "https://www.stheadline.com" + href
                if href not in seen:
                    seen.add(href); links.append(href)
        for href in links[:60]:
            try:
                html = fetch_html(href)
                title, desc, soup_a = parse_title_summary(html, href)
                pub_dt = try_parse_date_from_html(soup_a) or try_parse_date_from_url(href)
                if not pub_dt or not in_last_24h(pub_dt, now_hk):
                    continue
                full_text = soup_a.get_text(" ", strip=True)
                cn_sum, tips = build_cn_summary(title, desc, full_text)
                out.append({"site":"stheadline","url":href,"title":title,"summary":cn_sum,"tips":tips,"pubDate":pub_dt.isoformat()})
            except Exception:
                continue
    except Exception:
        pass
    return out[:MAX_PER_SOURCE]

def grab_am730(now_hk):
    url = SRC_AM730[2]
    out = []
    try:
        soup = BeautifulSoup(fetch_html(url), "lxml")
        anchors = soup.find_all("a", href=True)
        links, seen = [], set()
        for a in anchors:
            href = a["href"]; text = a.get_text(strip=True)
            if not text: continue
            if "/體育/" in href or "/%E9%AB%94%E8%82%B2/" in href:
                if href.startswith("/"):
                    href = "https://www.am730.com.hk" + href
                if href not in seen:
                    seen.add(href); links.append(href)
        for href in links[:60]:
            try:
                html = fetch_html(href)
                title, desc, soup_a = parse_title_summary(html, href)
                pub_dt = try_parse_date_from_html(soup_a) or try_parse_date_from_url(href)
                if not pub_dt or not in_last_24h(pub_dt, now_hk):
                    continue
                full_text = soup_a.get_text(" ", strip=True)
                cn_sum, tips = build_cn_summary(title, desc, full_text)
                out.append({"site":"am730","url":href,"title":title,"summary":cn_sum,"tips":tips,"pubDate":pub_dt.isoformat()})
            except Exception:
                continue
    except Exception:
        pass
    return out[:MAX_PER_SOURCE]

def grab_oncc_for_date(d: datetime.date, now_hk):
    """只回傳標題＋連結（不做摘要/貼士），加強 on.cc 標題抽取"""
    ymd = d.strftime("%Y%m%d")
    base = f"https://football.on.cc/cnt/news/newa/{ymd}/"
    out = []
    for i in range(1, 101):
        slug = f"fbnewa01{i:02d}x0.html"
        href = base + slug
        try:
            html = fetch_html(href)
        except Exception:
            break  # 該日已無更多
        pub_dt = datetime.datetime(d.year, d.month, d.day, tzinfo=HK_TZ)
        if not in_last_24h(pub_dt, now_hk):
            continue
        title, _desc, _soup = parse_title_summary(html, href)
        out.append({
            "site":"oncc","url":href,"title":title,
            "summary":"", "tips":[], "pubDate":pub_dt.isoformat()
        })
    return out[:MAX_PER_SOURCE]

# ---------------- HTML ----------------

def build_html(date_str, bundles):
    def esc(s): return (s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")
    total = sum(len(x[2]) for x in bundles)
    css = """
    body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,'Noto Sans TC','PingFang TC','Microsoft JhengHei',sans-serif;background:#f8fafc;color:#0f172a;}
    .wrap{max-width:1000px;margin:32px auto;padding:0 16px;}
    .src{display:flex;align-items:center;gap:8px;margin:16px 0 8px;}
    .pill{display:inline-block;padding:4px 10px;border-radius:999px;font-size:12px;font-weight:600;border:1px solid}
    .p-st{background:#ecfdf5;color:#065f46;border-color:#a7f3d0;}
    .p-am{background:#eff6ff;color:#1e40af;border-color:#bfdbfe;}
    .p-on{background:#fffbeb;color:#92400e;border-color:#fde68a;}
    ul{margin:0 0 18px 20px}
    li{margin:10px 0;padding:10px;border:1px solid #e5e7eb;border-radius:12px;background:#fff}
    a{color:#0ea5e9;text-decoration:underline;font-weight:700}
    .muted{color:#64748b;font-size:12px}
    .badges span{display:inline-block;border:1px solid #e5e7eb;border-radius:999px;background:#f1f5f9;padding:2px 8px;margin-right:6px;font-size:12px}
    """
    html = [f"<!DOCTYPE html><html lang='zh-Hant'><head><meta charset='utf-8'>",
            f"<meta name='viewport' content='width=device-width, initial-scale=1'>",
            f"<title>每日足球新聞摘要 {esc(date_str)}</title><style>{css}</style></head><body>",
            "<div class='wrap'>",
            f"<h1 style='margin:0'>每日足球新聞摘要</h1>",
            f"<div class='muted'>香港時間 {esc(date_str)} 內最近 24 小時新文；每來源最多 12 篇。</div>"]
    for sid, sname, items in bundles:
        pill = "p-st" if sid=="stheadline" else ("p-am" if sid=="am730" else "p-on")
        html.append(f"<div class='src'><span class='pill {pill}'>{esc(sname)}</span><span class='muted'>{len(items)} 篇</span></div>")
        if not items:
            html.append("<div class='muted'>今日未見更新</div>")
        else:
            html.append("<ul>")
            for it in items:
                line1 = f"【{esc(sname)}】—「{esc(it['title'])}」"
                html.append("<li>")
                html.append(f"<div>{line1}　<a href='{esc(it['url'])}' target='_blank' rel='noopener'>（直達連結）</a></div>")
                if sid != "oncc":
                    if it.get("summary"):
                        html.append(f"<div style='margin-top:6px'>{esc(it['summary'])}</div>")
                    if it.get("tips"):
                        html.append("<div class='badges' style='margin-top:6px'>")
                        for t in it["tips"]:
                            html.append(f"<span>{esc(t)}</span>")
                        html.append("</div>")
                html.append("</li>")
            html.append("</ul>")
    html.append("</div></body></html>")
    return "\n".join(html)

# ---------------- Main ----------------

def main():
    if len(sys.argv) >= 2:
        try:
            base_date = datetime.date.fromisoformat(sys.argv[1])
        except Exception:
            print("[!] 日期格式錯誤，請用 YYYY-MM-DD，例如：2025-08-24")
            sys.exit(1)
    else:
        base_date = hk_today()

    now_hk = hk_now()
    date_str = now_hk.strftime("%Y-%m-%d %H:%M（HKT）")

    st_items = grab_stheadline(now_hk)
    am_items = grab_am730(now_hk)
    on_items = grab_oncc_for_date(base_date, now_hk)

    html = build_html(date_str, [
        (SRC_STHEADLINE[0], SRC_STHEADLINE[1], st_items),
        (SRC_AM730[0],      SRC_AM730[1],      am_items),
        (SRC_ONCC[0],       SRC_ONCC[1],       on_items),
    ])

    out_name = f"news_summary_{base_date.strftime('%Y%m%d')}.html"
    with open(out_name, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[+] 已輸出：{out_name}")

if __name__ == "__main__":
    main()
