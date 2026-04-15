# app.py - Lead Intelligence Engine (Flask + Waitress Production Edition)
import os
import json
import re
import time
import traceback
import requests
import pymysql
import base64
from datetime import datetime
from dotenv import load_dotenv

# Updated Import
from ddgs import DDGS 
from openai import OpenAI
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import logging

# This creates a text file right next to app.py that records everything
logging.basicConfig(
    filename='DEBUG_LOG.txt', 
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s'
)

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'lead-intel-secret-key-2026')
CORS(app)

# ==========================================
# --- ENVIRONMENT TOGGLE ---
# ==========================================
DEV_MODE = False 

# ==========================================
# --- DATABASE & HELPER FUNCTIONS ---
# ==========================================
def get_db_connection():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "lead_intel"),
        cursorclass=pymysql.cursors.DictCursor
    )
#when was the previous enquuiry for this company and what was the decision and suggested price
def check_previous_enquiry(company_name, website):
    try:
        conn = get_db_connection()
        # Strip out http/www to do a "fuzzy" match on the core domain
        core_domain = website.replace("https://", "").replace("http://", "").replace("www.", "").strip('/')
        
        with conn.cursor() as cursor:
            # Use LIKE for fuzzy matching
            cursor.execute("""
                SELECT timestamp, decision, suggested_price 
                FROM leads 
                WHERE company_name LIKE %s OR website LIKE %s 
                ORDER BY timestamp DESC LIMIT 1
            """, (f"%{company_name}%", f"%{core_domain}%"))
            result = cursor.fetchone()
        conn.close()
        
        if result:
            print(f"🔄 [DATABASE] Repeat Lead Found: {company_name}")
        else:
            print(f"✨ [DATABASE] New Lead (No prior records): {company_name}")
            
        return result
    except Exception as e:
        print(f"❌ [DATABASE] Error checking history: {e}")
        return None
# Convert datetime to "X days ago" format
def get_time_ago(dt_obj):
    if not dt_obj: return "in the past"
    
    # Safely handle it if the database returns a string instead of a datetime object
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
# Save the lead and AI decision to the database
def save_lead(sales_person, company_name, website, service_type, input_json, output_json, decision, suggested_price):
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO leads (sales_person, company_name, website, service_type, full_input_json, full_output_json, decision, suggested_price, timestamp)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (sales_person, company_name, website, service_type, json.dumps(input_json), json.dumps(output_json), decision, suggested_price, datetime.now()))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"DB Error: {e}")
        return False
# Fetch recent history for the dashboard (last 20 entries)
def fetch_recent_history():
    try:
        conn = get_db_connection()
        # Removed the LIMIT clause to pull the entire history
        query = "SELECT timestamp, sales_person, company_name, service_type, decision, suggested_price FROM leads ORDER BY timestamp DESC"
        with conn.cursor() as cursor:
            cursor.execute(query)
            results = cursor.fetchall()
        conn.close()
        return results
    except Exception as e:
        print(f"❌ [DATABASE] Error fetching history: {e}")
        return []
# Fetch list of countries for the dropdown (using restcountries API with fallback)
def fetch_countries():
    try:
        response = requests.get("https://restcountries.com/v3.1/all?fields=name", timeout=5)
        if response.status_code == 200:
            data = response.json()
            return sorted([country['name']['common'] for country in data])
    except:
        pass
    return ["United States", "United Kingdom", "India", "Australia", "Canada", "Germany", "France", "Other"]
# Load the system prompt template from a text file
def load_prompt_template():
    try:
        with open(os.path.join(os.path.dirname(__file__), "system_prompt.txt"), "r", encoding="utf-8") as f:
            return f.read()
    except:
        return ""

def get_image_base64(filename):
    try:
        with open(os.path.join(os.path.dirname(__file__), filename), "rb") as f:
            return base64.b64encode(f.read()).decode()
    except:
        return None

# ==========================================
# --- 1. MULTI-STAGE WEBSITE SCRAPER ---
# ==========================================

