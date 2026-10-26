import os, re, json, html, time, asyncio
import httpx
from pymongo import MongoClient

CFG = json.load(open("config.json")); SRC, ALERT = CFG["sources"], CFG["alert"]
INDIA = re.compile(r"india|bengaluru|bangalore|mumbai|delhi|gurugram|gurgaon|noida|hyderabad|pune|chennai|kolkata|ahmedabad", re.I)
clean = lambda s: re.sub(r"<[^>]+>", " ", html.unescape(s or ""))[:6000]

async def fetch(cli, kind, t):
    try:
        if kind == "greenhouse":
            r = (await cli.get(f"https://boards-api.greenhouse.io/v1/boards/{t}/jobs?content=true")).json()["jobs"]
            return [dict(id=f"gh{j['id']}", title=j["title"], location=j["location"]["name"], url=j["absolute_url"], desc=j.get("content")) for j in r]
        if kind == "lever":
            r = (await cli.get(f"https://api.lever.co/v0/postings/{t}?mode=json")).json()
            return [dict(id="lv" + j["id"], title=j["text"], location=j["categories"].get("location") or "", url=j["hostedUrl"],
                         desc=j.get("descriptionPlain"), remote=j.get("workplaceType") == "remote") for j in r]
        if kind == "amazon":  # t = search term; India only
            r = (await cli.get("https://www.amazon.jobs/en/search.json", params={"base_query": t, "loc_query": "India", "country": "IND", "result_limit": 50, "sort": "recent"})).json()["jobs"]
            return [dict(id="am" + str(j["id_icims"]), title=j["title"], location=j.get("normalized_location") or j.get("location") or "",
                         url="https://www.amazon.jobs" + j["job_path"], desc=j.get("description_short") or j.get("description")) for j in r]
        r = (await cli.get(f"https://api.ashbyhq.com/posting-api/job-board/{t}")).json()["jobs"]
        return [dict(id="ab" + j["id"], title=j["title"], location=j.get("location") or "", url=j["jobUrl"],
                     desc=j.get("descriptionPlain"), remote=bool(j.get("isRemote"))) for j in r]
    except Exception:
        return []  # bad token or API change: skip quietly

def wanted(j):
    kws = [k.lower() for k in ALERT.get("keywords", [])]
    if kws and not any(k in f"{j['title']} {j['company']}".lower() for k in kws): return False
    if ALERT.get("intern") and not j["intern"]: return False
    return bool((ALERT.get("india") and j["india"]) or (ALERT.get("remote") and j["remote"]))

def send(text):
    e = os.getenv
    if e("TELEGRAM_BOT_TOKEN") and e("TELEGRAM_CHAT_ID"):
        for i in range(0, len(text), 3900):
            httpx.post(f"https://api.telegram.org/bot{e('TELEGRAM_BOT_TOKEN')}/sendMessage", timeout=20,
                       json={"chat_id": e("TELEGRAM_CHAT_ID"), "text": text[i:i + 3900], "disable_web_page_preview": True}).raise_for_status()
    if e("RESEND_API_KEY") and e("EMAIL_TO"):
        httpx.post("https://api.resend.com/emails", timeout=20, headers={"Authorization": f"Bearer {e('RESEND_API_KEY')}"},
                   json={"from": e("EMAIL_FROM") or "Job Alerts <onboarding@resend.dev>", "to": [x.strip() for x in e("EMAIL_TO").split(",")],
                         "subject": text.split("\n")[0], "text": text}).raise_for_status()

async def scan():
    pairs = [(k, t) for k, ts in SRC.items() for t in ts]
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as cli:
        res = await asyncio.gather(*[fetch(cli, k, t) for k, t in pairs])
    docs, now = {}, time.time()
    for (k, t), jobs in zip(pairs, res):
        for j in jobs:
            loc = j["location"] or ""
            india = bool(INDIA.search(loc)); remote = bool(j.get("remote")) or "remote" in f"{loc} {j['title']}".lower()
            if not (india or remote): continue
            intern = bool(re.search(r"intern|trainee|graduate|entry|junior|associate", j["title"], re.I))
            docs[j["id"]] = dict(_id=j["id"], company="amazon" if k == "amazon" else t, title=j["title"], location=loc,
                                 url=j["url"], desc=clean(j["desc"]), remote=remote, india=india, intern=intern, seen=now, last=now)
    col = MongoClient(os.environ["MONGODB_URI"]).jobs.jobs
    first = col.estimated_document_count() == 0  # first run only seeds the DB: no alert flood
    have = {d["_id"] for d in col.find({}, {"_id": 1})}
    new = [d for i, d in docs.items() if i not in have]
    hits = [] if first else [j for j in new if wanted(j)]
    print(f"{len(docs)} matching jobs, {len(new)} new, {len(hits)} to alert")
    if hits:  # no new matching postings = no message at all
        lines = [f"{len(hits)} new job(s) posted"] + [f"\n{j['title']} - {j['company']} ({j['location'] or 'Remote'})\n{j['url']}" for j in hits[:30]]
        if len(hits) > 30: lines.append(f"\n...and {len(hits) - 30} more in your job board")
        send("\n".join(lines))
    if new: col.insert_many(new, ordered=False)  # saved only after the alert went out, so a failed send retries next scan
    if docs: col.update_many({"_id": {"$in": list(docs)}}, {"$set": {"last": now}})
    col.delete_many({"last": {"$lt": now - 30 * 86400}})  # drop jobs no longer listed for 30 days

asyncio.run(scan())
