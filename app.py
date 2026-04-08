import streamlit as st
import pymysql
import os
import json
import pandas as pd
import requests
import base64
import time
import re
from dotenv import load_dotenv
from ddgs import DDGS
from openai import OpenAI
from datetime import datetime
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

st.set_page_config(page_title="Lead Intelligence Engine", layout="wide", initial_sidebar_state="collapsed")
load_dotenv()

# --- HELPER: TIME AGO CALCULATOR ---
def get_time_ago(dt_obj):
    if not dt_obj: return "in the past"
    if isinstance(dt_obj, str):
        try: dt_obj = pd.to_datetime(dt_obj)
        except: return "in the past"
        
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

# --- DB CONNECTION & FETCH ---
def get_db_connection():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "lead_intel"),
        cursorclass=pymysql.cursors.DictCursor
    )

def check_previous_enquiry(company_name, website):
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT timestamp, decision, suggested_price 
                FROM leads 
                WHERE LOWER(company_name) = LOWER(%s) OR LOWER(website) = LOWER(%s) 
                ORDER BY timestamp DESC LIMIT 1
            """, (company_name, website))
            result = cursor.fetchone()
        conn.close()
        return result
    except:
        return None

def fetch_recent_history():
    try:
        conn = get_db_connection()
        query = "SELECT timestamp, sales_person, company_name, service_type, decision, suggested_price, full_output_json FROM leads ORDER BY timestamp DESC"
        with conn.cursor() as cursor:
            cursor.execute(query)
            results = cursor.fetchall()
        conn.close()
        
        if not results: return pd.DataFrame()
        df = pd.DataFrame(results)

        signals = []
        for out_json in df['full_output_json']:
            try:
                parsed = json.loads(out_json)
                insights = parsed.get("background_insights", [])
                if insights and isinstance(insights, list) and "none" not in str(insights[0]).lower():
                     signals.append("Available")
                else:
                     signals.append("None")
            except:
                signals.append("None")
        
        df['Background Signal'] = signals
        df['Date/Time'] = pd.to_datetime(df['timestamp']).dt.strftime('%Y-%m-%d %H:%M')
        df['View Details'] = "🔍 View"
        
        df = df[['Date/Time', 'sales_person', 'company_name', 'service_type', 'decision', 'suggested_price', 'Background Signal', 'View Details']]
        df.columns = ['Date/Time', 'Sales Person', 'Company', 'Service', 'Decision', 'Quote', 'Signal', 'Action']
        return df
    except:
        return pd.DataFrame()

@st.cache_data
def fetch_countries():
    try:
        response = requests.get("https://restcountries.com/v3.1/all?fields=name", timeout=5)
        if response.status_code == 200:
            data = response.json()
            return sorted([country['name']['common'] for country in data])
    except:
        pass
    return ["United States", "United Kingdom", "India", "Australia", "Canada", "Germany", "Other"]

COUNTRIES = fetch_countries()

def load_prompt_template():
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(base_dir, "system_prompt.txt")
        with open(file_path, "r", encoding="utf-8") as file:
            return file.read()
    except Exception as e:
        st.error(f"Failed to load prompt template: {e}")
        return ""
        
# ==========================================
# --- BRAIN 1: MULTI-STAGE WEBSITE SCRAPER ---
# ==========================================
def scrape_company_website(url):
    if not url or url == "N/A": 
        return ""
        
    print(f"\n[SCRAPER] 🧠 BRAIN 1: Visiting company website -> {url}")
    if not url.startswith("http"): 
        url = "https://" + url
            
    # 💥 ATTEMPT 1: BeautifulSoup (Free, Fast)
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        # Strict 7-second timeout so the app never hangs forever
        response = requests.get(url, headers=headers, timeout=7)
        
        # If the site blocks bots (403), skip BS4 and go straight to the fallback
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            for script in soup(["script", "style"]): script.extract()
            text = soup.get_text(separator=' ', strip=True)
            clean_text = text[:1500] 
            
            # Make sure we actually got text, not just a blank JS-rendered page
            # Make sure we actually got text, not just a blank JS-rendered page
            if len(clean_text) > 100:
                # 💥 CLOUDFLARE TRAP: Check if it's a fake success page
                if "Just a moment" in clean_text or "Enable JavaScript" in clean_text or "cloudflare" in clean_text.lower():
                    raise Exception("Cloudflare CAPTCHA block detected.")
                    
                print(f"[SCRAPER] -> BS4 Success! Extracted {len(clean_text)} characters.")
                return f"COMPANY WEBSITE:\n{clean_text}\n\n"
                
        print(f"[SCRAPER] ⚠️ BS4 blocked or empty (Status: {response.status_code}). Triggering Fallback...")
    except Exception as e:
        print(f"[SCRAPER] ⚠️ BS4 Failed: {e}. Triggering Fallback...")

    # 💥 ATTEMPT 2: The Firecrawl Fallback (Bypasses Cloudflare & renders JS)
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
    
    # 💥 ATTEMPT 1: DuckDuckGo (Free)
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
        time.sleep(7)

    # 💥 ATTEMPT 2: Serper Fallback (If DDG is totally banned)
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
# --- 6. API BACKEND FLOW & ARMOR PLATING ---
# ==========================================
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=10), reraise=True)
def call_openrouter_with_retry(client, model_id, prompt_content):
    return client.chat.completions.create(
        model=model_id, 
        messages=[{"role": "user", "content": prompt_content}],
        temperature=0.0,
        seed=42,
        extra_headers={"HTTP-Referer": "https://valencynetworks.com", "X-Title": "Lead Intel Engine"}
    )

def analyze_lead_with_groq(input_dict, background_text, model_id):
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return {"error": "OPENROUTER_API_KEY is missing from your .env file."}

    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
    pr = input_dict.get("pricing_rules", {})
    raw_template = load_prompt_template()
    
    if not raw_template:
        return {"error": "Missing or empty system_prompt.txt file."}

    try:
        prompt_content = raw_template.format(
            company_name=input_dict.get('company_name', 'N/A'),
            website=input_dict.get('website', 'N/A'),
            linkedin=input_dict.get('linkedin', 'N/A'),
            country=input_dict.get('country', 'N/A'),
            company_domain=input_dict.get('company_domain', 'Not specified'),
            service=input_dict.get('service', 'N/A'),
            scope=input_dict.get('scope', 0),
            complexity=input_dict.get('complexity', 'N/A'),
            lead_type=input_dict.get('lead_type', 'N/A'),
            clarity=input_dict.get('clarity', 'N/A'),
            stage=input_dict.get('stage', 'N/A'),
            sensitivity=input_dict.get('sensitivity', 'N/A'),
            timeline=input_dict.get('timeline', 'N/A'),
            competitors=input_dict.get('competitors', 'N/A'),
            pr_web=pr.get('web', 0),
            pr_api=pr.get('api', 0),
            pr_net=pr.get('net', 0),
            pr_min=pr.get('min', 0),
            pr_ent=pr.get('ent', 1.0),
            background_text=background_text
        )
    except Exception as template_err:
        return {"error": f"Template Formatting Error: {str(template_err)}"}

    try:
        response = call_openrouter_with_retry(client, model_id, prompt_content)
        raw_response = response.choices[0].message.content
        
        start_idx = raw_response.find('{')
        end_idx = raw_response.rfind('}')
        
        if start_idx != -1 and end_idx != -1:
            clean_json = raw_response[start_idx:end_idx+1]
            clean_json = re.sub(r',\s*}', '}', clean_json)
            clean_json = re.sub(r',\s*]', ']', clean_json)
            clean_json = re.sub(r'(?<=\d),(?=\d)', '', clean_json)
            try:
                parsed_data = json.loads(clean_json)
                parsed_data["_raw_debug"] = raw_response
                return parsed_data
            except Exception as parse_error:
                return {"error": f"AI produced invalid JSON formatting: {str(parse_error)}", "_raw_debug": raw_response}
        else:
            return {"error": "AI completely failed to output JSON brackets.", "_raw_debug": raw_response}
            
    except Exception as e:
        return {"error": f"API Connection Error (Failed after 3 retries): {str(e)}", "_raw_debug": "No response received."}


# ==========================================
# --- UI INITIALIZATION & PREMIUM CSS ---
# ==========================================
def get_image_base64(filename):
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(base_dir, filename)
        with open(file_path, "rb") as img_file: 
            return base64.b64encode(img_file.read()).decode()
    except Exception: 
        return None

logo_b64 = get_image_base64("logo.png")
logo_html = f'<img src="data:image/png;base64,{logo_b64}" height="35">' if logo_b64 else '<b>⬛ LOGO</b>'

st.markdown(f"""
<style>
    [data-testid="stAppViewContainer"] {{ background-color: #f4f7f9; }}
    .block-container {{ padding-top: 56px !important; padding-bottom: 24px !important; max-width: 98% !important; }}
    [data-testid="stVerticalBlock"] > [data-testid="stVerticalBlock"] {{ height: auto !important; }}
    header[data-testid="stHeader"] {{ background: transparent; height: 0px; }}
    .fixed-header {{
        position: fixed; top: 0; left: 0; width: 100%; background-color: rgba(255, 255, 255, 0.95); 
        backdrop-filter: blur(10px); z-index: 9999; border-bottom: 1px solid #e2e8f0;
        padding: 8px 40px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }}
    .custom-footer {{
        background-color: #000055; color: white; text-align: center; padding: 5px 0; font-size: 11px;
        position: fixed; bottom: 0; left: 0; width: 100%; z-index: 999;
    }}
    input[type="text"], input[type="number"], .stSelectbox div[data-baseweb="select"] {{ 
        min-height: 32px !important; height: 32px !important; font-size: 0.82rem !important; 
        border-radius: 6px !important; border: 1px solid #cbd5e1 !important; background-color: #ffffff !important;
    }}
    label[data-testid="stWidgetLabel"] {{ font-size: 0.78rem !important; color: #475569 !important; padding-bottom: 1px !important; margin-bottom: 0px !important; font-weight: 600 !important;}}
    div[data-testid="stVerticalBlock"] > div {{ padding-bottom: 0 !important; }}
    p {{ margin-bottom: 0.15rem !important; font-size: 0.82rem !important; }}
    h6 {{ margin-top: 6px !important; margin-bottom: 5px !important; font-size: 0.9rem !important; font-weight: 800 !important; color: #0f172a; text-transform: uppercase; letter-spacing: 0.5px;}}
    button[kind="primary"] {{
        background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%) !important; border: none !important; 
        box-shadow: 0 4px 6px -1px rgba(37, 99, 235, 0.3) !important; border-radius: 8px !important; 
        font-weight: 600 !important; transition: all 0.2s; color: white !important; margin-top: 6px !important;
    }}
    button[kind="primary"]:hover {{ transform: translateY(-1px); box-shadow: 0 6px 12px -2px rgba(37, 99, 235, 0.4) !important; }}
    div.row-widget.stRadio > div {{ flex-direction: row; align-items: center; gap: 8px; }}
    .glass-card {{
        background: #ffffff; border: 1px solid #e2e8f0; border-radius: 10px; 
        padding: 12px; box-shadow: 0 2px 4px -1px rgba(0,0,0,0.03); margin-bottom: 10px !important;
    }}
    [data-testid="stTabsContent"] {{ overflow-y: visible !important; max-height: none !important; padding-right: 4px; }}
    div[data-testid="column"]:first-child {{ overflow-y: visible !important; max-height: none !important; }}
    button[data-baseweb="tab"] {{ font-weight: 600 !important; font-size: 0.85rem !important; padding: 6px 12px !important; }}
    input[type=number]::-webkit-inner-spin-button, input[type=number]::-webkit-outer-spin-button {{ opacity: 1; }}
    hr {{ margin: 8px 0 !important; }}
</style>

<div class="fixed-header">
    <div style="display:flex; align-items:center; gap:15px;">{logo_html} <span style="font-weight:800; font-size:1.2rem; color:#0f172a;">⚡Lead Intelligence Engine</span></div>
    <div style="color: #64748b; font-size: 0.9rem;">User: <b style="color:#0f172a;">Sales Team</b></div>
</div>
<div class="custom-footer">Copyright &copy; 2026 : Valency Networks Private Limited.</div>
""", unsafe_allow_html=True)


# ==========================================
# --- DASHBOARD TABS ---
# ==========================================
tab_dash, tab_hist = st.tabs(["🎯 Live Deal Desk", "📚 Intelligence History"])

with tab_dash:
    input_col, output_col = st.columns([4, 6], gap="large")

    with input_col:
        top_c1, top_c2 = st.columns([1.2, 3]) 
        
        with top_c1:
            st.markdown("<h6 style='margin-top: 6px!important; margin-bottom: 0px!important;'>⚙️ AI Engine:</h6>", unsafe_allow_html=True)
            
        with top_c2:
            MODEL_OPTIONS = {
                "Llama 3.1 (8B)": "meta-llama/llama-3.1-8b-instruct",
                "Gemma 2 (9B)": "google/gemma-2-9b-it",
                "Mistral Nemo": "mistralai/mistral-nemo"
            }
            selected_model_name = st.radio(
                "Engine", list(MODEL_OPTIONS.keys()), horizontal=True, label_visibility="collapsed"
            )
            selected_model_id = MODEL_OPTIONS[selected_model_name]

        st.markdown("<h6>1. Target Profile</h6>", unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        company_name = c1.text_input("Company Name")
        website = c2.text_input("Website (Mandatory)*")
        
        invalid_web = False
        if website and (" " in website.strip() or "." not in website.strip()):
            st.error("⚠️ Invalid URL")
            invalid_web = True

        c3, c4 = st.columns(2)
        linkedin_url = c3.text_input("LinkedIn URL (Opt)")
        invalid_li = False
        if linkedin_url and "linkedin.com" not in linkedin_url.strip().lower():
            st.error("⚠️ Invalid LinkedIn")
            invalid_li = True
            
        country = c4.selectbox("Country", COUNTRIES, index=None)
        
        company_domain = st.selectbox("Company Domain", [
            "Banking & Financial Services", "Insurance", "Healthcare & Pharma",
            "Manufacturing & OT / ICS", "Information Technology & SaaS",
            "Telecom & Media", "Retail & E-Commerce", "Logistics & Supply Chain",
            "Energy & Utilities", "Education & EdTech", "Government & Defence",
            "Legal & Compliance", "Real Estate & Construction", "Hospitality & Travel", "Other"
        ], index=None, placeholder="Select industry domain…")

        st.markdown("<hr style='border:0;border-top:1px solid #cbd5e1;margin:8px 0;'><h6>2. Service Scope</h6>", unsafe_allow_html=True)
        service_type = st.radio("Service Type", ["Web VAPT", "API VAPT", "Network VAPT"], horizontal=True, index=None)
        
        s1, s2 = st.columns(2)
        scope_label = "IPs/Endpoints" if service_type == "Network VAPT" else "URLs/Roles"
        scope_count = s1.number_input(scope_label, min_value=1)
        complexity = s2.select_slider("Complexity", options=["Low", "Medium", "High"], value="Medium")

        st.markdown("<hr>", unsafe_allow_html=True)
        
        p1, p2 = st.columns(2)
        
        with p1.popover("🧠 Lead Signals", width="stretch"):
            lead_type = st.radio("Type", ["New", "Repeat", "Referred"], horizontal=True, index=None)
            req_clarity = st.selectbox("Clarity", ["Vague", "Somewhat clear", "Clear"], index=1)
            buying_stage = st.radio("Stage", ["Exploring", "Comparing", "Ready"], horizontal=True, index=None)
            price_sensitivity = st.selectbox("Price Sens.", ["Low", "Medium", "High"], index=1)
            timeline = st.radio("Timeline", ["Immediate", "Soon", "Not Defined"], horizontal=True, index=None)
            competitor_inv = "Yes" if st.toggle("Competitors?") else "No"
            
        with p2.popover("⚙️ Base Rates", width="stretch"):
            r1, r2 = st.columns(2)
            web_rate = r1.number_input("Web Rate", value=1500)
            api_rate = r2.number_input("API Rate", value=1000)
            net_rate = r1.number_input("Net Rate", value=800)
            min_value = r2.number_input("Min Proj", value=2500)
            ent_mult = st.number_input("Ent. Multiplier", value=1.5, step=0.1)

        is_missing_mandatory = not company_name or not website or not service_type
        has_errors = invalid_web
        
        btn = st.button(
            "Generate Intelligence Report",
            type="primary",
            use_container_width=True,
            disabled=(is_missing_mandatory or has_errors),
            key="generate_intelligence_report"
        )
        if is_missing_mandatory or has_errors:
            st.caption("Please complete required fields and resolve validation errors to enable the button.")

    with output_col:
        if btn:
            errors = []
            comp_clean = company_name.strip()
            web_clean = website.strip().lower()
            li_clean = linkedin_url.strip().lower() if linkedin_url else ""
            
            if not comp_clean: errors.append("Company Name cannot be empty.")
            if not service_type: errors.append("Please select a Service Type.")
            if not web_clean: errors.append("Website is mandatory.")
            elif " " in web_clean or "." not in web_clean: errors.append("Please enter a valid Website URL.")
            if li_clean and "linkedin.com" not in li_clean: errors.append("LinkedIn URL is invalid.")

            if errors:
                for error in errors: st.error(f"⚠️ **Validation Error:** {error}")
            else:
                prev_enq_html = ""
                prev_enquiry = check_previous_enquiry(comp_clean, web_clean)
                if prev_enquiry:
                    t_ago = get_time_ago(prev_enquiry['timestamp'])
                    prev_dec = prev_enquiry['decision']
                    prev_price = prev_enquiry['suggested_price']
                    prev_enq_html = f"""
                    <div style="background:#fffbeb; border:1px solid #fde047; padding:12px 16px; border-radius:8px; margin-bottom:15px; display:flex; align-items:center; gap:12px; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
                        <div style="font-size:1.8rem;">🔄</div>
                        <div>
                            <p style="margin:0; font-size:0.75rem; font-weight:800; color:#b45309; text-transform:uppercase; letter-spacing:0.5px;">Repeat Lead Detected</p>
                            <p style="margin:2px 0 0 0; font-size:0.9rem; color:#78350f;"><b>{comp_clean}</b> previously enquired <b>{t_ago}</b>. Past Decision: <b>{prev_dec}</b> ({prev_price})</p>
                        </div>
                    </div>
                    """

                with st.status("Initializing Intelligence Engine...", expanded=True) as status:
                    st.write("🕵️‍♂️ Scraping the web for digital footprint...")
                    background_text = gather_background_intelligence(comp_clean, country, web_clean)
                    st.write(f"🧠 Running algorithmic inference via {selected_model_name}...")
                    
                    input_data = {
                        "company_name": comp_clean, "website": web_clean, "linkedin": li_clean,
                        "country": country, "company_domain": company_domain or "Not specified",
                        "service": service_type, "scope": scope_count, "complexity": complexity, 
                        "lead_type": lead_type, "clarity": req_clarity, "stage": buying_stage, 
                        "sensitivity": price_sensitivity, "timeline": timeline, "competitors": competitor_inv,
                        "pricing_rules": {"web": web_rate, "api": api_rate, "net": net_rate, "min": min_value, "ent": ent_mult}
                    }
                    
                    ai_response = analyze_lead_with_groq(input_data, background_text, selected_model_id)
                    status.update(label="Analysis Complete!", state="complete", expanded=False)

                raw_debug_text = ai_response.pop("_raw_debug", None)
                if raw_debug_text:
                    with st.expander("🛠️ View Raw AI Output (Debug Console)", expanded=("error" in ai_response)):
                        st.code(raw_debug_text, language="json")

                if "error" in ai_response:
                    st.error(f"🚨 **Critical Pipeline Error:** {ai_response['error']}")
                else:
                    input_json_str = json.dumps(input_data)
                    output_json_str = json.dumps(ai_response)
                    
                    is_valid_b2b = ai_response.get("is_valid_b2b_target", True)
                    lead_score = str(ai_response.get("lead_score", "Low")).capitalize()
                    conversion_probability = str(ai_response.get("conversion_probability", "N/A"))
                    pricing = ai_response.get("pricing", {}) if isinstance(ai_response.get("pricing"), dict) else {}
                    discount = ai_response.get("discount_strategy", {}) if isinstance(ai_response.get("discount_strategy"), dict) else {}
                    org_intel = ai_response.get("company_profile", {}) if isinstance(ai_response.get("company_profile"), dict) else {}
                
                    def safe_int(val, default=0):
                        try: return int(float(val))
                        except: return default

                    raw_price = safe_int(pricing.get('suggested_quote', 0))
                    price_min = safe_int(pricing.get('price_min', raw_price))
                    price_max = safe_int(pricing.get('price_max', int(raw_price * 1.15)))
                
                    is_fake_company = ("No reliable recent news found" in background_text)
                    raw_reasoning = ai_response.get("reasoning_summary") or "The AI analyzed the lead signals."

                    if service_type == "Web VAPT": base_rate = web_rate
                    elif service_type == "API VAPT": base_rate = api_rate
                    else: base_rate = net_rate
                    
                    base_cost = base_rate * scope_count
                    comp_mult = 1.0 if complexity == "Low" else (1.2 if complexity == "Medium" else 1.5)
                    deduced_tier = str(org_intel.get("deduced_company_tier", "Small")).lower()
                    size_mult = ent_mult if deduced_tier == "enterprise" else (1.2 if deduced_tier == "medium" else 1.0)
                    
                    calculated_price = int(base_cost * comp_mult * size_mult)
                    actual_quote = max(calculated_price, int(min_value))
                    display_price = f"₹{actual_quote:,}"

                    if is_fake_company or not is_valid_b2b or (lead_score == "Low" and "fake" in str(ai_response).lower()):
                        lead_score = "Low"
                        conversion_probability = "< 5%"
                        inval_reason = ai_response.get("invalidation_reason", "No reliable digital footprint found.")
                        reasoning_text = f"🚨 **CRITICAL WARNING:** {inval_reason} <br><br><b>Notes:</b> {raw_reasoning}"
                        ai_response["deal_quality"], ai_response["confidence"], ai_response["effort_level"] = "LOW", "HIGH", "LOW"
                        ai_response["next_action"] = "Do not engage. Flag as invalid/non-commercial lead."
                        ai_response["closing_strategy"] = ["Reject lead", "Do not invest sales effort", "Purge from CRM"]
                        discount["level"], discount["percentage"], discount["guidance"] = "None", 0, "No discounts applicable."
                        status_ui = f"""
                        <div style='background:#fee2e2; border:2px solid #ef4444; color:#b91c1c; padding:12px 16px; border-radius:10px; font-weight:800; font-size:1rem; display:flex; justify-content:space-between; align-items:center;'>
                            <span>🔴 LEAD SCORE: LOW</span>
                            <span style='background:#b91c1c; color:white; padding:4px 12px; border-radius:20px; font-size:0.85rem;'>Conversion Probability: {conversion_probability}</span>
                        </div>"""
                    else:
                        reasoning_text = raw_reasoning
                        deal_quality = str(ai_response.get("deal_quality", "")).upper()
                        
                        if not is_fake_company and is_valid_b2b:
                            if deal_quality == "HIGH": lead_score = "High"
                            elif deal_quality == "MEDIUM": lead_score = "Medium"
                            else: lead_score = "Low"

                        if lead_score == "High":
                            status_ui = """
                            <div style='background:#dcfce7; border:2px solid #22c55e; color:#15803d; padding:12px 16px; border-radius:10px; font-weight:800; font-size:1rem; display:flex; justify-content:space-between; align-items:center;'>
                                <span>🟢 LEAD SCORE: HIGH</span>
                                <span style='background:#15803d; color:white; padding:4px 12px; border-radius:20px; font-size:0.85rem;'>Conversion Probability: {conv_prob}</span>
                            </div>""".format(conv_prob=conversion_probability)
                        elif lead_score == "Medium":
                            status_ui = """
                            <div style='background:#fef9c3; border:2px solid #eab308; color:#a16207; padding:12px 16px; border-radius:10px; font-weight:800; font-size:1rem; display:flex; justify-content:space-between; align-items:center;'>
                                <span>🟡 LEAD SCORE: MEDIUM</span>
                                <span style='background:#a16207; color:white; padding:4px 12px; border-radius:20px; font-size:0.85rem;'>Conversion Probability: {conv_prob}</span>
                            </div>""".format(conv_prob=conversion_probability)
                        else:
                            status_ui = """
                            <div style='background:#fee2e2; border:2px solid #ef4444; color:#b91c1c; padding:12px 16px; border-radius:10px; font-weight:800; font-size:1rem; display:flex; justify-content:space-between; align-items:center;'>
                                <span>🔴 LEAD SCORE: LOW</span>
                                <span style='background:#b91c1c; color:white; padding:4px 12px; border-radius:20px; font-size:0.85rem;'>Conversion Probability: {conv_prob}</span>
                            </div>""".format(conv_prob=conversion_probability)

                    try:
                        conn = get_db_connection()
                        with conn.cursor() as cursor:
                            cursor.execute("""INSERT INTO leads (sales_person, company_name, website, service_type, full_input_json, full_output_json, decision, suggested_price) VALUES ('Sales Team', %s, %s, %s, %s, %s, %s, %s)""", (comp_clean, web_clean, service_type, input_json_str, output_json_str, f"{lead_score} ({conversion_probability})", display_price))
                        conn.commit()
                        conn.close()
                    except Exception:
                        pass 
                        
                    if prev_enq_html:
                        st.markdown(prev_enq_html, unsafe_allow_html=True)
                    
                    hr1, hr2 = st.columns([1.5, 1])
                    with hr1: st.markdown(status_ui, unsafe_allow_html=True)
                    with hr2: 
                        st.markdown(f"""
                        <div style="background:#1e293b; border-radius:8px; padding:6px 15px; display:flex; justify-content:space-between; align-items:center; color:white;">
                            <div style="font-size:0.75rem; font-weight:700; color:#94a3b8; text-transform:uppercase;">Target Quote</div>
                            <div style="font-size:1.6rem; font-weight:800; letter-spacing:1px;">{display_price}</div>
                        </div>
                        """, unsafe_allow_html=True)

                    st.markdown("<div style='margin-top:8px;'></div>", unsafe_allow_html=True)

                    dash_t1, dash_t2, dash_t3 = st.tabs(["📊 Executive Summary", "🎯 Execution Plan", "⚠️ Risk & Deep Intel"])

                    with dash_t1: 
                        def render_metric_bar(label, value, is_inverted=False):
                            val_upper = str(value).upper()
                            if val_upper == "HIGH": active_blocks, color = 3, "#dc2626" if is_inverted else "#16a34a"
                            elif val_upper == "MEDIUM": active_blocks, color = 2, "#eab308"
                            elif val_upper == "LOW": active_blocks, color = 1, "#16a34a" if is_inverted else "#dc2626"
                            else: active_blocks, color = 0, "#cbd5e1"
                            blocks_html = "".join([f'<div style="flex:1;height:4px;background:{color if i<=active_blocks else "#e2e8f0"};border-radius:3px;"></div>' for i in range(1,4)])
                            return f"""<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:7px;padding:9px 12px;margin-bottom:8px;"><p style="margin:0 0 4px 0;color:#64748b;font-size:0.62rem;font-weight:800;text-transform:uppercase;letter-spacing:0.5px;">{label}</p><div style="display:flex;gap:3px;margin-bottom:3px;">{blocks_html}</div><p style="margin:0;color:#0f172a;font-size:0.9rem;font-weight:800;">{val_upper}</p></div>"""

                        left_col, right_col = st.columns([1, 1])
                        with left_col:
                            st.markdown(f"""
                            <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:12px;margin-bottom:8px;">
                                <p style="margin:0 0 4px 0;font-size:0.62rem;color:#64748b;font-weight:800;text-transform:uppercase;">🧠 Reasoning</p>
                                <p style="margin:0;font-size:0.82rem;color:#0f172a;line-height:1.45;">{reasoning_text}</p>
                            </div>
                            """, unsafe_allow_html=True)
                            st.markdown(render_metric_bar("Deal Quality", ai_response.get("deal_quality", "N/A")), unsafe_allow_html=True)
                            st.markdown(render_metric_bar("Confidence", ai_response.get("confidence", "N/A")), unsafe_allow_html=True)
                            st.markdown(render_metric_bar("Effort Level", ai_response.get("effort_level", "N/A"), is_inverted=True), unsafe_allow_html=True)

                        with right_col:
                            if org_intel:
                                def oi(key, label, icon):
                                    val = org_intel.get(key, "Unknown")
                                    if isinstance(val, bool): val = "Yes" if val else "No"
                                    if val is None: val = "Unknown"
                                    val_color = "#0f172a"
                                    if str(val).lower() in ["unknown", "none"]: val_color = "#94a3b8"
                                    return f"<div style='display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid #f1f5f9;'><span style='color:#64748b;font-size:0.72rem;font-weight:600;'>{icon} {label}</span><span style='color:{val_color};font-size:0.72rem;font-weight:700;max-width:55%;text-align:right;'>{val}</span></div>"
                                
                                oi_rows = (
                                    oi("is_startup", "Startup?", "🚀") +
                                    oi("financial_status", "Financial Status", "💰") +
                                    oi("deduced_company_tier", "Size Tier", "🏢") +
                                    oi("estimated_headcount", "Est. Headcount", "👥") +
                                    oi("office_locations_count", "Offices", "📍") +
                                    oi("years_in_business", "Years in Business", "📅") +
                                    oi("leadership_experienced", "Exp. Leadership?", "🎯") +
                                    oi("customer_type", "Customer Type", "🤝")
                                )
                                st.markdown(f"""
                                <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:12px;">
                                    <p style="margin:0 0 8px 0;font-size:0.62rem;font-weight:800;color:#64748b;text-transform:uppercase;">🔬 Organisation Intelligence</p>
                                    {oi_rows}
                                </div>
                                """, unsafe_allow_html=True)

                    with dash_t2: 
                        closing = ai_response.get("closing_strategy", [])
                        cl_html = "".join([f"<li style='margin-bottom:8px;'>{b}</li>" for b in closing]) if isinstance(closing, list) else f"<li>{closing}</li>"
                        
                        raw_guidance = str(discount.get('guidance', '')).strip()
                        if raw_guidance.lower() in ['none', 'n/a', 'null', '']:
                            guidance_ui = "<p style='margin:2px 0 0 0; font-size: 0.85rem; color: #94a3b8; font-style:italic;'>No discount authorized for this deal profile.</p>"
                        else:
                            guidance_ui = f"<p style='margin:2px 0 0 0; font-size: 0.85rem; color: #475569; font-style:italic;'>\"{raw_guidance}\"</p>"
                            
                        st.markdown(f"""
                        <div class="glass-card" style="margin-bottom: 15px; background:#eff6ff; border-color:#bfdbfe;">
                            <p style="margin: 0 0 5px 0; font-size: 0.75rem; font-weight: 800; color:#1e40af; text-transform: uppercase;">▶️ Immediate Next Action</p>
                            <p style="margin:0; font-size: 1rem; font-weight: 700; color: #1e3a8a;">{ai_response.get('next_action', 'N/A')}</p>
                        </div>
                        <div class="glass-card" style="margin-bottom: 15px;">
                            <p style="margin: 0 0 10px 0; font-size: 0.75rem; font-weight: 800; color:#64748b; text-transform: uppercase;">💰 Authorized Discounting</p>
                            <p style="margin:0; font-size: 0.95rem; color: #0f172a;"><b>Level:</b> {discount.get('level', 'None')} ({discount.get('percentage', 0)}%)</p>
                            {guidance_ui}
                        </div>
                        <div class="glass-card">
                            <p style="margin: 0 0 10px 0; font-size: 0.75rem; font-weight: 800; color:#64748b; text-transform: uppercase;">🗣️ Closing Narrative</p>
                            <ul style="margin:0; padding-left:20px; font-size:0.95rem; color:#0f172a;">{cl_html}</ul>
                        </div>
                        """, unsafe_allow_html=True)
                       
                    with dash_t3: 
                        flags = ai_response.get("red_flags", [])
                        if not isinstance(flags, list):
                            flags = []
                        if is_fake_company:
                            flags = ["Zero digital footprint - verify company existence."] + flags
                        if isinstance(flags, list) and flags and str(flags[0]).lower() != "none":
                            flags_html = "".join([f"<div style='background-color:#fee2e2; color:#991b1b; padding:8px 12px; border-radius:6px; font-size:0.85rem; margin-bottom:8px; border:1px solid #f87171; font-weight:600;'>🚨 {f}</div>" for f in flags])
                        else:
                            flags_html = "<p style='font-size:0.9rem; color:#64748b;'>No major risks detected by AI.</p>"
                        
                        if is_fake_company:
                            ins_list = "<p style='font-size:0.9rem; color:#64748b;'>No recent digital footprint found. AI insights disabled to prevent hallucinations.</p>"
                        else:
                            insights = ai_response.get("background_insights", [])
                            if isinstance(insights, list) and insights and str(insights[0]).lower() != "none":
                                ins_html = "".join([f"<li style='margin-bottom:8px;'>{i}</li>" for i in insights])
                                ins_list = f"<ul style='font-size:0.9rem; padding-left:20px; margin:0; color:#0f172a;'>{ins_html}</ul>"
                            else:
                                ins_list = "<p style='font-size:0.9rem; color:#64748b;'>No recent digital footprint found.</p>"
                        st.markdown(f"""
                        <div class="glass-card" style="margin-bottom: 15px;">
                            <p style="margin: 0 0 10px 0; color: #dc2626; font-weight: 800; font-size: 0.85rem; text-transform: uppercase;">⚠️ Identified Deal Risks</p>
                            {flags_html}
                        </div>
                        <div class="glass-card">
                            <p style="margin: 0 0 10px 0; color: #2563eb; font-weight: 800; font-size: 0.85rem; text-transform: uppercase;">🌐 Web Scraped Insights</p>
                            {ins_list}
                        </div>
                        """, unsafe_allow_html=True)
        else:
            st.info("Select Target Profile and Service Scope on the left to begin intelligence analysis.")

with tab_hist:
    st.markdown("###### Intelligence History Archive")
    df_history = fetch_recent_history()

    if not df_history.empty:
        def highlight_decision(val):
            v = str(val).upper()
            if 'HIGH' in v: return 'color: #16a34a; font-weight:bold;'
            elif 'MEDIUM' in v: return 'color: #ca8a04; font-weight:bold;'
            else: return 'color: #dc2626; font-weight:bold;'
        def highlight_signal(val):
            return 'color: #16a34a;' if val == 'Available' else 'color: #64748b;'

        styled_df = df_history.style.map(highlight_decision, subset=['Decision']).map(highlight_signal, subset=['Signal'])
        st.dataframe(styled_df, height=700, hide_index=True, width="stretch")
    else:
        st.caption("No history found. Data will appear here once saved.")