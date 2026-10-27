import os, re, json, html, time, asyncio
import httpx
from pymongo import MongoClient

with open("config.json", encoding="utf-8") as f:
    CFG = json.load(f)

SRC, ALERT = CFG["sources"], CFG["alert"]

INDIA = re.compile(
    r"\b(?:india|bengaluru|bangalore|mumbai|delhi|new delhi|"
    r"gurugram|gurgaon|noida|hyderabad|pune|chennai|kolkata|"
    r"ahmedabad|kochi|cochin|mysuru|mysore|thiruvananthapuram|"
    r"trivandrum|jaipur|indore|lucknow|chandigarh|bhubaneswar|"
    r"coimbatore|visakhapatnam|vizag|nagpur|surat)\b",
    re.I
)
REMOTE = re.compile(
    r"\b(?:fully remote|100\s*%\s*remote|remote|work from anywhere|"
    r"work from home|wfh)\b", re.I
)
HYBRID = re.compile(
    r"\b(?:hybrid|flexible workplace|partially remote|partly remote)\b",
    re.I
)
INTERNSHIP = re.compile(r"\b(?:internship|intern|interns)\b", re.I)
FRESHER = re.compile(
    r"\b(?:fresher|freshers|new grad(?:uate)?s?|recent graduates?|"
    r"graduate trainee|graduate engineer trainee|graduate program|"
    r"graduate programme|entry[ -]level|early career|"
    r"no experience required|no prior experience|without experience)\b",
    re.I
)
ZERO_YEARS = re.compile(
    r"\b(?:0|zero)\s*(?:[-–to]+\s*\d+\s*)?(?:\+?\s*)?"
    r"(?:years?|yrs?)(?:\s+of\s+experience)?\b|"
    r"\bno (?:prior |previous )?experience\b|"
    r"\bexperience\s*:?\s*0\s*years?\b",
    re.I
)

def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or "")))[:6000]

async def fetch(cli, kind, t):
    try:
        if kind == "greenhouse":
            r = (await cli.get(
                f"https://boards-api.greenhouse.io/v1/boards/{t}/jobs?content=true"
            )).json()["jobs"]
            return [
                dict(id=f"gh{j['id']}", title=j["title"],
                     location=(j.get("location") or {}).get("name") or "",
                     url=j["absolute_url"], desc=j.get("content"),
                     remote=False, hybrid=False)
                for j in r
            ]
        if kind == "lever":
            r = (await cli.get(
                f"https://api.lever.co/v0/postings/{t}?mode=json"
            )).json()
            return [
                dict(id="lv" + j["id"], title=j["text"],
                     location=(j.get("categories") or {}).get("location") or "",
                     url=j["hostedUrl"], desc=j.get("descriptionPlain"),
                     remote=(j.get("workplaceType") or "").lower() == "remote",
                     hybrid=(j.get("workplaceType") or "").lower() == "hybrid")
                for j in r
            ]
        if kind == "amazon":
            r = (await cli.get(
                "https://www.amazon.jobs/en/search.json",
                params={"base_query": t, "loc_query": "India", "country": "IND",
                        "result_limit": 50, "sort": "recent"}
            )).json()["jobs"]
            return [
                dict(id="am" + str(j["id_icims"]), title=j["title"],
                     location=j.get("normalized_location") or j.get("location") or "",
                     url="https://www.amazon.jobs" + j["job_path"],
                     desc=j.get("description_short") or j.get("description"),
                     remote=False, hybrid=False)
                for j in r
            ]

        r = (await cli.get(
            f"https://api.ashbyhq.com/posting-api/job-board/{t}"
        )).json()["jobs"]
        return [
            dict(id="ab" + j["id"], title=j["title"],
                 location=j.get("location") or "", url=j["jobUrl"],
                 desc=j.get("descriptionPlain"),
                 remote=bool(j.get("isRemote")),
                 hybrid=bool(j.get("isHybrid", False)))
            for j in r
        ]
    except Exception as exc:
        # Keep a failed source from stopping the rest of the scan.
        print(f"Source fetch failed ({kind}:{t}): {exc}")
        return []

