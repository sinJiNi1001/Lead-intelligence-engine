import os
import json
import re
import time
import traceback
import sqlite3
import asyncio
from datetime import datetime
from typing import List, Any, Dict
from dotenv import load_dotenv
from database import get_db_connection, init_db

import requests
from bs4 import BeautifulSoup
from ddgs import DDGS # Updated import
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import logging

# ==========================================
# --- SETUP & LOGGING ---
# ==========================================
load_dotenv()

# 1. Backend Logger
logging.basicConfig(
    filename='DEBUG_LOG.txt', 
    level=logging.INFO, 
    format='%(asctime)s - BACKEND - %(levelname)s - %(message)s'
)

# 2. Frontend Logger
frontend_logger = logging.getLogger('frontend_logger')
frontend_handler = logging.FileHandler('FRONTEND_DEBUG_LOG.txt', encoding='utf-8')
frontend_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
frontend_logger.addHandler(frontend_handler)
frontend_logger.setLevel(logging.INFO)

app = FastAPI(title="Lead Intelligence Engine API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DEV_MODE = False

# ==========================================
# --- PYDANTIC MODELS ---
# ==========================================
class PricingRules(BaseModel):
    web: int = 1500
    api: int = 1000
    net: int = 800
    min: int = 2500
    ent: float = 1.5

class LeadRequest(BaseModel):
    company_name: str
    website: str
    model: str = "meta-llama/llama-3.1-8b-instruct"
    linkedin_url: str = "N/A"
    country: str = "N/A"
    industry: str = "N/A"
    service_type: str = "Web VAPT"
    scope_count: int = 1
    complexity: str = "Medium"
    lead_type: str = "New"
    clarity: str = "N/A"
    buying_stage: str = "N/A"
    price_sensitivity: str = "N/A"
    timeline: str = "N/A"
    competitors: bool = False
    pricing_rules: PricingRules = PricingRules()

class BatchLeadRequest(BaseModel):
    leads: List[LeadRequest]

class FrontendLog(BaseModel):
    level: str
    message: str

# ==========================================
# --- FILE LOADING (PROMPTS) ---
# ==========================================
def load_text_file(filename: str) -> str:
    try:
        filepath = os.path.join(os.path.dirname(__file__), filename)
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        logging.error(f"Failed to load text file {filename}: {e}")
        return ""

# ==========================================
# --- DATABASE & HELPER FUNCTIONS ---
# ==========================================
init_db()

def check_previous_enquiry(company_name, website):
    try:
        core_domain = website.replace("https://", "").replace("http://", "").replace("www.", "").strip('/')
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT timestamp, decision, suggested_price 
                FROM leads 
                WHERE company_name LIKE ? OR website LIKE ? 
                ORDER BY timestamp DESC LIMIT 1
            """, (f"%{company_name}%", f"%{core_domain}%"))
            result = cursor.fetchone()
            
        if result:
            logging.info(f"🔄 [DATABASE] Repeat Lead Found: {company_name}")
            return dict(result)
        else:
            logging.info(f"✨ [DATABASE] New Lead: {company_name}")
            return None
    except Exception as e:
        logging.error(f"❌ [DATABASE] Error checking history: {e}")
        return None

def get_time_ago(dt_obj):
    if not dt_obj: return "in the past"
    if isinstance(dt_obj, str):
        try:
            dt_obj = datetime.strptime(dt_obj.split('.')[0], '%Y-%m-%d %H:%M:%S')
        except:
            return "in the past"
            
    diff = datetime.now() - dt_obj
    days = diff.days
    
    if days == 0: return "today"
    elif days == 1: return "yesterday"
    elif days < 30: return f"{days} days ago"
    elif days < 365:
        m = days // 30
        return f"{m} month{'s' if m > 1 else ''} ago"
    else:
        y = days // 365
        return f"{y} year{'s' if y > 1 else ''} ago"

def save_lead(sales_person, company_name, website, service_type, input_dict, output_dict, decision, suggested_price):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO leads (sales_person, company_name, website, service_type, full_input_json, full_output_json, decision, suggested_price, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (sales_person, company_name, website, service_type, json.dumps(input_dict), json.dumps(output_dict), decision, str(suggested_price)))
            conn.commit()
    except Exception as e:
        logging.error(f"DB Error: {e}")

# ==========================================
# --- SCRAPING ENGINE ---
# ==========================================
def scrape_company_website(url):
    if not url or url == "N/A": return ""
    logging.info(f"[SCRAPER] Visiting company website -> {url}")
    if not url.startswith("http"): url = "https://" + url
            
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(url, headers=headers, timeout=7)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            for script in soup(["script", "style"]): script.extract()
            clean_text = soup.get_text(separator=' ', strip=True)[:1500] 
            
            if len(clean_text) > 100:
                if "Just a moment" in clean_text or "Enable JavaScript" in clean_text or "cloudflare" in clean_text.lower():
                    raise Exception("Cloudflare CAPTCHA block detected.")
                return f"COMPANY WEBSITE:\n{clean_text}\n\n"
    except Exception as e:
        logging.warning(f"BS4 Failed: {e}. Triggering Fallback...")

    try:
        firecrawl_key = os.getenv("FIRECRAWL_API_KEY")
        if firecrawl_key:
            headers = {"Authorization": f"Bearer {firecrawl_key}", "Content-Type": "application/json"}
            payload = {"url": url, "formats": ["markdown"]}
            fc_response = requests.post("https://api.firecrawl.dev/v1/scrape", headers=headers, json=payload, timeout=15)
            if fc_response.status_code == 200:
                clean_text = fc_response.json().get("data", {}).get("markdown", "")[:1500]
                return f"COMPANY WEBSITE (FIRECRAWL EXTRACT):\n{clean_text}\n\n"
    except Exception as e:
        logging.error(f"Fallback Scraper Exception: {e}")

    return "COMPANY WEBSITE: Could not access directly. Rely strictly on Search Engine Intel.\n\n"

def gather_background_intelligence(company_name, country_name="", website_url=""):
    if not company_name: return "No reliable recent news found."
    location = f" {country_name}" if country_name and country_name != "Other" else ""
    core_domain = website_url.replace("https://", "").replace("http://", "").replace("www.", "").split('/')[0]
    
    queries = [
        f'"{company_name}" "{core_domain}" funding OR investment OR valuation',
        f'"{company_name}" "{core_domain}" breach OR lawsuit OR penalty',
        f'"{company_name}" {location} founded incorporated -"{core_domain}"', 
        f'"{company_name}" "{core_domain}" employees headcount',
        f'who is the CEO or founder of "{company_name}" "{core_domain}"',
        f'site:linkedin.com/company "{company_name}" {location}'
    ]
    
    raw_results = []
    ddgs = DDGS()
    for q in queries:
        try:
            res = ddgs.text(q, max_results=8) 
            raw_results.extend(list(res) if res else [])
        except Exception:
            continue 
        time.sleep(2)

    if not raw_results:
        serper_key = os.getenv("SERPER_API_KEY")
        if serper_key:
            headers = {'X-API-KEY': serper_key, 'Content-Type': 'application/json'}
            for q in [f'"{company_name}" "{core_domain}" business OR funding OR lawsuit']:
                try:
                    resp = requests.post("https://google.serper.dev/search", headers=headers, json={"q": q, "num": 8}, timeout=10)
                    if resp.status_code == 200:
                        for item in resp.json().get("organic", []):
                            raw_results.append({"title": item.get("title", ""), "href": item.get("link", ""), "body": item.get("snippet", "")})
                except Exception:
                    pass

    clean_results = []
    seen_urls = set()
    spam_domains = ['tiktok.com', 'reddit.com', 'facebook.com', 'instagram.com', 'twitter.com']
    dead_words = ['latest news and live updates', 'read latest news']
    name_parts = company_name.lower().split()
    core_name = " ".join(name_parts[:2]) if len(name_parts) > 1 else name_parts[0]
    
    for r in raw_results:
        title, url, snippet = r.get('title', ''), r.get('href', ''), r.get('body', '')
        if url in seen_urls or any(domain in url.lower() for domain in spam_domains): continue
        if any(dead in title.lower() for dead in dead_words): continue
        if core_name not in title.lower() and core_name not in snippet.lower(): continue
        
        seen_urls.add(url)
        clean_results.append({"title": title, "snippet": snippet[:300]})
        if len(clean_results) >= 35: break

    ddg_text = "SEARCH ENGINE INTEL:\n" + "\n".join([f"- {r['title']}: {r['snippet']}" for r in clean_results]) if clean_results else "No recent news found."
    return scrape_company_website(website_url) + ddg_text

# ==========================================
# --- AI & JSON SANITIZATION ---
# ==========================================
def sanitize_json(raw_str: str) -> dict:
    """
    Robust JSON parser that walks the string character-by-character to 
    properly escape control characters (like \n, \r, \t) only when they
    appear inside string values, preventing json.loads crashes.
    """
    # 1. Isolate the JSON block (handles both objects and arrays)
    start_obj = raw_str.find('{')
    end_obj = raw_str.rfind('}')
    start_arr = raw_str.find('[')
    end_arr = raw_str.rfind(']')

    # Determine if it's primarily an object or array
    if start_obj != -1 and end_obj != -1 and (start_arr == -1 or start_obj < start_arr):
        start, end = start_obj, end_obj
    elif start_arr != -1 and end_arr != -1:
        start, end = start_arr, end_arr
    else:
        raise ValueError("No JSON object or array found in AI response")

    json_str = raw_str[start:end+1]
    
    # 2. Walk the string to escape raw control characters inside strings
    sanitized = []
    in_string = False
    escape_next = False
    
    for char in json_str:
        if in_string:
            if escape_next:
                sanitized.append(char)
                escape_next = False
            elif char == '\\':
                sanitized.append(char)
                escape_next = True
            elif char == '"':
                sanitized.append(char)
                in_string = False
            elif char == '\n':
                sanitized.append('\\n')
            elif char == '\r':
                sanitized.append('\\r')
            elif char == '\t':
                sanitized.append('\\t')
            elif ord(char) < 32:
                # Escape any other raw unprintable control characters
                sanitized.append(f"\\u{ord(char):04x}")
            else:
                sanitized.append(char)
        else:
            if char == '"':
                in_string = True
            sanitized.append(char)
            
    clean_str = "".join(sanitized)
    
    # 3. Handle common trailing comma errors as a final safety net
    clean_str = re.sub(r',\s*}', '}', clean_str)
    clean_str = re.sub(r',\s*]', ']', clean_str)
    
    return json.loads(clean_str)

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=10), reraise=True)
def call_openrouter(model_id, prompt_content):
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.getenv("OPENROUTER_API_KEY"))
    return client.chat.completions.create(
        model=model_id, messages=[{"role": "user", "content": prompt_content}],
        temperature=0.0, seed=42
    )

def analyze_lead(input_dict, background_text, model_id) -> Dict[str, Any]:
    if not os.getenv("OPENROUTER_API_KEY"): return {"error": "OPENROUTER_API_KEY missing from .env"}
    template = load_text_file("system_prompt.txt")
    if not template: return {"error": "Missing system_prompt.txt file"}
    
    try:
        pr = input_dict.get("pricing_rules", {})
        prompt = template.format(
            company_name=input_dict.get("company_name", ""),
            website=input_dict.get("website", ""),
            linkedin=input_dict.get("linkedin_url", "N/A"),
            country=input_dict.get("country", "N/A"),
            company_domain=input_dict.get("industry", "N/A"),
            service=input_dict.get("service_type", ""),
            scope=input_dict.get("scope_count", 1),
            complexity=input_dict.get("complexity", ""),
            lead_type=input_dict.get("lead_type", ""),
            clarity=input_dict.get("clarity", ""),
            stage=input_dict.get("buying_stage", ""),
            sensitivity=input_dict.get("price_sensitivity", ""),
            timeline=input_dict.get("timeline", ""),
            competitors=input_dict.get("competitors", False),
            pr_web=pr.get("web", 1500),
            pr_api=pr.get("api", 1000),
            pr_net=pr.get("net", 800),
            pr_min=pr.get("min", 2500),
            pr_ent=pr.get("ent", 1.5),
            background_text=background_text,
        )

        response = call_openrouter(model_id, prompt)
        raw = response.choices[0].message.content

        if not raw:
            return {"error": "AI returned an empty response."}
            
        # Use our new robust sanitizer
        return sanitize_json(raw)
        
    except Exception as e:
        return {"error": str(e)}

def analyze_lead_batch(leads_data: list, model_id: str) -> Dict[str, Any]:
    if not os.getenv("OPENROUTER_API_KEY"): return {"error": "OPENROUTER_API_KEY missing from .env"}
    
    system_prompt = load_text_file("system_prompt.txt")
    batch_prompt = load_text_file("batch_prompt.txt")
    
    if not system_prompt or not batch_prompt:
        return {"error": "Missing prompt files (.txt)"}
    
    batch_payload = []
    for i, data in enumerate(leads_data):
        req = data['req_dict']
        batch_payload.append({
            "id": i,
            "data": {k: v for k, v in req.items() if v and k not in ['pricing_rules', 'model']},
            "intel": data['background_text'][:1000]
        })
        
    try:
        prompt = f"{system_prompt}\n\n{batch_prompt}\n{json.dumps(batch_payload, separators=(',', ':'))}"
        
        response = call_openrouter(model_id, prompt)
        raw = response.choices[0].message.content
        
        if not raw:
            return {"error": "AI returned an empty response."}
            
        # Use our new robust sanitizer
        return sanitize_json(raw)
        
    except Exception as e:
        return {"error": str(e)}

def calculate_pricing(input_dict, ai_response):
    pr = input_dict.get("pricing_rules", {})
    rates = {
        "Web VAPT": pr.get("web", 1500), 
        "API VAPT": pr.get("api", 1000), 
        "Network VAPT": pr.get("net", 800)
    }
    base_rate = rates.get(input_dict.get("service_type", "Web VAPT"), 1500)
    scope = input_dict.get("scope_count", 1)
    
    c_mult = {"Low": 1.0, "Medium": 1.2, "High": 1.5}
    calc = int(base_rate * scope * c_mult.get(input_dict.get("complexity", "Medium"), 1.2))
    
    tier = ai_response.get("company_profile", {}).get("deduced_company_tier", "")
    if "Enterprise" in tier or "Unicorn" in tier:
        calc = int(calc * pr.get("ent", 1.5))
        
    final = max(calc, pr.get("min", 2500))
    return {"suggested_quote": final, "price_min": int(final * 0.85), "price_max": int(final * 1.15), "base_rate": base_rate}

# ==========================================
# --- ROUTES ---
# ==========================================
@app.get('/api/countries')
def countries():
    try:
        res = requests.get("https://restcountries.com/v3.1/all?fields=name", timeout=5)
        if res.status_code == 200:
            return {"countries": sorted([c['name']['common'] for c in res.json()])}
    except: pass
    return {"countries": ["United States", "United Kingdom", "India", "Australia", "Canada", "Germany", "France", "Other"]}

@app.get('/api/history')
def history():
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT timestamp, sales_person, company_name, service_type, decision, suggested_price FROM leads ORDER BY timestamp DESC")
            rows = cursor.fetchall()
            return {"history": [{"timestamp": r['timestamp'], "company_name": r['company_name'], "service_type": r['service_type'], "decision": r['decision'], "suggested_price": r['suggested_price']} for r in rows]}
    except Exception as e:
        return {"history": [], "error": str(e)}

@app.post('/api/process')
async def analyze(data: LeadRequest):
    logging.info("🚨 NEW REQUEST STARTED")
    try:
        req_dict = data.model_dump() # Updated to model_dump()
        
        prev_enq = check_previous_enquiry(data.company_name, data.website)
        prev_data = None
        if prev_enq:
            prev_data = {
                "time_ago": get_time_ago(prev_enq['timestamp']),
                "decision": prev_enq['decision'],
                "suggested_price": prev_enq['suggested_price']
            }

        if DEV_MODE:
            mock_ai = {
                "lead_score": "High", "conversion_probability": 85, "deal_quality": "Excellent", 
                "company_profile": {"deduced_company_tier": "Mid-Market", "estimated_headcount": 250},
                "next_action": "Schedule immediate discovery call."
            }
            pricing = calculate_pricing(req_dict, mock_ai)
            mock_ai['pricing'] = pricing
            mock_ai['previous_enquiry'] = prev_data
            return {"success": True, "data": mock_ai}
            
        background_text = gather_background_intelligence(data.company_name, data.country, data.website)
        
        ai_response: Dict[str, Any] = analyze_lead(req_dict, background_text, data.model)
        if "error" in ai_response: 
            raise HTTPException(status_code=500, detail=ai_response['error'])
            
        pricing = calculate_pricing(req_dict, ai_response)
        ai_response['pricing'] = pricing
        ai_response['previous_enquiry'] = prev_data
        
        save_lead('Sales Team', data.company_name, data.website, data.service_type, req_dict, ai_response, 
                 f"{str(ai_response.get('lead_score', 'Low')).capitalize()} ({str(ai_response.get('conversion_probability', 'N/A'))})", 
                 pricing.get('suggested_quote', 0))
                 
        return {"success": True, "data": ai_response}
    except Exception as e:
        logging.error(f"❌ PYTHON CRASHED:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post('/api/log')
def save_frontend_log(log_data: FrontendLog):
    """Receives logs from the React frontend and writes them to the frontend file"""
    if log_data.level == 'error':
        frontend_logger.error(log_data.message)
    elif log_data.level == 'warn':
        frontend_logger.warning(log_data.message)
    else:
        frontend_logger.info(log_data.message)
    return {"status": "logged"}

@app.post('/api/process-batch')
async def process_batch(batch: BatchLeadRequest):
    logging.info(f"🚨 NEW BATCH REQUEST STARTED: {len(batch.leads)} leads")
    try:
        if not batch.leads:
            return {"success": False, "error": "No leads provided."}
            
        model = batch.leads[0].model 
        
        async def scrape_lead(lead):
            loop = asyncio.get_running_loop()
            bg_text = await loop.run_in_executor(
                None, 
                gather_background_intelligence, 
                lead.company_name, 
                lead.country, 
                lead.website
            )
            return {"req_dict": lead.model_dump(), "background_text": bg_text} # Updated to model_dump()

        leads_data = await asyncio.gather(*(scrape_lead(lead) for lead in batch.leads))
        
        ai_batch_response = analyze_lead_batch(leads_data, model)
        
        if "error" in ai_batch_response:
            raise HTTPException(status_code=500, detail=ai_batch_response['error'])
            
        final_results = []
        
        for ai_result in ai_batch_response.get('results', []):
            lead_idx = ai_result.get('id')
            if not isinstance(lead_idx, int) or lead_idx >= len(leads_data):
                continue
            req_dict = leads_data[lead_idx]['req_dict']
            
            pricing = calculate_pricing(req_dict, ai_result)
            ai_result['pricing'] = pricing
            
            save_lead(
                'Sales Team', 
                req_dict['company_name'], 
                req_dict['website'], 
                req_dict['service_type'], 
                req_dict, 
                ai_result, 
                f"{str(ai_result.get('lead_score', 'Low')).capitalize()} ({str(ai_result.get('conversion_probability', 'N/A'))})", 
                pricing.get('suggested_quote', 0)
            )
            final_results.append(ai_result)
            
        return {"success": True, "data": final_results}
        
    except Exception as e:
        logging.error(f"❌ BATCH CRASH:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))