import os, re, json
from openai import OpenAI
from fastapi import FastAPI, Header, HTTPException, Depends, Body
from pymongo import MongoClient

db = MongoClient(os.environ["MONGODB_URI"]).jobs  # module-level: reused across warm invocations
MODEL = os.getenv("MODEL", "gpt-4o-mini")

def auth(x_pass: str = Header("")):
    if x_pass != os.environ["APP_PASSWORD"]: raise HTTPException(401, "bad password")

app = FastAPI(dependencies=[Depends(auth)])

@app.get("/api/jobs")
def jobs(q: str = "", india: int = 0, remote: int = 0, intern: int = 0):
    f = {}
    if q: f["$or"] = [{k: {"$regex": re.escape(q), "$options": "i"}} for k in ("title", "company", "desc")]
    if india and not remote: f["india"] = True
    elif remote and not india: f["remote"] = True
    if intern: f["intern"] = True
    return list(db.jobs.find(f, {"desc": 0}).sort("seen", -1).limit(200))

@app.get("/api/profile")
def get_profile(): return (db.kv.find_one({"_id": "profile"}) or {}).get("v", {})

@app.post("/api/profile")
def set_profile(p: dict = Body(...)):
    db.kv.replace_one({"_id": "profile"}, {"v": p}, upsert=True); return p

SYS = """You tailor job applications. Use ONLY facts present in the candidate profile/resume. Never invent employers,
degrees, projects, numbers or skills; if the job needs something the candidate lacks, say so in match_notes instead.
Return ONLY JSON: {"match_notes":[strengths and gaps], "resume_summary":"3 lines", "highlight_bullets":[5 resume bullets
reworded from the resume to fit this job], "cover_letter":"<=250 words", "answers":{"why":"why this company/role",
"project":"most relevant project","additional":"anything else"}}"""

@app.post("/api/tailor/{jid}")
def tailor(jid: str):
    j, p = db.jobs.find_one({"_id": jid}), get_profile()
    if not j: raise HTTPException(404, "job not found")
    if not p.get("resume_text"): return {"error": "Paste your resume text into the profile first."}
    m = OpenAI().chat.completions.create(model=MODEL, max_tokens=2500, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": SYS}, {"role": "user",
        "content": f"JOB: {j['company']} - {j['title']} ({j['location']})\n{j['desc']}\n\nCANDIDATE:\n{json.dumps(p)}"}])
    return json.loads(m.choices[0].message.content)