def classify_job(j):
    title = j.get("title", "")
    desc = clean(j.get("desc", ""))
    location = j.get("location", "")
    text = f"{title}\n{desc}"
    workplace_text = f"{title}\n{location}\n{desc}"

    india = bool(INDIA.search(location))
    remote = bool(j.get("remote")) or bool(REMOTE.search(workplace_text))
    hybrid = bool(j.get("hybrid")) or bool(HYBRID.search(workplace_text))

    work_modes = set()
    if india:
        work_modes.add("india")
    if remote:
        work_modes.add("remote")
    if hybrid:
        work_modes.add("hybrid")

    levels = set()
    if INTERNSHIP.search(title):
        levels.add("internship")
    if FRESHER.search(text):
        levels.add("fresher")
    if ZERO_YEARS.search(text):
        levels.add("0_years")

    return {
        "india": india,
        "remote": remote,
        "hybrid": hybrid,
        "work_modes": sorted(work_modes),
        "experience_levels": sorted(levels),
        "intern": "internship" in levels,
        "desc": desc,
    }

def wanted(j):
    keywords = [
        k.lower().strip()
        for k in ALERT.get("keywords", [])
        if isinstance(k, str) and k.strip()
    ]
    if keywords:
        searchable = (
            f"{j.get('title', '')} {j.get('company', '')} "
            f"{j.get('desc', '')}"
        ).lower()
        if not any(k in searchable for k in keywords):
            return False

    selected_modes = set(ALERT.get("work_modes", ["india", "remote"]))
    selected_levels = set(ALERT.get("experience_levels", ["internship"]))

    # OR within each group: any selected location mode and any selected
    # experience level is enough. Empty group means no restriction.
    if selected_modes and not selected_modes.intersection(j.get("work_modes", [])):
        return False
    if selected_levels and not selected_levels.intersection(j.get("experience_levels", [])):
        return False
    return True

def send(text):
    e = os.getenv
    if e("TELEGRAM_BOT_TOKEN") and e("TELEGRAM_CHAT_ID"):
        for i in range(0, len(text), 3900):
            httpx.post(
                f"https://api.telegram.org/bot{e('TELEGRAM_BOT_TOKEN')}/sendMessage",
                timeout=20,
                json={"chat_id": e("TELEGRAM_CHAT_ID"), "text": text[i:i + 3900],
                      "disable_web_page_preview": True}
            ).raise_for_status()
    if e("RESEND_API_KEY") and e("EMAIL_TO"):
        httpx.post(
            "https://api.resend.com/emails", timeout=20,
            headers={"Authorization": f"Bearer {e('RESEND_API_KEY')}"},
            json={"from": e("EMAIL_FROM") or "Job Alerts <onboarding@resend.dev>",
                  "to": [x.strip() for x in e("EMAIL_TO").split(",") if x.strip()],
                  "subject": text.split("\n")[0], "text": text}
        ).raise_for_status()

async def scan():
    pairs = [(k, t) for k, ts in SRC.items() for t in ts]
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as cli:
        res = await asyncio.gather(*[fetch(cli, k, t) for k, t in pairs])

    docs, now = {}, time.time()
    for (kind, token), jobs in zip(pairs, res):
        for job in jobs:
            classified = classify_job(job)
            if not classified["india"] and not classified["remote"]:
                continue
            docs[job["id"]] = dict(
                _id=job["id"],
                company="amazon" if kind == "amazon" else token,
                title=job["title"],
                location=job.get("location") or "",
                url=job["url"],
                seen=now,
                **classified
            )

    client = MongoClient(os.environ["MONGODB_URI"], serverSelectionTimeoutMS=10000)
    try:
        col = client.jobs.jobs
        first = col.estimated_document_count() == 0
        have = {d["_id"] for d in col.find({}, {"_id": 1})}
        new = [d for key, d in docs.items() if key not in have]
        hits = [] if first else [j for j in new if wanted(j)]

        print(f"{len(docs)} jobs fetched, {len(new)} new, {len(hits)} to alert")
        if hits:
            lines = [f"{len(hits)} new matching job(s)"]
            for j in hits[:30]:
                modes = ", ".join(j["work_modes"]) or "work mode unspecified"
                levels = ", ".join(j["experience_levels"]) or "experience level unspecified"
                lines.append(
                    f"\n{j['title']} - {j['company']} "
                    f"({j['location'] or 'Location not specified'})"
                    f"\nWork mode: {modes} | Level: {levels}\n{j['url']}"
                )
            if len(hits) > 30:
                lines.append(f"\n...and {len(hits) - 30} more in your job board")
            send("\n".join(lines))

        # Insert only after notification succeeds, so a failed send can retry.
        if new:
            col.insert_many(new, ordered=False)
        col.delete_many({"india": {"$ne": True}, "remote": {"$ne": True}})
        col.delete_many({"seen": {"$lt": now - 10 * 86400}})
    finally:
        client.close()

if __name__ == "__main__":
    asyncio.run(scan())
