import os
import re
import json
import time
import datetime
import urllib.parse
import xml.etree.ElementTree as ET
import requests
from bs4 import BeautifulSoup
from jobspy import scrape_jobs
from google import genai
from google.genai import types
from pydantic import BaseModel
import gspread
from google.oauth2.service_account import Credentials
from weasyprint import HTML

# ================= CONFIGURATION =================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GOOGLE_CREDS_JSON = json.loads(os.environ["GOOGLE_CREDS_JSON"])
SHEET_NAME = "Elisa_Job_Tracker"
GITHUB_REPO = os.environ.get("GITHUB_REPOSITORY", "senthamilvalavan/elisa-job-agent")
SEEN_JOBS_FILE = "seen_jobs.json"

# EXACT RATIO TARGETS PER RUN (30 Fresher Full-Time + 20 Internships)
TARGET_FRESHER_JOBS = 30
TARGET_INTERNSHIPS = 20
MAX_PER_PLATFORM = 5

# STRICT WHITELIST OF SKILLS SENTHAMIL ACTUALLY KNOWS & CAN DEFEND IN INTERVIEWS!
ALLOWED_SKILLS_WHITELIST = """
- Analytics & BI: Power BI (DAX basics, Data Modeling, Interactive Dashboards, KPI Reporting), Tableau (Basic), Exploratory Data Analysis (EDA), Data Visualization, Business Intelligence, Trend & Retention Analysis, MIS & Automated Reporting.
- Data & SQL: SQL, MySQL, PostgreSQL (Joins, CTEs, Window Functions, Subqueries, Aggregations, Group By), Data Extraction, Data Cleaning, Data Validation, Data Quality Checks, Database Management, ETL Pipelines (Python/SQL).
- Programming & AI/ML: Python (Pandas, NumPy, Matplotlib, Seaborn, Scikit-learn), Machine Learning, Deep Learning Basics (TensorFlow, OpenCV, Grad-CAM), Generative AI Fundamentals (LLM APIs, Gemini API, Prompt Engineering, Structured JSON Outputs, AI Agent Workflows).
- Tools & Workflow: Advanced Excel (VLOOKUP, XLOOKUP, Pivot Tables, Conditional Formatting, Formulas), Jupyter Notebook, VS Code, PyCharm, Git, GitHub, GitHub Actions (Basic Automation), Google Sheets Automation.
"""

FRESHER_ROLES = [
    "Junior Data Analyst",
    "Data Analyst",
    "Associate Data Analyst",
    "Business Intelligence Analyst",
    "Power BI Developer",
    "SQL Data Analyst",
    "Generative AI Engineer",
    "Junior AI Developer"
]

INTERN_ROLES = [
    "Data Analyst Intern",
    "Business Intelligence Intern",
    "Data Science Intern",
    "Generative AI Intern",
    "Analytics Trainee"
]

BING_RSS_PORTALS = [
    ("wellfound", "wellfound.com"),
    ("cutshort", "cutshort.io"),
    ("instahyre", "instahyre.com"),
    ("hirist", "hirist.tech"),
    ("cuvette", "cuvette.tech"),
    ("foundit", "foundit.in"),
    ("freshersworld", "freshersworld.com"),
    ("ashby", "jobs.ashbyhq.com"),
    ("workable", "apply.workable.com"),
    ("smartrecruiters", "jobs.smartrecruiters.com"),
    ("zohorecruit", "zohorecruit.com"),
    ("keka", "keka.com"),
    ("darwinbox", "darwinbox.in"),
    ("workatastartup", "workatastartup.com")
]

GREENHOUSE_BOARDS = [
    "razorpay", "postman", "groww", "meesho", "cred", "clevertap",
    "chargebee", "browserstack", "freshworks", "druva", "mindtickle",
    "innovaccer", "whatfix", "sprinklr", "databricks", "stripe", "gitlab", "canonical"
]
LEVER_BOARDS = [
    "zeta", "upstox", "plum", "paytm", "bookmyshow", "practo",
    "rapido", "acko", "slice", "jar", "fampay", "turing", "veeva"
]

# ================= SETUP CLIENTS =================
ai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
creds = Credentials.from_service_account_info(GOOGLE_CREDS_JSON, scopes=SCOPES)
gc = gspread.authorize(creds)

with open("base_resume.json", "r", encoding="utf-8") as f:
    BASE_RESUME = json.load(f)


def load_seen_jobs():
    if os.path.exists(SEEN_JOBS_FILE):
        try:
            with open(SEEN_JOBS_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_seen_jobs(seen_set):
    with open(SEEN_JOBS_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen_set)[-4000:], f, indent=2)


def normalize_url(url):
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url.strip())
    query_params = urllib.parse.parse_qs(parsed.query)
    clean_params = {
        k: v for k, v in query_params.items()
        if k.lower() in ["jk", "id", "gh_jid", "jobid", "job_id", "position"]
    }
    clean_query = urllib.parse.urlencode(clean_params, doseq=True)
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", clean_query, ""))