# This function attempts to scrape the company's website using BeautifulSoup first, and if it detects blocks or empty content, it falls back to using the Firecrawl API for a more robust extraction. It also includes checks for common anti-scraping blocks and returns a clean text snippet of the website's content.
def scrape_company_website(url):
    if not url or url == "N/A": 
        return ""
        
    print(f"\n[SCRAPER] 🧠 BRAIN 1: Visiting company website -> {url}")
    if not url.startswith("http"): 
        url = "https://" + url
            
    # 💥 ATTEMPT 1: BeautifulSoup (Free, Fast)
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(url, headers=headers, timeout=7)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            for script in soup(["script", "style"]): script.extract()
            text = soup.get_text(separator=' ', strip=True)
            clean_text = text[:1500] 
            
            if len(clean_text) > 100:
                if "Just a moment" in clean_text or "Enable JavaScript" in clean_text or "cloudflare" in clean_text.lower():
                    raise Exception("Cloudflare CAPTCHA block detected.")
                    
                print(f"[SCRAPER] -> BS4 Success! Extracted {len(clean_text)} characters.")
                return f"COMPANY WEBSITE:\n{clean_text}\n\n"
                
        print(f"[SCRAPER] ⚠️ BS4 blocked or empty (Status: {response.status_code}). Triggering Fallback...")
    except Exception as e:
        print(f"[SCRAPER] ⚠️ BS4 Failed: {e}. Triggering Fallback...")

    # 💥 ATTEMPT 2: The Firecrawl Fallback
    try:
        print(f"[SCRAPER] 🚀 FALLBACK: Firing advanced scraper (Firecrawl)...")
        firecrawl_key = os.getenv("FIRECRAWL_API_KEY")
        
        if firecrawl_key:
            headers = {"Authorization": f"Bearer {firecrawl_key}", "Content-Type": "application/json"}
            payload = {"url": url, "formats": ["markdown"]}
            
            fc_response = requests.post("https://api.firecrawl.dev/v1/scrape", headers=headers, json=payload, timeout=15)
            
            if fc_response.status_code == 200:
                fc_data = fc_response.json()
                markdown_text = fc_data.get("data", {}).get("markdown", "")
                clean_text = markdown_text[:1500]
                print(f"[SCRAPER] -> Fallback Success! Extracted {len(clean_text)} characters.")
                return f"COMPANY WEBSITE (FIRECRAWL EXTRACT):\n{clean_text}\n\n"
            else:
                print(f"[SCRAPER] ❌ Fallback failed with status: {fc_response.status_code}")
        else:
            print("[SCRAPER] ⏭️ No FIRECRAWL_API_KEY found in .env. Skipping fallback.")
            
    except Exception as e:
        print(f"[SCRAPER] ❌ Fallback Scraper Exception: {e}")

    return "COMPANY WEBSITE: Could not access directly. Rely strictly on Search Engine Intel.\n\n"

# ==========================================
# --- 2. RUTHLESS SCRAPING ENGINE ---
# ==========================================