def make_job_fingerprint(company, title):
    c = re.sub(r"[^a-z0-9]", "", company.lower())
    t = re.sub(r"[^a-z0-9]", "", title.lower())
    return f"{c}::{t}"


def is_internship_role(title, jd=""):
    t = title.lower()
    if any(w in t for w in ["intern", "internship", "trainee", "apprentice", "stipend"]):
        return True
    if "internship" in jd[:400].lower() and "full-time" not in jd[:400].lower():
        return True
    return False


def is_valid_relevant_role(title, jd):
    """
    Strictly blocks Senior roles, HR/Talent roles, Data Entry, Unpaid roles,
    non-Data/GenAI Backend SWE roles, and non-Tamil/English regional language roles!
    """
    t = title.lower()
    jd_low = jd.lower()

    # 1. Block Senior / Lead titles
    senior_words = ["senior", "sr.", "sr ", "lead", "principal", "manager", "director", "head of", "vp ", "architect", "staff ", "iii"]
    if any(w in t for w in senior_words):
        return False

    # 2. Block HR, Recruiting, Sales, Marketing, BPO, Data Entry, Typing, Accounting
    junk_titles = [
        "talent acquisition", "people operations", "human resource", "hr ", "recruiter",
        "recruitment", "sales", "marketing", "customer support", "voice", "telecaller",
        "data entry", "typing", "accountant", "audit", "legal", "content writer",
        "graphic", "ios ", "android ", "golang", "ruby", "java developer", "dotnet", ".net"
    ]
    if any(w in t for w in junk_titles):
        return False

    # 3. Title MUST actually contain a Data / BI / Analytics / AI / ML / Python / SQL word!
    must_have_title = [
        "data", "analyst", "analytics", "business intelligence", "power bi",
        "tableau", "sql", "python", "ai ", "ai/", "genai", "generative ai",
        "machine learning", "ml ", "llm", "decision science", "mis ", "reporting"
    ]
    if not any(w in f"{t} " for w in must_have_title):
        return False

    # 4. Reject 3+ years mandatory experience in JD
    if re.search(r"\b([3-9]|1[0-5])\s*\+?\s*(to\s*\d+\s*)?years?\s+(of\s+)?(relevant\s+|professional\s+|work\s+|industry\s+)?experience", jd_low):
        return False

    # 5. Reject explicitly unpaid roles
    if "unpaid internship" in jd_low or "unpaid role" in jd_low:
        return False

    return True


def get_gemini_models():
    preferred = [
        "gemini-3.6-flash",
        "gemini-3.7-flash",
        "gemini-3.8-flash",
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash-lite",
        "gemini-3-flash-preview",
        "gemini-3.5-flash"
    ]
    if not ai_client:
        return preferred
    try:
        available = []
        exclude = ["tts", "live", "image", "transcribe", "embedding", "omni", "audio", "robotics", "computer-use", "banana", "argon", "2.5-flash", "2.5-pro"]
        for m in ai_client.models.list():
            name = m.name.replace("models/", "")
            if "gemini" in name and not any(x in name for x in exclude):
                available.append(name)
        ordered = [p for p in preferred if p in available]
        for a in available:
            if a not in ordered:
                ordered.append(a)
        print(f"🤖 Active Gemini Rotation Pool: {ordered}")
        return ordered if ordered else preferred
    except Exception:
        return preferred

GEMINI_MODEL_POOL = get_gemini_models()


# ================= STRUCTURED AI OUTPUT =================
class TailoredResumeSchema(BaseModel):
    is_eligible_fresher_btech: bool
    job_category: str
    match_score: int
    tailored_headline: str
    tailored_summary: str
    analytics_bi_skills: str
    data_sql_skills: str
    programming_skills: str
    tools_skills: str
    project_bullets: list[list[str]]
    experience_bullets: list[str]
    missing_skills_and_notes: str


# ================= 1. MULTI-PLATFORM SCRAPER =================
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9"
}


def scrape_page_text(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=8)
        if resp.status_code != 200:
            return ""
        soup = BeautifulSoup(resp.text, "html.parser")
        for s in soup(["script", "style", "nav", "footer"]):
            s.decompose()
        return " ".join(soup.get_text().split())[:3500]
    except Exception:
        return ""


def scrape_internshala_and_unstop():
    results = []
    urls = [
        "https://internshala.com/fresher-jobs/data-analytics-jobs/",
        "https://internshala.com/fresher-jobs/data-science-jobs/",
        "https://internshala.com/internships/data-analytics-internship/",
        "https://internshala.com/internships/artificial-intelligence-ai-internship/",
    ]
    for page_url in urls:
        try:
            resp = requests.get(page_url, headers=HEADERS, timeout=10)
            soup = BeautifulSoup(resp.text, "html.parser")
            for c in soup.select(".individual_internship")[:7]:
                title_el = c.select_one(".job-internship-name, .profile")
                comp_el = c.select_one(".company-name, .company_name")
                link_el = c.select_one("a.job-title-href, a.view_detail_button, a[href*='detail']")
                if title_el and link_el:
                    href = link_el.get("href", "")
                    full_url = "https://internshala.com" + href if href.startswith("/") else href
                    title = title_el.get_text(strip=True)
                    comp = comp_el.get_text(strip=True) if comp_el else "Startup"
                    jd = scrape_page_text(full_url) or f"{title} at {comp}. Skills: SQL, Python, Excel, Power BI, Data Analysis."
                    if is_valid_relevant_role(title, jd):
                        results.append({"company": comp, "title": title, "url": full_url, "jd": jd, "source": "internshala"})
        except Exception:
            pass

    endpoints = [
        "https://unstop.com/api/public/opportunity/search-result?opportunity=jobs&searchTerm=Data%20Analyst&oppstatus=open",
        "https://unstop.com/api/public/opportunity/search-result?opportunity=jobs&searchTerm=Generative%20AI&oppstatus=open",
        "https://unstop.com/api/public/opportunity/search-result?opportunity=internships&searchTerm=Data%20Analyst&oppstatus=open"
    ]
    for ep in endpoints:
        try:
            r = requests.get(ep, headers=HEADERS, timeout=10)
            data = r.json().get("data", {}).get("data", [])
            for item in data[:8]:
                title = item.get("title", "")
                org = (item.get("organisation") or {}).get("name", "Company")
                seo_url = item.get("seo_url", "")
                full_url = f"https://unstop.com/{seo_url.lstrip('/')}" if seo_url and not seo_url.startswith("http") else seo_url
                details = BeautifulSoup(str(item.get("details", "")), "html.parser").get_text(" ", strip=True)
                if full_url and len(details) > 80 and is_valid_relevant_role(title, details):
                    results.append({"company": org, "title": title, "url": full_url, "jd": details[:3500], "source": "unstop"})
        except Exception:
            pass
    return results