# This function constructs multiple search queries based on the company name, domain, and location to gather recent news and intelligence from search engines. It first tries to use the DDGS library for scraping DuckDuckGo results, and if that fails (due to rate limits or blocks), it falls back to using the Serper API for Google search results. The function then applies aggressive filtering to remove spammy or irrelevant results, ensuring that only high-quality intelligence is returned for AI analysis.
def gather_background_intelligence(company_name, country_name="", website_url=""):
    if not company_name:
        return "No reliable recent news found."
        
    print(f"\n[SCRAPER] --- STARTING INTELLIGENCE GATHERING FOR: {company_name} ---")
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
    
    # 💥 ATTEMPT 1: DDGS
    ddgs = DDGS()
    for i, q in enumerate(queries, 1):
        print(f"[SCRAPER] Executing DDGS Query {i}/{len(queries)}: {q}")
        try:
            res = ddgs.text(q, max_results=8) 
            res_list = list(res) if res else []
            print(f"[SCRAPER] -> Found {len(res_list)} raw results.")
            raw_results.extend(res_list)
        except Exception as e:
            print(f"[SCRAPER] ⚠️ DDGS Query {i} Failed/Rate-limited. Skipping...")
            continue 
        time.sleep(2) # Reduced sleep for Flask speed, adjust to 7 if rate-limited

    # 💥 ATTEMPT 2: Serper Fallback
    if not raw_results:
        print("[SCRAPER] ❌ DDGS FAILED COMPLETELY. TRIGGERING SERPER FALLBACK...")
        serper_key = os.getenv("SERPER_API_KEY")
        if serper_key:
            headers = {'X-API-KEY': serper_key, 'Content-Type': 'application/json'}
            fallback_queries = [
                f'"{company_name}" "{core_domain}" business OR funding OR lawsuit',
                f'what year was "{company_name}" "{core_domain}" founded employees'
            ]
            for q in fallback_queries:
                try:
                    payload = json.dumps({"q": q, "num": 8})
                    resp = requests.post("https://google.serper.dev/search", headers=headers, data=payload, timeout=10)
                    if resp.status_code == 200:
                        search_data = resp.json().get("organic", [])
                        for item in search_data:
                            raw_results.append({"title": item.get("title", ""), "href": item.get("link", ""), "body": item.get("snippet", "")})
                except Exception as e:
                    print(f"[SCRAPER] ❌ Serper query failed: {e}")
        else:
            print("[SCRAPER] ⏭️ No SERPER_API_KEY found in .env. Skipping fallback.")

    if not raw_results:
        return "No reliable recent news found."

    clean_results = []
    seen_urls = set()
    
    spam_domains = ['tiktok.com', 'reddit.com', 'facebook.com', 'instagram.com', 'twitter.com', 'x.com', 'youtube.com', 'pinterest.com', 'snapchat.com', 'vk.com', 't.me', 'whatsapp.com', 'dnaindia.com']
    dead_words = ['latest news and live updates', 'read latest news', 'journal of', 'symposium', 'conference proceedings', 'white paper']
    
    name_parts = company_name.lower().split()
    core_name = " ".join(name_parts[:2]) if len(name_parts) > 1 else name_parts[0]
    
    for r in raw_results:
        title = r.get('title', '')
        url = r.get('href', '')
        snippet = r.get('body', '')
        title_lower = title.lower()
        snippet_lower = snippet.lower()
        
        if url in seen_urls: continue
        if any(domain in url.lower() for domain in spam_domains): continue
        if ".pdf" in url.lower(): continue
        
        if "com" in url.lower() or "co.uk" in url.lower() or ".in" in url.lower():
            if core_domain not in url.lower() and "linkedin.com" not in url.lower() and "zoominfo.com" not in url.lower() and "crunchbase.com" not in url.lower():
                continue
                
        if any(dead in title_lower for dead in dead_words): continue
        if core_name not in title_lower and core_name not in snippet_lower: continue
        if len(title.split()) < 3: continue
        if any(spam in title_lower for spam in ['login', 'buy', 'cheap', 'click here', 'subscribe']): continue
        
        seen_urls.add(url)
        clean_results.append({"title": title, "snippet": snippet[:300]})
        if len(clean_results) >= 35: break

    if not clean_results:
        return "No reliable recent news found."

    formatted_lines = [f"- {r['title']}: {r['snippet']}" for r in clean_results]
    ddg_text = "SEARCH ENGINE INTEL (THIRD-PARTY REALITY):\n" + "\n".join(formatted_lines)
    
    site_text = scrape_company_website(website_url)
    
    return site_text + ddg_text

# ==========================================
# --- AI ANALYSIS ---
# ==========================================
# This function is responsible for calling the OpenRouter API to execute the Llama/Gemma model. It includes a retry mechanism with exponential backoff to handle transient errors or rate limits. The function takes the constructed prompt and model ID as input and returns the AI's response, which will then be processed to extract the lead intelligence insights.
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=10), reraise=True)
def call_openrouter(model_id, prompt_content):
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.getenv("OPENROUTER_API_KEY"))
    return client.chat.completions.create(
        model=model_id, messages=[{"role": "user", "content": prompt_content}],
        temperature=0.0, seed=42
    )