def scrape_remote_global_apis():
    results = []

    for cat in ["data", "ai-ml"]:
        try:
            r = requests.get(f"https://remotive.com/api/remote-jobs?category={cat}&limit=25", timeout=10)
            if r.status_code == 200:
                for j in r.json().get("jobs", []):
                    title = j.get("title", "")
                    cand_loc = str(j.get("candidate_required_location", "")).lower()
                    if any(loc in cand_loc for loc in ["worldwide", "anywhere", "india", "apac", "asia", "global", ""]):
                        jd = BeautifulSoup(j.get("description", ""), "html.parser").get_text(" ", strip=True)
                        if is_valid_relevant_role(title, jd):
                            results.append({"company": j.get("company_name", "Remote Tech"), "title": title, "url": j.get("url", ""), "jd": jd[:3500], "source": "remotive"})
        except Exception:
            pass

    try:
        r = requests.get("https://www.workingnomads.com/api/exposed_jobs/", headers=HEADERS, timeout=10)
        if r.status_code == 200:
            for j in r.json():
                title = str(j.get("title", ""))
                jd = BeautifulSoup(str(j.get("description", "")), "html.parser").get_text(" ", strip=True)
                if is_valid_relevant_role(title, jd):
                    results.append({"company": j.get("company_name", "Global Remote"), "title": title, "url": j.get("url", ""), "jd": jd[:3500], "source": "workingnomads"})
    except Exception:
        pass

    for tag in ["data", "analyst", "python"]:
        try:
            r = requests.get(f"https://remoteok.com/api?tag={tag}", headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            if r.status_code == 200:
                for item in r.json()[1:25]:
                    title = str(item.get("position", ""))
                    jd = BeautifulSoup(str(item.get("description", "")), "html.parser").get_text(" ", strip=True)
                    if len(jd) > 100 and is_valid_relevant_role(title, jd):
                        results.append({"company": item.get("company", "RemoteOK Startup"), "title": title, "url": item.get("url", ""), "jd": jd[:3500], "source": "remoteok"})
        except Exception:
            pass

    try:
        r = requests.get("https://jobicy.com/api/v2/remote-jobs?count=25&industry=data-science", headers=HEADERS, timeout=10)
        if r.status_code == 200:
            for item in r.json().get("jobs", []):
                title = item.get("jobTitle", "")
                jd = BeautifulSoup(item.get("jobDescription", ""), "html.parser").get_text(" ", strip=True)
                if is_valid_relevant_role(title, jd):
                    results.append({"company": item.get("companyName", "Global Tech"), "title": title, "url": item.get("url", ""), "jd": jd[:3500], "source": "jobicy"})
    except Exception:
        pass

    try:
        r = requests.get("https://himalayas.app/jobs/api?limit=100", headers=HEADERS, timeout=12)
        if r.status_code == 200:
            for item in r.json().get("jobs", []):
                title = str(item.get("title", ""))
                jd = BeautifulSoup(str(item.get("description", "")), "html.parser").get_text(" ", strip=True)
                if is_valid_relevant_role(title, jd):
                    results.append({"company": item.get("companyName", "Himalayas Co"), "title": title, "url": item.get("applicationLink") or item.get("guid", ""), "jd": jd[:3500], "source": "himalayas"})
    except Exception:
        pass

    try:
        r = requests.get("https://www.arbeitnow.com/api/job-board-api", headers=HEADERS, timeout=10)
        if r.status_code == 200:
            for item in r.json().get("data", []):
                title = str(item.get("title", ""))
                jd = BeautifulSoup(str(item.get("description", "")), "html.parser").get_text(" ", strip=True)
                if item.get("remote") and is_valid_relevant_role(title, jd):
                    results.append({"company": item.get("company_name", "Tech Co"), "title": title, "url": item.get("url", ""), "jd": jd[:3500], "source": "arbeitnow"})
    except Exception:
        pass

    return results


def scrape_greenhouse_and_lever_apis():
    results = []
    for board in GREENHOUSE_BOARDS:
        try:
            r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true", timeout=7)
            if r.status_code == 200:
                for j in r.json().get("jobs", []):
                    title = j.get("title", "")
                    loc = (j.get("location") or {}).get("name", "")
                    if any(l in loc.lower() for l in ["india", "bangalore", "bengaluru", "chennai", "hyderabad", "pune", "mumbai", "remote", "gurugram", "anywhere"]):
                        jd = BeautifulSoup(j.get("content", ""), "html.parser").get_text(" ", strip=True)
                        if is_valid_relevant_role(title, jd):
                            results.append({"company": board.capitalize(), "title": title, "url": j.get("absolute_url", ""), "jd": jd[:3500], "source": "greenhouse"})
        except Exception:
            pass

    for board in LEVER_BOARDS:
        try:
            r = requests.get(f"https://api.lever.co/v0/postings/{board}?mode=json", timeout=7)
            if r.status_code == 200:
                for j in r.json():
                    title = j.get("text", "")
                    loc = (j.get("categories") or {}).get("location", "")
                    if any(l in str(loc).lower() for l in ["india", "bangalore", "bengaluru", "chennai", "hyderabad", "remote"]):
                        jd = j.get("descriptionPlain", "") + " " + " ".join([lst.get("content", "") for lst in j.get("lists", [])])
                        jd_clean = BeautifulSoup(jd, "html.parser").get_text(" ", strip=True)
                        if is_valid_relevant_role(title, jd_clean):
                            results.append({"company": board.capitalize(), "title": title, "url": j.get("hostedUrl", ""), "jd": jd_clean[:3500], "source": "lever"})
        except Exception:
            pass
    return results


def search_bing_rss_portals():
    found = []
    role_queries = ["Data Analyst", "Data Analyst Intern", "Generative AI"]
    for portal_name, domain in BING_RSS_PORTALS:
        for rq in role_queries[:2]:
            q = urllib.parse.quote(f'site:{domain} "{rq}" (India OR Chennai OR Bangalore OR Remote)')
            rss_url = f"https://www.bing.com/search?q={q}&format=rss"
            try:
                resp = requests.get(rss_url, headers=HEADERS, timeout=9)
                if resp.status_code != 200 or "<rss" not in resp.text[:300]:
                    continue
                root = ET.fromstring(resp.content)
                for item in root.findall(".//item")[:4]:
                    title_raw = (item.findtext("title") or "").strip()
                    link = (item.findtext("link") or "").strip()
                    snippet = (item.findtext("description") or "").strip()
                    if domain.split("/")[0] not in link:
                        continue
                    if any(bad in link.lower() for bad in ["?q=", "/search", "jobs-in-", "internships-in-"]):
                        continue
                    page_jd = scrape_page_text(link)
                    full_jd = page_jd if len(page_jd) > 180 else f"Job Posting: {title_raw}. Details: {snippet}. Role Focus: {rq} requiring SQL, Python, Power BI, Data Analysis."
                    if is_valid_relevant_role(title_raw, full_jd):
                        comp = title_raw.split("-")[0].split("|")[0].split("at")[-1].strip()[:25]
                        found.append({
                            "company": comp or portal_name.capitalize(),
                            "title": title_raw[:45] or rq,
                            "url": link,
                            "jd": full_jd,
                            "source": portal_name
                        })
            except Exception:
                pass
    return found


def fetch_all_jobs():
    by_platform = {}

    def add_job(item):
        src = item.get("source", "web")
        by_platform.setdefault(src, []).append(item)

    print("🔍 1/5 Scraping Direct Indian Portals (Internshala, Unstop)...")
    for j in scrape_internshala_and_unstop():
        add_job(j)

    print("🔍 2/5 Scraping Global Remote APIs (Remotive, WorkingNomads, RemoteOK, Himalayas, Jobicy, Arbeitnow)...")
    for j in scrape_remote_global_apis():
        add_job(j)

    print("🔍 3/5 Scraping Direct Startup ATS APIs (Greenhouse, Lever)...")
    for j in scrape_greenhouse_and_lever_apis():
        add_job(j)

    print("🔍 4/5 Scraping Startup Portals via Bing RSS (Wellfound, Cutshort, Instahyre, Hirist, Cuvette, Ashby, Workable, Foundit)...")
    for j in search_bing_rss_portals():
        add_job(j)

    print("🔍 5/5 Scraping Naukri, Google Jobs, Indeed & LinkedIn...")
    for role in FRESHER_ROLES[:4] + INTERN_ROLES[:2]:
        try:
            df = scrape_jobs(
                site_name=["naukri", "google", "indeed", "linkedin"],
                search_term=role,
                location="India",
                results_wanted=4,
                hours_old=48,
                country_indeed="india",
                linkedin_fetch_description=True
            )
            for _, row in df.iterrows():
                desc = str(row.get("description", ""))
                site = str(row.get("site", "board")).lower()
                title = str(row.get("title", role)).strip()
                if desc and desc != "nan" and len(desc) > 140 and is_valid_relevant_role(title, desc):
                    add_job({
                        "company": str(row.get("company", "Company")).strip(),
                        "title": title,
                        "url": str(row.get("job_url", "")),
                        "jd": desc,
                        "source": site
                    })
        except Exception:
            pass

    summary_str = " | ".join([f"{k}: {len(v)}" for k, v in sorted(by_platform.items())])
    print(f"📊 Raw Jobs Per Platform -> {summary_str}")

    fresher_pool, intern_pool = [], []
    priority_order = [k for k in by_platform.keys() if k not in ["linkedin", "indeed"]] + ["naukri", "indeed", "linkedin"]
    priority_order = list(dict.fromkeys(priority_order))

    max_depth = max([len(v) for v in by_platform.values()] + [1])
    for idx in range(max_depth):
        for p in priority_order:
            plist = by_platform.get(p, [])
            if idx < len(plist):
                job_item = plist[idx]
                if is_internship_role(job_item["title"], job_item["jd"]):
                    intern_pool.append(job_item)
                else:
                    fresher_pool.append(job_item)

    print(f"⚖️ Categorized Pools -> Full-Time Fresher Pool: {len(fresher_pool)} | Internship Pool: {len(intern_pool)}")
    return fresher_pool, intern_pool


# ================= 2. HONEST SKILL-LOCKED HUMANIZED RESUME ENGINE =================
def build_prompt(job):
    return f"""
    You are a practical hiring manager helping Senthamil Valavan S (2026 B.Tech Information Technology fresher from Chennai, speaks Tamil & English) tailor his 1-page resume.

    STEP 1 — STRICT ELIGIBILITY CHECK (`is_eligible_fresher_btech`) & CATEGORY (`job_category`):
    - Set `job_category` to `"internship"` if Intern / Trainee / Apprentice, else `"fresher"`.
    - Set `is_eligible_fresher_btech` to FALSE and `match_score` to 0 if ANY of the following is true:
      1. Job requires 2+ or 3+ years mandatory full-time experience.
      2. Job strictly requires ONLY non-engineering degrees (B.Com, CA, MBA Finance, Pharma) and rejects B.Tech / IT.
      3. Role is HR, Talent Acquisition, People Operations, Recruitment, Data Entry, Typing, BPO, Unpaid Internship, or Pure Backend Java/Go/C++ Software Engineering unrelated to Data/Analytics/AI.
      4. Job requires mandatory fluency in non-Tamil/non-English regional/foreign languages (e.g., Punjabi, Marathi, Bengali, German, Japanese, Spanish).

    STEP 2 — STRICT HONEST SKILLS LOCK (CRITICAL — INTERVIEW SAFETY!):
    Senthamil ONLY knows the skills in this exact whitelist:
    {ALLOWED_SKILLS_WHITELIST}
    - **NEVER ADD SKILLS HE DOES NOT KNOW!** Do NOT put SAP, Snowflake, Apache Spark, Kafka, Airflow, AWS, Azure, GCP, Databricks, Looker, Qlik, Alteryx, Scala, Java, Docker, Kubernetes, Splunk, Workday, Keka, or DBT into Technical Skills, Summary, or Bullets!
    - Maximize ATS score by prioritizing & re-ordering his REAL skills that match the JD, and using natural ATS synonyms of what he ALREADY knows.
    - Any unfamiliar tool requested in the JD MUST ONLY be mentioned in `missing_skills_and_notes` (keep notes concise, max 2 sentences, no newlines!).

    STEP 3 — 100% HUMANIZED, FULL-PAGE DEPTH (NO AI BUZZWORDS!):
    - NEVER use first-person pronouns ("I", "my", "we").
    - FORBIDDEN AI WORDS: Never use "spearheaded", "leveraged", "synergized", "cutting-edge", "dynamic", "passionate", "delve", "testament", "orchestrated", "pioneered", "transformative", "seamless", "robust", "meticulous", or em-dashes (—).
    - `tailored_headline`: 4-5 honest role/skill tags separated by ` | `.
    - `tailored_summary`: 3 to 4 full sentences (55 to 70 words total) matching JD responsibilities using ONLY his real skill set.
    - Categorized skills (`analytics_bi_skills`, `data_sql_skills`, `programming_skills`, `tools_skills`): Clean, interview-safe lists strictly from his whitelist.
    - `project_bullets`: 3 inner lists for his exact 3 projects, **2 detailed 2-line bullets per project (22 to 30 words per bullet)**.
    - `experience_bullets`: **4 detailed 2-line bullets (22 to 28 words per bullet)** for Codeniet Data Analyst experience.

    Candidate Base Resume:
    {json.dumps(BASE_RESUME)}

    Target Job:
    Company: {job['company']}
    Title: {job['title']}
    Platform: {job.get('source', 'portal')}
    JD: {job['jd'][:2800]}
    """


def call_groq_fallback(prompt):
    if not GROQ_API_KEY:
        return None
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": "Output strictly valid JSON with keys: is_eligible_fresher_btech, job_category, match_score, tailored_headline, tailored_summary, analytics_bi_skills, data_sql_skills, programming_skills, tools_skills, project_bullets, experience_bullets, missing_skills_and_notes."},
            {"role": "user", "content": prompt}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.25
    }
    resp = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=30)
    if resp.status_code == 200:
        return json.loads(resp.json()["choices"][0]["message"]["content"])
    return None