# This function takes the input data from the user, the gathered background intelligence, and the specified model ID to construct a detailed prompt for the AI. It then calls the OpenRouter API to execute the model and processes the response to extract the lead intelligence insights. The function also includes error handling to manage cases where the AI returns invalid JSON or when required environment variables are missing.
def analyze_lead(input_dict, background_text, model_id):
    if not os.getenv("OPENROUTER_API_KEY"): return {"error": "OPENROUTER_API_KEY missing from .env"}
    template = load_prompt_template()
    if not template: return {"error": "Missing system_prompt.txt"}
    
    try:
        pr = input_dict.get("pricing_rules", {})
        prompt = template.format(
            company_name=input_dict.get('company_name', 'N/A'),
            website=input_dict.get('website', 'N/A'),
            linkedin=input_dict.get('linkedin_url', 'N/A'),
            country=input_dict.get('country', 'N/A'),
            company_domain=input_dict.get('industry', 'Not specified'),
            service=input_dict.get('service_type', 'N/A'),
            scope=input_dict.get('scope_count', 1),
            complexity=input_dict.get('complexity', 'N/A'),
            lead_type=input_dict.get('lead_type', 'N/A'),
            clarity=input_dict.get('clarity', 'N/A'),
            stage=input_dict.get('buying_stage', 'N/A'),
            sensitivity=input_dict.get('price_sensitivity', 'N/A'),
            timeline=input_dict.get('timeline', 'N/A'),
            competitors="Yes" if input_dict.get('competitors') else "No",
            pr_web=pr.get('web', 0), pr_api=pr.get('api', 0), pr_net=pr.get('net', 0),
            pr_min=pr.get('min', 0), pr_ent=pr.get('ent', 1.0),
            background_text=background_text
        )
        
        response = call_openrouter(model_id, prompt)
        raw = response.choices[0].message.content
        start, end = raw.find('{'), raw.rfind('}')
        if start == -1 or end == -1: return {"error": "AI returned invalid JSON structure."}
        
        clean = raw[start:end+1] # type: ignore
        clean = re.sub(r',\s*}', '}', clean) # JSON Scrubber
        clean = re.sub(r',\s*]', ']', clean)
        return json.loads(clean)
    except Exception as e:
        return {"error": str(e)}
# This function calculates the suggested pricing for the lead based on the input parameters and the AI's response. It uses predefined pricing rules and applies multipliers based on the service type, scope, complexity, and the deduced company tier from the AI analysis. The function ensures that the final suggested quote meets a minimum threshold and returns a structured response with the suggested quote, price range, and base rate.
def calculate_pricing(input_dict, ai_response):
    pr = input_dict.get("pricing_rules", {})
    rates = {"Web VAPT": pr.get("web", 1500), "API VAPT": pr.get("api", 1000), "Network VAPT": pr.get("net", 800)}
    base_rate = rates.get(input_dict.get("service_type", "Web VAPT"), 1500)
    scope = input_dict.get("scope_count", 1)
    
    c_mult = {"Low": 1.0, "Medium": 1.2, "High": 1.5}
    calc = int(base_rate * scope * c_mult.get(input_dict.get("complexity", "Medium"), 1.2))
    
    # Apply Enterprise Multiplier if AI deduced high tier
    tier = ai_response.get("company_profile", {}).get("deduced_company_tier", "")
    if "Enterprise" in tier or "Unicorn" in tier:
        calc = int(calc * pr.get("ent", 1.5))
        
    final = max(calc, pr.get("min", 2500))
    return {"suggested_quote": final, "price_min": int(final * 0.85), "price_max": int(final * 1.15), "base_rate": base_rate}

# ==========================================
# --- ROUTES ---
# ==========================================
@app.route('/')
def index():
    return render_template('index.html', countries=fetch_countries(), logo=get_image_base64("logo.png"))

# API endpoint to analyze the lead and return AI insights along with pricing suggestions. It also checks for previous enquiries for the same company and includes that information in the response. The endpoint handles both the AI analysis and the database interactions to save the lead information and decision.
@app.route('/api/process', methods=['POST'])
def analyze():
    data = request.get_json()

    # 1. Define every exact key your backend expects to receive
    expected_keys = [
        'company_name', 'website', 'model', 'linkedin_url', 'country', 'industry',
        'service_type', 'scope_count', 'complexity', 'lead_type', 'clarity',
        'buying_stage', 'price_sensitivity', 'timeline', 'competitors',
        'web_rate', 'api_rate', 'net_rate', 'min_value', 'ent_mult'
    ]

    # 2. Check the incoming data against the checklist
    missing_keys = []
    for key in expected_keys:
        if key not in data:
            missing_keys.append(key)

    # 3. If anything is missing, tell the frontend EXACTLY what it is
    if missing_keys:
        error_msg = f"Missing required fields: {', '.join(missing_keys)}"
        logging.error(f"Validation Failed. {error_msg}") # Good for your server logs
        return jsonify({
            'success': False,
            'error': error_msg
        }), 400

        
    logging.info("🚨 NEW REQUEST STARTED from frontend!")
    try:
    
            
        # Check DB BEFORE we save the new one
        prev_enq = check_previous_enquiry(data['company_name'], data['website'])
        prev_data = None
        if prev_enq:
            prev_data = {
                "time_ago": get_time_ago(prev_enq['timestamp']),
                "decision": prev_enq['decision'],
                "suggested_price": prev_enq['suggested_price']
            }

        if DEV_MODE:
            print("🚀 DEV MODE: Returning Mock Data...")
            time.sleep(1) 
            mock_ai = {
                "lead_score": "High", "conversion_probability": 85, "deal_quality": "Excellent", 
                "company_profile": {"deduced_company_tier": "Mid-Market", "estimated_headcount": 250},
                "next_action": "Schedule immediate discovery call."
            }
            pricing = calculate_pricing(data, mock_ai)
            mock_ai['pricing'] = pricing
            mock_ai['previous_enquiry'] = prev_data
            return jsonify({"success": True, "data": mock_ai})
        
        # REAL PIPELINE
        background_text = gather_background_intelligence(data['company_name'], data.get('country', ''), data['website'])
        print("\n🧠 Executing Llama/Gemma Model via OpenRouter...")
        ai_response = analyze_lead(data, background_text, data.get('model', 'meta-llama/llama-3.1-8b-instruct'))
        
        if "error" in ai_response: 
            print(f"❌ AI Error: {ai_response['error']}")
            return jsonify({"error": ai_response['error']}), 500
        
        print("✅ AI Inference Complete. Processing calculations...")
        pricing = calculate_pricing(data, ai_response)
        ai_response['pricing'] = pricing
        ai_response['previous_enquiry'] = prev_data
        
        save_lead('Sales Team', data['company_name'], data['website'], data['service_type'], data, ai_response, 
                 f"{str(ai_response.get('lead_score', 'Low')).capitalize()} ({str(ai_response.get('conversion_probability', 'N/A'))})", 
                 pricing.get('suggested_quote', 0))
        logging.info("✅ REQUEST FINISHED SUCCESSFULLY! Sending JSON back.")
        
        return jsonify({"success": True, "data": ai_response})
    except Exception as e:
        print(f"❌ Server Error: {str(e)}")
        
        logging.error(f"❌ PYTHON CRASHED:\n{traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500

# API endpoint to fetch recent history of leads for the dashboard. It retrieves the data from the database, formats the timestamps to a human-readable "time ago" format, and returns a structured JSON response that can be easily displayed on the frontend.
@app.route('/api/history', methods=['GET'])
def history():
    data = fetch_recent_history()
    return jsonify({"history": [{"timestamp": item['timestamp'].isoformat(), "company_name": item['company_name'], 
                                  "service_type": item['service_type'], "decision": item['decision'], 
                                  "suggested_price": item['suggested_price']} for item in data]})

# API endpoint to fetch the list of countries for the dropdown in the lead input form. It uses the restcountries API to get an updated list of countries, and if that fails, it falls back to a hardcoded list of common countries. The endpoint returns a JSON response with the list of countries that can be used to populate the dropdown on the frontend.
@app.route('/api/countries', methods=['GET'])
def countries():
    return jsonify({"countries": fetch_countries()})

# ==========================================
# --- SERVER RUN LOGIC ---
# ==========================================
if __name__ == '__main__':
    if DEV_MODE:
        print("\n" + "="*50)
        print("⚠️  RUNNING IN DEV MODE  ⚠️")
        print("AI calls are bypassed. Auto-reload is ON.")
        print("="*50 + "\n")
        app.run(debug=True, host='127.0.0.1', port=5004)
    else:
        from waitress import serve
        print("\n" + "="*50)
        print("🚀 RUNNING IN PRODUCTION MODE (Waitress)")
        print("Listening on http://127.0.0.1:5004")
        print("="*50 + "\n")
        serve(app, host='127.0.0.1', port=5004)