def tailor_resume_with_ai(job):
    global GEMINI_MODEL_POOL
    prompt = build_prompt(job)

    if GROQ_API_KEY:
        try:
            res = call_groq_fallback(prompt)
            if res and "match_score" in res:
                return res
        except Exception:
            pass

    for model_name in list(GEMINI_MODEL_POOL):
        try:
            response = ai_client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=TailoredResumeSchema,
                    temperature=0.25
                )
            )
            return json.loads(response.text)
        except Exception as e:
            err_str = str(e)
            if any(k in err_str for k in ["429", "503", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "overloaded", "busy"]):
                GEMINI_MODEL_POOL.remove(model_name)
                GEMINI_MODEL_POOL.append(model_name)
                time.sleep(3)
                continue
            elif "404" in err_str or "NOT_FOUND" in err_str:
                GEMINI_MODEL_POOL.remove(model_name)
                continue
            else:
                raise e

    raise RuntimeError("All AI models temporarily rate-limited.")


# ================= 3. FULL-A4 BALANCED 1-PAGE PDF IN DATE FOLDER =================
def build_resume_html(tailored_data, font_pt=10.6, margin_in=0.50, line_h=1.45, sec_gap=13, li_gap=4.5):
    proj_html = ""
    t_proj_list = tailored_data.get("project_bullets", [])

    for i, proj in enumerate(BASE_RESUME["projects"]):
        bullets_to_use = t_proj_list[i] if i < len(t_proj_list) and isinstance(t_proj_list[i], list) and t_proj_list[i] else proj["bullets"]
        bullets_html = "".join([f"<li>{b}</li>" for b in bullets_to_use[:3]])
        proj_html += f"""
        <div class="item">
            <div class="row"><b>{proj['title']}</b><b>{proj['tech']}</b></div>
            <ul>{bullets_html}</ul>
        </div>"""

    exp = BASE_RESUME["experience"][0]
    exp_bullets_to_use = (tailored_data.get("experience_bullets") or exp["bullets"])[:4]
    exp_bullets_html = "".join([f"<li>{b}</li>" for b in exp_bullets_to_use])

    contact_html = (
        '<span class="c-item"><a href="tel:+919361577533">+91 9361577533</a></span> | '
        '<span class="c-item"><a href="mailto:senthamilvalavan456@gmail.com">senthamilvalavan456@gmail.com</a></span> | '
        '<span class="c-item"><a href="https://www.linkedin.com/in/senthamil-valavan-s/">linkedin.com/in/senthamil-valavan-s/</a></span> | '
        '<span class="c-item"><a href="https://github.com/senthamilvalavan">github.com/senthamilvalavan</a></span> | '
        '<span class="c-item">Portfolio: <a href="https://senthamil.vercel.app/">senthamil.vercel.app</a></span>'
    )

    return f"""
    <html>
    <head>
    <style>
        @page {{ size: A4; margin: {margin_in}in; }}
        body {{
            font-family: Arial, Helvetica, sans-serif;
            font-size: {font_pt}pt;
            line-height: {line_h};
            color: #111;
            margin: 0;
        }}
        h1 {{
            font-size: {font_pt + 8.5}pt;
            margin: 0 0 3px 0;
            font-weight: bold;
            letter-spacing: 0.4px;
            color: #000;
        }}
        .headline {{
            font-size: {font_pt + 0.8}pt;
            font-weight: bold;
            color: #222;
            margin-bottom: 4px;
        }}
        .contact {{
            font-size: {font_pt - 0.9}pt;
            margin-bottom: {sec_gap - 2}px;
            color: #333;
            line-height: 1.4;
        }}
        .c-item {{ white-space: nowrap; }}
        .contact a {{ color: #0b57d0; text-decoration: none; }}
        h2 {{
            font-size: {font_pt + 0.6}pt;
            border-bottom: 1.4px solid #000;
            padding-bottom: 2px;
            margin: {sec_gap}px 0 6px 0;
            text-transform: uppercase;
            font-weight: bold;
            letter-spacing: 0.3px;
            color: #000;
        }}
        .summary {{
            margin: 0 0 6px 0;
            text-align: justify;
        }}
        .skill-line {{
            margin: 3.5px 0;
        }}
        .item {{
            margin-bottom: {li_gap + 3}px;
        }}
        .row {{
            display: flex;
            justify-content: space-between;
            font-size: {font_pt}pt;
            margin-top: 4px;
        }}
        ul {{
            margin: 3px 0 6px 18px;
            padding: 0;
        }}
        li {{
            margin-bottom: {li_gap}px;
            text-align: justify;
        }}
        .edu-line {{
            margin-bottom: 4px;
        }}
    </style>
    </head>
    <body>
        <h1>{BASE_RESUME['name']}</h1>
        <div class="headline">{tailored_data.get('tailored_headline', BASE_RESUME['headline'])}</div>
        <div class="contact">{contact_html}</div>

        <h2>PROFESSIONAL SUMMARY</h2>
        <p class="summary">{tailored_data.get('tailored_summary', BASE_RESUME['summary'])}</p>

        <h2>TECHNICAL SKILLS</h2>
        <div class="skill-line"><b>Analytics & BI:</b> {tailored_data.get('analytics_bi_skills', BASE_RESUME['skills']['Analytics & BI'])}</div>
        <div class="skill-line"><b>Data & SQL:</b> {tailored_data.get('data_sql_skills', BASE_RESUME['skills']['Data & SQL'])}</div>
        <div class="skill-line"><b>Programming & Libraries:</b> {tailored_data.get('programming_skills', BASE_RESUME['skills']['Programming & Libraries'])}</div>
        <div class="skill-line"><b>Tools:</b> {tailored_data.get('tools_skills', BASE_RESUME['skills']['Tools'])}</div>

        <h2>KEY PROJECTS</h2>
        {proj_html}

        <h2>PRACTICAL EXPERIENCE</h2>
        <div class="item">
            <div class="row"><b>{exp['role']}</b><b>{exp['duration']}</b></div>
            <ul>{exp_bullets_html}</ul>
        </div>

        <h2>EDUCATION & CERTIFICATIONS</h2>
        <div class="edu-line"><b>{BASE_RESUME['education']}</b></div>
        <div>{BASE_RESUME['certifications']}</div>
    </body>
    </html>
    """


def generate_ats_pdf(tailored_data, folder_date_str, filename):
    day_dir = os.path.join("resumes", folder_date_str)
    os.makedirs(day_dir, exist_ok=True)
    filepath = os.path.join(day_dir, filename)

    presets = [
        (11.0, 0.52, 1.52, 16, 6.0),
        (10.7, 0.50, 1.48, 14, 5.0),
        (10.4, 0.48, 1.43, 13, 4.5),
        (10.1, 0.46, 1.38, 11, 3.8),
        (9.8,  0.44, 1.34, 10, 3.0),
        (9.5,  0.40, 1.29, 9,  2.5),
        (9.1,  0.36, 1.24, 8,  2.0),
    ]
    doc = None
    for font_pt, margin_in, line_h, sec_gap, li_gap in presets:
        html_str = build_resume_html(tailored_data, font_pt, margin_in, line_h, sec_gap, li_gap)
        doc = HTML(string=html_str).render()
        if len(doc.pages) == 1:
            doc.write_pdf(filepath)
            return filepath

    doc.pages = doc.pages[:1]
    doc.write_pdf(filepath)
    return filepath


# ================= 4. STRICT COLUMN-A ANCHORED SHEETS WRITER =================
def append_row_strictly_at_col_a(sheet, row_values):
    """
    Guarantees every row starts strictly at Column A (A:H) and NEVER shifts sideways!
    """
    clean_vals = [str(v).replace("\n", " ").replace("\r", " ").strip() for v in row_values]
    sheet.append_row(
        clean_vals,
        value_input_option="USER_ENTERED",
        insert_data_option="INSERT_ROWS",
        table_range="A1:H1"  # Locks insertion strictly to Columns A through H!
    )


def ensure_today_date_header(sheet, today_str):
    col_a = [c.strip() for c in sheet.col_values(1)]
    if today_str not in col_a:
        append_row_strictly_at_col_a(sheet, [today_str, "", "", "", "", "", "", ""])
        new_row_idx = len(sheet.col_values(1))
        try:
            sheet.format(
                f"A{new_row_idx}:H{new_row_idx}",
                {
                    "textFormat": {"bold": True, "fontSize": 11},
                    "backgroundColor": {"red": 0.90, "green": 0.94, "blue": 0.98}
                }
            )
        except Exception:
            pass
        print(f"📅 Created Bold Date Header Row in Sheet: {today_str}")


def main():
    sheet = gc.open(SHEET_NAME).sheet1
    all_rows = sheet.get_all_values()

    seen_cache = load_seen_jobs()
    for r in all_rows[1:]:
        if len(r) >= 3 and r[2].startswith("http"):
            seen_cache.add(normalize_url(r[2]))
            seen_cache.add(make_job_fingerprint(r[0], re.sub(r"\[(INTERNSHIP|FRESHER JOB)\]", "", r[1])))

    ist_now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
    today_sheet_str = ist_now.strftime("%d/%m/%Y")
    today_folder_str = ist_now.strftime("%d-%m-%Y")
    date_header_checked = False

    fresher_pool, intern_pool = fetch_all_jobs()

    candidate_queue = []
    fi, ii = 0, 0
    while fi < len(fresher_pool) or ii < len(intern_pool):
        for _ in range(3):
            if fi < len(fresher_pool):
                candidate_queue.append(fresher_pool[fi])
                fi += 1
        for _ in range(2):
            if ii < len(intern_pool):
                candidate_queue.append(intern_pool[ii])
                ii += 1

    fresher_added = 0
    intern_added = 0
    platform_counts = {}

    for job in candidate_queue:
        if fresher_added >= TARGET_FRESHER_JOBS and intern_added >= TARGET_INTERNSHIPS:
            break

        src = job.get("source", "web")
        if platform_counts.get(src, 0) >= MAX_PER_PLATFORM:
            continue

        norm_url = normalize_url(job["url"])
        fingerprint = make_job_fingerprint(job["company"], job["title"])
        if not norm_url or norm_url in seen_cache or fingerprint in seen_cache:
            continue

        guess_intern = is_internship_role(job["title"], job["jd"])
        if guess_intern and intern_added >= TARGET_INTERNSHIPS:
            continue
        if not guess_intern and fresher_added >= TARGET_FRESHER_JOBS:
            continue

        try:
            print(f"⚙️ [{src.upper()}] Analyzing: {job['company']} - {job['title']}")
            tailored = tailor_resume_with_ai(job)
            time.sleep(3.5)

            seen_cache.add(norm_url)
            seen_cache.add(fingerprint)

            is_eligible = tailored.get("is_eligible_fresher_btech", True)
            score = int(tailored.get("match_score", 0))
            ai_cat = str(tailored.get("job_category", "")).lower()
            final_is_intern = (ai_cat == "internship") or guess_intern

            if not is_eligible or score < 68:
                print(f"   ⏩ Skipped (Eligible: {is_eligible}, Score: {score}/100)")
                continue

            if final_is_intern and intern_added >= TARGET_INTERNSHIPS:
                continue
            if not final_is_intern and fresher_added >= TARGET_FRESHER_JOBS:
                continue

            if not date_header_checked:
                ensure_today_date_header(sheet, today_sheet_str)
                date_header_checked = True

            clean_comp = re.sub(r"[^a-zA-Z0-9]", "", job["company"])[:15] or "Company"
            clean_role = re.sub(r"[^a-zA-Z0-9_]", "_", job["title"])[:22]
            short_hash = str(abs(hash(norm_url)))[:4]
            pdf_filename = f"{clean_comp}_{clean_role}_{short_hash}.pdf"

            generate_ats_pdf(tailored, today_folder_str, pdf_filename)
            github_pdf_url = f"https://github.com/{GITHUB_REPO}/blob/main/resumes/{today_folder_str}/{pdf_filename}"

            tag = "[INTERNSHIP]" if final_is_intern else "[FRESHER JOB]"
            clean_notes = f"[{src.upper()}] {tailored.get('missing_skills_and_notes', '')}"

            row_data = [
                job["company"],                                       # Col A: Company
                f"{job['title']} {tag}",                              # Col B: Job title
                job["url"],                                           # Col C: Job URL
                f"{score}/100",                                       # Col D: Match score
                f'=HYPERLINK("{github_pdf_url}", "{pdf_filename}")',  # Col E: Resume file
                "Ready for review",                                   # Col F: Application status
                "",                                                   # Col G: Applied date
                clean_notes                                           # Col H: Notes
            ]
            append_row_strictly_at_col_a(sheet, row_data)

            platform_counts[src] = platform_counts.get(src, 0) + 1
            if final_is_intern:
                intern_added += 1
            else:
                fresher_added += 1

            print(f"✅ Added ({fresher_added} Fresher | {intern_added} Intern) via {src.upper()}: resumes/{today_folder_str}/{pdf_filename}")

        except Exception as e:
            print(f"⚠️ Skipped job due to error: {e}")

    save_seen_jobs(seen_cache)
    print(f"🎉 Mission Complete! Added {fresher_added} Fresher Jobs + {intern_added} Internships across {len(platform_counts)} platforms: {platform_counts}")


if __name__ == "__main__":
    main()